"""Seed fake overdue hunts so the red safety banner can be looked at without
waiting eight hours for a real one.

    backend/venv/bin/python backend/fake_overdue.py add
    backend/venv/bin/python backend/fake_overdue.py add --count 6
    backend/venv/bin/python backend/fake_overdue.py clear

Writes only to TEST_DATABASE_URL, never the database the app serves, and
refuses to run if the two point at the same place. It creates its own member
and stands when the test database is empty, and clear removes everything it
made. Nothing it writes is matched by anything other than its own marker.
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

# Importing settings loads the local .env before anything reads a connection
# URL, the same way seed.py does.
from app import config
from app.database import PRIMARY_PROPERTY_ID, normalize_database_url
from app.sessions import OVERDUE_AFTER, session_boundary
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

# Written into guest_phone so these rows can be found and deleted later. A real
# guest phone never looks like this.
MARKER = "FAKE-OVERDUE"

# The member and stands this script creates for itself when the test database
# is empty, named so clear can recognise and remove them.
MARKER_MEMBER_ID = "fake-overdue-member"
MARKER_STAND_PREFIX = "fake-overdue-stand-"

FAKE_GUESTS = [
    "Jim Walker",
    "Ray Kessler",
    "Tom Bridges",
    "Hal Menders",
    "Cal Rowan",
    "Gus Tillery",
]


def utc_now():
    return datetime.now(timezone.utc)


# Neon gives one database separate direct and pooled hostnames, so both forms
# are normalized before comparing. Matches the check in tests/conftest.py.
def _database_identity(url):
    parsed = make_url(url)
    host = (parsed.host or "").lower().rstrip(".").replace("-pooler.", ".", 1)
    return parsed.get_backend_name(), host, parsed.port or 5432, parsed.database


# This script writes hunts claiming members are still out in the woods, so it
# only ever runs against the test database, never the one the app serves.
def test_database_url():
    if config.SESSION_COOKIE_SECURE:
        sys.exit(
            "SESSION_COOKIE_SECURE is on, which means this is a real "
            "deployment. Refusing to write fake hunts."
        )

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        sys.exit(
            "TEST_DATABASE_URL is not set. This script only writes to the test "
            "database, never the one the app serves."
        )

    for name in ("DATABASE_URL", "DATABASE_URL_POOLED"):
        app_url = os.environ.get(name)
        if app_url and _database_identity(app_url) == _database_identity(url):
            sys.exit(
                f"TEST_DATABASE_URL points at the same database as {name}. "
                "Refusing to write fake hunts into real data."
            )

    return url


# Its own engine, deliberately not app.database's: that one is wired to the
# database the app serves, which is exactly what this script must not touch.
def get_test_connection():
    engine = create_engine(
        normalize_database_url(test_database_url()), pool_pre_ping=True, future=True
    )
    return engine.connect()


# The test database is emptied by the suite, so this script creates whatever it
# needs rather than telling someone to go and seed it by hand. Every row it
# makes carries the marker so clear takes them away again.
def ensure_member(conn):
    row = (
        conn.execute(
            text(
                "SELECT id, first_name, last_name FROM members "
                "WHERE id = :id"
            ),
            {"id": MARKER_MEMBER_ID},
        )
        .mappings()
        .fetchone()
    )
    if row is not None:
        return row

    conn.execute(
        text(
            """
            INSERT INTO members (
                id, email, is_admin, first_name, last_name, created_at
            ) VALUES (:id, :email, false, :first, :last, :created_at)
            """
        ),
        {
            "id": MARKER_MEMBER_ID,
            "email": f"{MARKER_MEMBER_ID}@example.invalid",
            "first": "Test",
            "last": "Hunter",
            "created_at": utc_now().isoformat(),
        },
    )
    return {"id": MARKER_MEMBER_ID, "first_name": "Test", "last_name": "Hunter"}


def ensure_property(conn):
    existing = conn.execute(
        text("SELECT id FROM properties WHERE is_active ORDER BY id LIMIT 1")
    ).scalar()
    if existing is not None:
        return existing

    conn.execute(
        text(
            """
            INSERT INTO properties (
                id, slug, name, center_lat, center_lng, default_zoom, is_active
            ) VALUES (:id, :id, 'Black Creek', 35.0, -78.0, 15, true)
            """
        ),
        {"id": PRIMARY_PROPERTY_ID},
    )
    return PRIMARY_PROPERTY_ID


def ensure_stands(conn, count):
    property_id = ensure_property(conn)
    rows = (
        conn.execute(
            text(
                """
                SELECT id, name FROM stands
                WHERE NOT is_retired
                ORDER BY name
                LIMIT :count
                """
            ),
            {"count": count},
        )
        .mappings()
        .fetchall()
    )

    made = list(rows)
    for index in range(len(made), count):
        stand_id = f"{MARKER_STAND_PREFIX}{index + 1}"
        name = f"Test Stand {index + 1}"
        conn.execute(
            text(
                """
                INSERT INTO stands (
                    id, name, type, lat, lng, capacity, is_retired, property_id
                ) VALUES (
                    :id, :name, 'ladder', 35.0, -78.0, 2, false, :property_id
                )
                """
            ),
            {"id": stand_id, "name": name, "property_id": property_id},
        )
        made.append({"id": stand_id, "name": name})

    return made


def add(args):
    conn = get_test_connection()
    now = utc_now()

    try:
        member = ensure_member(conn)
        stands = ensure_stands(conn, args.count)
        boundary = session_boundary(now)

        # A hunt is only overdue while it is still inside today's session, so
        # the check-in time has to sit between the 3am boundary and the eight
        # hour mark. Too early and the app treats it as yesterday's hunt and
        # hides it instead.
        latest = now - OVERDUE_AFTER - timedelta(minutes=1)
        earliest = boundary + timedelta(minutes=1)

        if latest <= earliest:
            hours = OVERDUE_AFTER.total_seconds() / 3600
            sys.exit(
                "Too early in the day to fake an overdue hunt: nothing checked "
                f"in after today's 3am reset is {hours:.0f} hours old yet. "
                f"Try again after {(boundary + OVERDUE_AFTER).astimezone().strftime('%-I:%M %p')} "
                "local time, or lower OVERDUE_AFTER in app/sessions.py."
            )

        created = []
        for index, stand in enumerate(stands):
            # Spread the check-ins out so the banner shows a range of hours.
            checked_in_at = latest - timedelta(minutes=30 * index)
            if checked_in_at < earliest:
                checked_in_at = earliest

            # Alternate member and guest rows, because the banner shows both
            # and they are built from different columns.
            is_guest = index % 2 == 1
            conn.execute(
                text(
                    """
                    INSERT INTO hunts (
                        stand_id, member_id, checked_in_at, guest_name,
                        guest_phone
                    ) VALUES (
                        :stand_id, :member_id, :checked_in_at, :guest_name,
                        :guest_phone
                    )
                    """
                ),
                {
                    "stand_id": stand["id"],
                    "member_id": member["id"],
                    "checked_in_at": checked_in_at.isoformat(),
                    "guest_name": FAKE_GUESTS[index % len(FAKE_GUESTS)]
                    if is_guest
                    else None,
                    "guest_phone": MARKER,
                },
            )
            hours_out = (now - checked_in_at).total_seconds() / 3600
            who = (
                FAKE_GUESTS[index % len(FAKE_GUESTS)]
                if is_guest
                else f"{member['first_name']} {member['last_name'][0]}."
            )
            created.append(f"  {who} — {stand['name']} ({hours_out:.1f}h out)")

        conn.commit()
    finally:
        conn.close()

    print(f"Added {len(created)} fake overdue hunts:")
    print("\n".join(created))
    print("\nOpen the site and wait up to 30 seconds for the banner.")
    print("Remove them with: backend/venv/bin/python backend/fake_overdue.py clear")


def clear(args):
    conn = get_test_connection()

    try:
        # Matched on the marker alone, so a real hunt is never deleted.
        deleted = conn.execute(
            text("DELETE FROM hunts WHERE guest_phone = :marker"),
            {"marker": MARKER},
        ).rowcount

        # Hunts reference stands and members, so they go first. Only rows this
        # script named are removed; anything seeded elsewhere is left alone.
        conn.execute(
            text("DELETE FROM stands WHERE id LIKE :prefix"),
            {"prefix": f"{MARKER_STAND_PREFIX}%"},
        )
        conn.execute(
            text("DELETE FROM members WHERE id = :id"),
            {"id": MARKER_MEMBER_ID},
        )
        conn.commit()
    finally:
        conn.close()

    print(f"Removed {deleted} fake hunts.")


def build_parser():
    parser = argparse.ArgumentParser(
        description="Seed or remove fake overdue hunts for local testing."
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    add_command = subcommands.add_parser("add", help="Create fake overdue hunts.")
    add_command.add_argument(
        "--count",
        type=int,
        default=3,
        help="How many to create. Use a high number to test the banner's scrolling.",
    )
    add_command.set_defaults(handler=add)

    clear_command = subcommands.add_parser(
        "clear", help="Remove every hunt this script created."
    )
    clear_command.set_defaults(handler=clear)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.handler(args)


if __name__ == "__main__":
    main()
