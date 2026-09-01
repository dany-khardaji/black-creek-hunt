import sqlite3
from datetime import datetime, timezone

import pytest
from app.database import SCHEMA
from app.sessions import is_stand_occupied, session_boundary


# Reusable fake database, shared across tests that ask for it
# note: any test function that takes "conn" as a parameter automatically
# gets a brand new one of these, built fresh, no copy-pasting the setup
@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")  # fresh in-memory db
    connection.executescript(SCHEMA)  # create the real tables
    yield connection  # hand it to the test
    connection.close()  # cleanup after test finishes


# Boundary should be TODAY's 3am if current time is after 3am
def test_boundary_after_3am_is_today():
    now = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
    expected = datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc)
    assert session_boundary(now) == expected


# Boundary should be YESTERDAY's 3am if current time is before 3am
def test_boundary_before_3am_is_yesterday():
    now = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)
    expected = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)
    assert session_boundary(now) == expected


# Stand with no hunts at all should not be occupied
def test_empty_stand_is_not_occupied(conn):
    now = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
    assert is_stand_occupied(conn, "test-stand-1", now) is False


# Stand with an open (not checked out) hunt should be occupied
def test_open_session_makes_stand_occupied(conn):
    now = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)

    # insert a hunt with no checked_out_at, meaning it's still active
    conn.execute(
        "INSERT INTO hunts (stand_id, member_id, checked_in_at) VALUES (?, ?, ?)",
        (
            "test-stand-1",
            "member-1",
            "2026-11-10T12:00:00+00:00",
        ),
    )
    assert is_stand_occupied(conn, "test-stand-1", now) is True


# A hunt from before today's boundary should not count, even if never checked out
def test_stale_session_does_not_occupy(conn):
    now = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)

    # this check-in happened BEFORE the reset boundary, so it's stale
    conn.execute(
        "INSERT INTO hunts (stand_id, member_id, checked_in_at) VALUES (?, ?, ?)",
        (
            "test-stand-1",
            "member-1",
            "2026-11-09T22:00:00+00:00",
        ),
    )
    assert is_stand_occupied(conn, "test-stand-1", now) is False


# A hunt that was checked out should not count as active
def test_checked_out_session_does_not_occupy(conn):
    now = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)

    # this hunt has both checked_in_at AND checked_out_at set
    conn.execute(
        "INSERT INTO hunts (stand_id, member_id, checked_in_at, checked_out_at) VALUES (?, ?, ?, ?)",
        (
            "test-stand-1",
            "member-1",
            "2026-11-10T12:00:00+00:00",
            "2026-11-10T15:00:00+00:00",
        ),
    )
    assert is_stand_occupied(conn, "test-stand-1", now) is False


# A hunt checked in before the boundary should still count as active
def test_session_before_boundary_is_still_active(conn):
    now = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)

    conn.execute(
        "INSERT INTO hunts (stand_id, member_id, checked_in_at) VALUES (?, ?, ?)",
        (
            "test-stand-1",
            "member-1",
            "2026-11-10T02:00:00+00:00",
        ),
    )
    assert is_stand_occupied(conn, "test-stand-1", now) is True


# Boundary should stay correct across the DST switch
def test_boundary_handles_dst_fall_back():
    # 9am Eastern, Nov 2 (after DST ends, EST = UTC-5)
    now = datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc)

    # 3am Eastern, same day, converted to UTC
    expected = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)

    assert session_boundary(now) == expected
