from datetime import datetime, timezone

from app.sessions import session_boundary


def test_boundary_before_3am_is_yesterday():
    now = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)
    assert session_boundary(now) == datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)
