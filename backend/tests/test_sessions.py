from datetime import datetime, timezone

from app.sessions import is_stand_occupied, session_boundary

# The "conn" fixture these tests take comes from conftest.py, shared with
# test_main.py: an in-memory database carrying the production schema.
from conftest import seed_hunt, seed_stand


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

    # a hunt with no checked_out_at is still active
    seed_stand(conn, "test-stand-1")
    seed_hunt(conn, "test-stand-1", checked_in_at="2026-11-10T12:00:00+00:00")

    assert is_stand_occupied(conn, "test-stand-1", now) is True


# A hunt from before today's boundary should not count, even if never checked out
def test_stale_session_does_not_occupy(conn):
    now = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)

    # this check-in happened BEFORE the reset boundary, so it's stale
    seed_stand(conn, "test-stand-1")
    seed_hunt(conn, "test-stand-1", checked_in_at="2026-11-09T22:00:00+00:00")

    assert is_stand_occupied(conn, "test-stand-1", now) is False


# A hunt that was checked out should not count as active
def test_checked_out_session_does_not_occupy(conn):
    now = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)

    # this hunt has both checked_in_at AND checked_out_at set
    seed_stand(conn, "test-stand-1")
    seed_hunt(
        conn,
        "test-stand-1",
        checked_in_at="2026-11-10T12:00:00+00:00",
        checked_out_at="2026-11-10T15:00:00+00:00",
    )

    assert is_stand_occupied(conn, "test-stand-1", now) is False


# A hunt checked in before the boundary should still count as active
def test_session_before_boundary_is_still_active(conn):
    now = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)

    seed_stand(conn, "test-stand-1")
    seed_hunt(conn, "test-stand-1", checked_in_at="2026-11-10T02:00:00+00:00")

    assert is_stand_occupied(conn, "test-stand-1", now) is True


# Boundary should stay correct across the DST switch
def test_boundary_handles_dst_fall_back():
    # 9am Eastern, Nov 2 (after DST ends, EST = UTC-5)
    now = datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc)

    # 3am Eastern, same day, converted to UTC
    expected = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)

    assert session_boundary(now) == expected
