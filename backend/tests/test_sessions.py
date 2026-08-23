from datetime import datetime, timezone

from app.sessions import session_boundary


def test_boundary_after_3am_is_today():
    now = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
    expected = datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc)
    assert session_boundary(now) == expected


def test_boundary_before_3am_is_yesterday():
    now = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)
    expected = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)
    assert session_boundary(now) == expected
