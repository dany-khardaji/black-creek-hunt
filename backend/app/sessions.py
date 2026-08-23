from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

CLUB_TZ = ZoneInfo("America/New_York")
RESET_HOUR = 3


def session_boundary(now_utc):
    eastern_time = now_utc.astimezone(CLUB_TZ)
    todays_3am = eastern_time.replace(
        hour=RESET_HOUR, minute=0, second=0, microsecond=0
    )

    if eastern_time < todays_3am:
        todays_3am = todays_3am - timedelta(days=1)

    todays_3am = todays_3am.astimezone(timezone.utc)

    return todays_3am
