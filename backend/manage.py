import sys
from pathlib import Path
import argparse

import getpass
from datetime import datetime, timezone
from uuid import uuid4

from app import auth
from app.database import get_connection, init_db


sys.path.insert(0, str(Path(__file__).parent))


# Typed rather than passed as a flag, so the password never lands in shell
# history or the process list. Asked twice to catch a typo.
def prompt_password():
    first = getpass.getpass("Password: ")
    if not first:
        fail("Password cannot be empty.")
    # Login strips edge whitespace and caps passwords at 1,024 characters.
    if first != first.strip():
        fail("Password cannot start or end with whitespace.")
    if len(first) > 1024:
        fail("Password cannot exceed 1,024 characters.")
    if first != getpass.getpass("Confirm password: "):
        fail("Passwords did not match.")
    return first


# A plain message beats a database traceback for someone running this by hand.
def fail(message):
    print(message, file=sys.stderr)
    raise SystemExit(1)


def create_member(args):
    init_db()
    email = auth.normalize_email(args.email)
    conn = get_connection()

    try:
        if auth.find_member_by_email(conn, email) is not None:
            fail(f"A member with the email {email} already exists.")

        password_hash = auth.hash_password(prompt_password())

        conn.execute(
            """
            INSERT INTO members (
                id, email, password_hash, is_admin, first_name, last_name,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                # Random rather than member-2, member-3: that pattern belongs to
                # the test fixtures and would collide with them.
                uuid4().hex,
                email,
                password_hash,
                int(args.admin),
                args.first_name,
                args.last_name,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    role = "admin" if args.admin else "member"
    print(f"Created {role} {email}.")


def reset_password(args):
    init_db()
    email = auth.normalize_email(args.email)
    conn = get_connection()

    try:
        member = auth.find_member_by_email(conn, email)
        if member is None:
            fail(f"No member found with the email {email}.")

        password_hash = auth.hash_password(prompt_password())

        # google_sub is left alone on purpose: one person may sign in with both
        # a password and Google, and clearing it would break the Google half.
        conn.execute(
            "UPDATE members SET password_hash = ? WHERE id = ?",
            (password_hash, member["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    print(f"Password reset for {email}.")


def build_parser():
    parser = argparse.ArgumentParser(description="Manage Black Creek Hunt members.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    create = subcommands.add_parser("create-member", help="Add a new member.")
    create.add_argument("--email", required=True)
    create.add_argument("--first-name", required=True)
    create.add_argument("--last-name", required=True)
    create.add_argument("--admin", action="store_true", help="Grant admin rights.")
    create.set_defaults(handler=create_member)

    reset = subcommands.add_parser(
        "reset-password", help="Set a new password for a member."
    )
    reset.add_argument("--email", required=True)
    reset.set_defaults(handler=reset_password)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.handler(args)


if __name__ == "__main__":
    main()
