# Shared test setup. Pytest loads this file on its own, so a test uses anything
# here just by naming it as an argument.

import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.engine import make_url

# Tests delete rows between runs, so they must never touch the app's database.
# Only the test url is read from .env, and the app's own urls are read solely
# to refuse if the two point at the same place.
_ENV_FILE = Path(__file__).parent.parent.parent / ".env"
_env = {}
if _ENV_FILE.is_file():
    for _line in _ENV_FILE.read_text().splitlines():
        _name, _, _value = _line.strip().partition("=")
        _env[_name.strip()] = _value.strip().strip("\"'")

_test_url = os.environ.get("TEST_DATABASE_URL") or _env.get("TEST_DATABASE_URL")
if not _test_url:
    raise RuntimeError(
        "TEST_DATABASE_URL is not set. Tests need their own database: create a "
        "Neon branch and put its connection string in .env."
    )


# Neon gives one database separate direct and pooled hostnames. Normalize both
# forms before comparing, or the safety check could mistake aliases for two
# databases and let test cleanup delete app data.
def _database_identity(url):
    parsed = make_url(url)
    host = (parsed.host or "").lower().rstrip(".")
    host = host.replace("-pooler.", ".", 1)
    return parsed.get_backend_name(), host, parsed.port or 5432, parsed.database


for _name in ("DATABASE_URL", "DATABASE_URL_POOLED"):
    _app_url = os.environ.get(_name) or _env.get(_name)
    if _app_url and _database_identity(_app_url) == _database_identity(_test_url):
        raise RuntimeError(
            f"TEST_DATABASE_URL points at the same database as {_name}. "
            "Tests delete rows and would wipe app data."
        )

# The app reads DATABASE_URL_POOLED first, so both are pointed at the test
# database before app.database is imported.
os.environ["DATABASE_URL"] = _test_url
os.environ["DATABASE_URL_POOLED"] = _test_url

# Set before app.config is imported below: tests must read every other setting
# from the environment alone, never from a developer's local .env file.
os.environ.setdefault("SKIP_ENV_FILE", "1")

import app.main as main_module
import pytest
from app import auth, config
from app.database import PRIMARY_PROPERTY_ID, get_engine, init_db, metadata
from app.main import app
from fastapi.testclient import TestClient
from sqlalchemy import text

DEFAULT_MEMBER_ID = "member-1"
ADMIN_MEMBER_ID = "member-admin"


# Every seed helper commits. Inside the test transaction that only closes a
# savepoint, so a route that fails and rolls back cannot undo the test's setup.
# Any test that reads the map needs this property row to exist.
def seed_primary_property(conn):
    conn.execute(
        text(
            """
            INSERT INTO properties (
                id, slug, name, center_lat, center_lng, default_zoom
            ) VALUES (:id, :slug, :name, :lat, :lng, :zoom)
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {
            "id": PRIMARY_PROPERTY_ID,
            "slug": PRIMARY_PROPERTY_ID,
            "name": "Black Creek",
            "lat": 35.0,
            "lng": -78.0,
            "zoom": 15,
        },
    )
    conn.commit()


# Pass password= only when a test actually signs in. Hashing is slow by design,
# and almost every test seeds a member without needing a real one.
def seed_member(
    conn,
    member_id=DEFAULT_MEMBER_ID,
    email=None,
    first_name="Mike",
    last_name="Doe",
    is_admin=False,
    password_hash="not-a-real-hash",
    password=None,
):
    if password is not None:
        password_hash = auth.hash_password(password)

    conn.execute(
        text(
            """
            INSERT INTO members (
                id, email, password_hash, is_admin, first_name, last_name,
                created_at
            ) VALUES (
                :id, :email, :password_hash, :is_admin, :first_name, :last_name,
                :created_at
            )
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {
            "id": member_id,
            "email": email or f"{member_id}@example.com",
            "password_hash": password_hash,
            "is_admin": bool(is_admin),
            "first_name": first_name,
            "last_name": last_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    conn.commit()
    return member_id


def seed_stand(
    conn,
    stand_id,
    name=None,
    type="ladder",
    lat=35.0,
    lng=-78.0,
    capacity=1,
    is_retired=False,
    property_id=PRIMARY_PROPERTY_ID,
):
    """Insert one stand. Defaults cover the common open, single-seat case."""
    conn.execute(
        text(
            """
            INSERT INTO stands (
                id, name, type, lat, lng, capacity, is_retired, property_id
            ) VALUES (
                :id, :name, :type, :lat, :lng, :capacity, :is_retired,
                :property_id
            )
            """
        ),
        {
            "id": stand_id,
            "name": name or stand_id,
            "type": type,
            "lat": lat,
            "lng": lng,
            "capacity": capacity,
            "is_retired": bool(is_retired),
            "property_id": property_id,
        },
    )
    conn.commit()
    return stand_id


def seed_hunt(
    conn,
    stand_id,
    member_id=DEFAULT_MEMBER_ID,
    checked_in_at=None,
    checked_out_at=None,
    host_hunt_id=None,
    guest_name=None,
    guest_phone=None,
):
    # The member is added first, because a hunt has to belong to someone real.
    seed_member(conn, member_id)
    # postgres has no lastrowid, so the new id is read back with RETURNING
    hunt_id = conn.execute(
        text(
            """
            INSERT INTO hunts (
                stand_id, member_id, host_hunt_id, checked_in_at,
                checked_out_at, guest_name, guest_phone
            ) VALUES (
                :stand_id, :member_id, :host_hunt_id, :checked_in_at,
                :checked_out_at, :guest_name, :guest_phone
            )
            RETURNING id
            """
        ),
        {
            "stand_id": stand_id,
            "member_id": member_id,
            "host_hunt_id": host_hunt_id,
            "checked_in_at": checked_in_at
            or datetime.now(timezone.utc).isoformat(),
            "checked_out_at": checked_out_at,
            "guest_name": guest_name,
            "guest_phone": guest_phone,
        },
    ).scalar()
    conn.commit()
    return hunt_id


# Tables are made once for the whole run rather than per test, because creating
# them over the network would dominate the suite.
@pytest.fixture(scope="session", autouse=True)
def database_schema():
    init_db()
    yield


# The routes open a connection, commit, and close it, all of which would end the
# test's transaction and let its rows reach the real database. This stands in
# for a connection: commit only closes the current savepoint and opens the next,
# and close does nothing, so the outer rollback still undoes every write.
# Routes may only call execute, commit, rollback, and close on a connection;
# anything else would bypass this and reach the real database.
class SavepointConnection:
    def __init__(self, connection):
        self._connection = connection
        self._savepoint = connection.begin_nested()

    def execute(self, *args, **kwargs):
        return self._connection.execute(*args, **kwargs)

    def commit(self):
        if self._savepoint.is_active:
            self._savepoint.commit()
        self._savepoint = self._connection.begin_nested()

    def rollback(self):
        if self._savepoint.is_active:
            self._savepoint.rollback()
        self._savepoint = self._connection.begin_nested()

    # Deliberately empty: the fixture owns the real connection's lifetime.
    def close(self):
        pass


# Every test runs inside one transaction that is rolled back afterwards, so no
# test can see another's rows and nothing reaches the real database.
@pytest.fixture
def conn(database_schema):
    connection = get_engine().connect()
    transaction = connection.begin()

    shared = SavepointConnection(connection)
    seed_primary_property(shared)

    yield shared

    transaction.rollback()
    connection.close()


# Signs a client in the same way the real site does: a genuine token in the
# real cookie. Tests therefore exercise the whole chain rather than skipping it.
def authed_client(member_id=DEFAULT_MEMBER_ID):
    client = TestClient(app)
    client.cookies.set(
        config.SESSION_COOKIE_NAME, auth.create_session_token(member_id)
    )
    return client


# Routes open their own connection; this hands them the test's transaction so
# they see its rows and their writes are rolled back with it.
def _point_app_at(conn, monkeypatch):
    monkeypatch.setattr(main_module, "get_connection", lambda: conn)
    return conn


@pytest.fixture
def client(conn, monkeypatch):
    # The acting member is added first because checking in records who did it,
    # and that has to point at a real person.
    seed_member(conn, DEFAULT_MEMBER_ID)
    _point_app_at(conn, monkeypatch)

    return authed_client()


# A second signed-in member who may check out hunts that are not theirs.
@pytest.fixture
def admin_client(conn, monkeypatch):
    seed_member(conn, ADMIN_MEMBER_ID, first_name="Ada", is_admin=True)
    _point_app_at(conn, monkeypatch)

    return authed_client(ADMIN_MEMBER_ID)


# A caller who is not signed in. Still points at the test transaction, so a
# mistake in the guards shows up as a failing test rather than a real read.
@pytest.fixture
def anonymous_client(conn, monkeypatch):
    _point_app_at(conn, monkeypatch)

    return TestClient(app)


# Two requests racing for the same stand cannot share one transaction, because
# each has to see the other's committed rows. These tests therefore write for
# real and the rows are deleted afterwards.
@pytest.fixture
def committed_db(database_schema, monkeypatch):
    engine = get_engine()
    monkeypatch.setattr(main_module, "get_connection", engine.connect)

    with engine.connect() as setup:
        seed_primary_property(setup)
        setup.commit()

    yield engine

    # Order matters: hunts point at stands and members, so they go first.
    with engine.connect() as cleanup:
        for table in ("hunts", "stands", "members"):
            cleanup.execute(text(f"DELETE FROM {table}"))
        cleanup.commit()


# Holds the clock still so tests about the 3am reset and overdue hunts always
# get the same answer.
@pytest.fixture
def frozen_now(monkeypatch):
    now = datetime(2026, 11, 10, 17, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(main_module, "utc_now", lambda: now)
    return now
