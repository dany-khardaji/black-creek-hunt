from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

CLUB_TZ = ZoneInfo("America/New_York")  # Clubs timezone
RESET_HOUR = 3  # Sessions reset at 3am local time, not midnight
OVERDUE_AFTER = timedelta(hours=8)


# The start of the current hunting day, as a UTC time.
def session_boundary(now_utc):
    eastern_time = now_utc.astimezone(CLUB_TZ)

    todays_3am = eastern_time.replace(
        hour=RESET_HOUR, minute=0, second=0, microsecond=0
    )

    # Before 3am still counts as the previous day's hunt.
    if eastern_time < todays_3am:
        todays_3am = todays_3am - timedelta(days=1)

    return todays_3am.astimezone(timezone.utc)


# Counts people still out, ignoring anyone checked out or left over from an
# earlier day.
def active_hunt_count(conn, stand_id, now_utc):
    boundary = session_boundary(now_utc)

    row = conn.execute(
        """
        SELECT COUNT(*) FROM hunts
        WHERE stand_id = ?
        AND checked_out_at IS NULL
        AND checked_in_at > ?
        """,
        (stand_id, boundary.isoformat()),
    ).fetchone()

    return row[0]


def is_stand_occupied(conn, stand_id, now_utc):
    return active_hunt_count(conn, stand_id, now_utc) > 0


# A hunt still open after eight hours is flagged so someone can check on them.
def is_hunt_overdue(checked_in_at, now_utc):
    if isinstance(checked_in_at, str):
        checked_in_at = datetime.fromisoformat(checked_in_at)

    return now_utc - checked_in_at >= OVERDUE_AFTER
