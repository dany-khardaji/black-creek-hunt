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


def build_connection(target=":memory:"):
    """Open a test database carrying the production schema and settings.

    Foreign keys are enforced here because get_connection enforces them in the
    real application: without this, a test can insert a hunt referencing a
    member that does not exist and pass where production would fail.
    """
    conn = sqlite3.connect(target, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA foreign_keys = ON")
    seed_primary_property(conn)
    return conn


@pytest.fixture
def conn():
    """A fresh in-memory database for one test."""
    connection = build_connection()
    yield connection
    connection.close()


@pytest.fixture
def client(conn, monkeypatch):
    """A TestClient whose routes read and write the test database.

    The acting member is seeded because foreign keys are enforced: check-in
    writes hunts.member_id, which must reference a real row. Slice 4 replaces
    this with an authenticated session for the same member.
    """
    seed_member(conn, main_module.CURRENT_MEMBER_ID)
    monkeypatch.setattr(main_module, "get_connection", lambda: conn)
    return TestClient(app)


@pytest.fixture
def file_db(tmp_path, monkeypatch):
    """A file-backed database where every request opens its own connection.

    Some behavior only appears when connections are not shared: transaction
    rollback, SQLite's write lock, and two requests racing for one seat. Those
    tests use this instead of the in-memory `conn`.

    Yields the path; open a connection with `build_connection(path)` to seed,
    and `inspect_file_db(path)` to read the final state.
    """
    db_path = tmp_path / "test.db"
    setup = build_connection(db_path)
    setup.close()

    def fake_get_connection():
        connection = sqlite3.connect(db_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    monkeypatch.setattr(main_module, "get_connection", fake_get_connection)
    seed_member(fake_get_connection(), main_module.CURRENT_MEMBER_ID)
    return db_path


def inspect_file_db(db_path):
    """Open a fresh read connection to assert on a file database's final state."""
    connection = sqlite3.connect(db_path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


@pytest.fixture
def frozen_now(monkeypatch):
    """Pin the clock so session-boundary and overdue behavior is deterministic.

    Returns the instant the application will see as "now".
    """
    now = datetime(2026, 11, 10, 17, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(main_module, "utc_now", lambda: now)
    return now
