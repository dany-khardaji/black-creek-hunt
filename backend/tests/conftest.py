# Shared test setup. Pytest loads this file on its own, so a test uses anything
# here just by naming it as an argument.

import sqlite3
from datetime import datetime, timezone

import app.main as main_module
import pytest
from app import auth
from app.database import PRIMARY_PROPERTY_ID, SCHEMA
from app.main import app
from fastapi.testclient import TestClient

DEFAULT_MEMBER_ID = "member-1"


# Any test that reads the map needs this property row to exist.
def seed_primary_property(conn):
    conn.execute(
        """
        INSERT OR IGNORE INTO properties (
            id, slug, name, center_lat, center_lng, default_zoom
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (PRIMARY_PROPERTY_ID, PRIMARY_PROPERTY_ID, "Black Creek", 35.0, -78.0, 15),
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
    is_admin=0,
    password_hash="not-a-real-hash",
    password=None,
):
    if password is not None:
        password_hash = auth.hash_password(password)

    conn.execute(
        """
        INSERT OR IGNORE INTO members (
            id, email, password_hash, is_admin, first_name, last_name, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            member_id,
            email or f"{member_id}@example.com",
            password_hash,
            is_admin,
            first_name,
            last_name,
            datetime.now(timezone.utc).isoformat(),
        ),
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
    is_retired=0,
    property_id=PRIMARY_PROPERTY_ID,
):
    """Insert one stand. Defaults cover the common open, single-seat case."""
    conn.execute(
        """
        INSERT INTO stands (
            id, name, type, lat, lng, capacity, is_retired, property_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            stand_id,
            name or stand_id,
            type,
            lat,
            lng,
            capacity,
            int(is_retired),
            property_id,
        ),
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
    cursor = conn.execute(
        """
        INSERT INTO hunts (
            stand_id, member_id, host_hunt_id, checked_in_at, checked_out_at,
            guest_name, guest_phone
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            stand_id,
            member_id,
            host_hunt_id,
            checked_in_at or datetime.now(timezone.utc).isoformat(),
            checked_out_at,
            guest_name,
            guest_phone,
        ),
    )
    conn.commit()
    return cursor.lastrowid


# Matches the real app's settings, so a test cannot pass on data the live site
# would reject.
def open_connection(target):
    connection = sqlite3.connect(target, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def build_connection(target):
    """Create a test database carrying the production schema."""
    connection = open_connection(target)
    connection.executescript(SCHEMA)
    seed_primary_property(connection)
    return connection


# A fresh database per test. Kept in a file rather than memory so the test and
# the app can both reach it without one closing the other's connection.
@pytest.fixture
def conn(tmp_path):
    connection = build_connection(tmp_path / "test.db")
    yield connection
    connection.close()


@pytest.fixture
def client(conn, monkeypatch):
    # The acting member is added first because checking in records who did it,
    # and that has to point at a real person.
    seed_member(conn, main_module.CURRENT_MEMBER_ID)
    db_path = conn.execute("PRAGMA database_list").fetchone()["file"]

    monkeypatch.setattr(
        main_module,
        "get_connection",
        lambda: open_connection(db_path),
    )

    return TestClient(app)


# A caller who is not signed in. Still points at the test database, so a mistake
# in the guards shows up as a failing test rather than a read of the real one.
@pytest.fixture
def anonymous_client(conn, monkeypatch):
    db_path = conn.execute("PRAGMA database_list").fetchone()["file"]

    monkeypatch.setattr(
        main_module,
        "get_connection",
        lambda: open_connection(db_path),
    )

    return TestClient(app)


@pytest.fixture
def file_db(tmp_path, monkeypatch):
    # Hands back a file path instead of an open connection, for tests that check
    # what was saved after a request and must not read stale data.
    db_path = tmp_path / "test.db"
    setup = build_connection(db_path)
    setup.close()

    def fake_get_connection():
        return open_connection(db_path)

    monkeypatch.setattr(main_module, "get_connection", fake_get_connection)
    acting_member_connection = open_connection(db_path)
    seed_member(acting_member_connection, main_module.CURRENT_MEMBER_ID)
    acting_member_connection.close()
    return db_path


def inspect_file_db(db_path):
    """Open a fresh read connection to inspect final database state."""
    return open_connection(db_path)


# Holds the clock still so tests about the 3am reset and overdue hunts always
# get the same answer.
@pytest.fixture
def frozen_now(monkeypatch):
    now = datetime(2026, 11, 10, 17, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(main_module, "utc_now", lambda: now)
    return now
