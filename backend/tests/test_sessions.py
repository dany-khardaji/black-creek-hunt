import sqlite3
from datetime import datetime, timezone

import pytest
from app.database import SCHEMA
from app.sessions import is_stand_occupied, session_boundary


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.executescript(SCHEMA)
    yield connection
    connection.close()


def test_boundary_after_3am_is_today():
    now = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
    expected = datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc)
    assert session_boundary(now) == expected


def test_boundary_before_3am_is_yesterday():
    now = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)
    expected = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)
    assert session_boundary(now) == expected


def test_empty_stand_is_not_occupied(conn):
    now = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
    assert is_stand_occupied(conn, "test-stand-1", now) is False


def test_open_session_makes_stand_occupied(conn):
    now = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)

    conn.execute(
        "INSERT INTO hunts (stand_id, member_id, checked_in_at) VALUES (?, ?, ?)",
        ("test-stand-1", "member-1", "2026-11-10T12:00:00+00:00"),
    )

    assert is_stand_occupied(conn, "test-stand-1", now) is True
