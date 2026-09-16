"""Shared fixtures and seed helpers for the backend suite.

Pytest loads this automatically, so tests use the fixtures by naming them as
arguments. Keeping database setup here means a change to how a test database
is built happens once rather than in every test.
"""

import sqlite3
from datetime import datetime, timezone

import app.main as main_module
import pytest
from app.database import PRIMARY_PROPERTY_ID, SCHEMA
from app.main import app
from fastapi.testclient import TestClient

DEFAULT_MEMBER_ID = "member-1"


def seed_primary_property(conn):
    """Give a test database the property that stands default to.

    /api/map-state refuses an unknown slug, so any test reading it needs the
    property row to exist even when the test is not about properties.
    """
    conn.execute(
        """
        INSERT OR IGNORE INTO properties (
            id, slug, name, center_lat, center_lng, default_zoom
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (PRIMARY_PROPERTY_ID, PRIMARY_PROPERTY_ID, "Black Creek", 35.0, -78.0, 15),
    )
    conn.commit()


def seed_member(
    conn,
    member_id=DEFAULT_MEMBER_ID,
    email=None,
    first_name="Mike",
    last_name="Doe",
    is_admin=0,
    password_hash="not-a-real-hash",
):
    """Insert a member. hunts.member_id references this table."""
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
    """Insert one hunt row and return its id.

    Seeds the member first: foreign keys are enforced in these tests, so a
    hunt cannot reference a member that does not exist.
    """
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


def open_connection(target):
    """Open a test connection with the production SQLite settings.

    Foreign keys are enforced because get_connection enforces them in the real
    application: without this, a test can insert a hunt referencing a member
    that does not exist and pass where production would fail.
    """
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


@pytest.fixture
def conn(tmp_path):
    """A fresh file-backed database for one test.

    Backed by a file, not memory, so the test and the application can hold
    separate connections to the same data: routes close the connection they
    were handed, which would otherwise close the test's own.
    """
    connection = build_connection(tmp_path / "test.db")
    yield connection
    connection.close()


@pytest.fixture
def client(conn, monkeypatch):
    """A TestClient whose requests each open their own connection.

    The acting member is seeded because foreign keys are enforced: check-in
    writes hunts.member_id, which must reference a real row. Slice 4 replaces
    this with an authenticated session for the same member.
    """
    seed_member(conn, main_module.CURRENT_MEMBER_ID)
    db_path = conn.execute("PRAGMA database_list").fetchone()["file"]

    monkeypatch.setattr(
        main_module,
        "get_connection",
        lambda: open_connection(db_path),
    )

    return TestClient(app)


@pytest.fixture
def file_db(tmp_path, monkeypatch):
    """A database addressed by path, with no open connection held by the test.

    Differs from `conn` in what it hands back: a path rather than a live
    connection. Tests that assert on state after a request use this, opening
    their own connection at each point so nothing observes stale data across
    SQLite's write lock: transaction rollback, and two requests racing for
    one seat.

    Yields the path; seed with `build_connection(path)`, and read the final
    state with `inspect_file_db(path)`.
    """
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


@pytest.fixture
def frozen_now(monkeypatch):
    """Pin the clock so session-boundary and overdue behavior is deterministic.

    Returns the instant the application will see as "now".
    """
    now = datetime(2026, 11, 10, 17, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(main_module, "utc_now", lambda: now)
    return now
