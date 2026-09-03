from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

CLUB_TZ = ZoneInfo("America/New_York")  # Clubs timezone
RESET_HOUR = 3  # Sessions reset at 3am local time, not midnight
OVERDUE_AFTER = timedelta(hours=8)


# Figures out "today's 3am" boundary, in UTC, based on the current time
def session_boundary(now_utc):
    # convert the current UTC time into the club's local time
    eastern_time = now_utc.astimezone(CLUB_TZ)

    # build today's 3am, in local time
    todays_3am = eastern_time.replace(
        hour=RESET_HOUR, minute=0, second=0, microsecond=0
    )

    # if it's currently before 3am, the "active" boundary is still YESTERDAY's 3am
    if eastern_time < todays_3am:
        todays_3am = todays_3am - timedelta(days=1)

    # convert the boundary back to UTC, since that's how times are stored in the db
    todays_3am = todays_3am.astimezone(timezone.utc)

    return todays_3am


# Checks whether a stand currently has an active (not checked out, not stale) session
def is_stand_occupied(conn, stand_id, now_utc):
    boundary = session_boundary(now_utc)

    # look for any hunt on this stand that hasn't been checked out or started after the reset boundary
    row = conn.execute(
        """
        SELECT * FROM hunts
        WHERE stand_id = ?
        AND checked_out_at IS NULL
        AND checked_in_at > ?
        """,
        (stand_id, boundary.isoformat()),
    ).fetchone()

    # true if a matching row was found, false if not
    return row is not None


def is_hunt_overdue(checked_in_at, now_utc):
    """Return True once an active hunt has lasted at least eight hours."""
    if isinstance(checked_in_at, str):
        checked_in_at = datetime.fromisoformat(checked_in_at)

    return now_utc - checked_in_at >= OVERDUE_AFTER
