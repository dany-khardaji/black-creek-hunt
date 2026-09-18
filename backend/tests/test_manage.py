import sqlite3

import app.database as database_module
import manage
import pytest
from app import auth


# manage.py writes to the real blackcreek.db, so every test here redirects it to
# a throwaway file first.
@pytest.fixture
def cli_db(tmp_path, monkeypatch):
    db_path = tmp_path / "manage.db"
    monkeypatch.setattr(database_module, "DB_PATH", db_path)
    monkeypatch.setattr(manage.getpass, "getpass", lambda *args: "correct-horse")
    return db_path


def read_members(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM members").fetchall()
    conn.close()
    return rows


def test_create_member_inserts_a_usable_account(cli_db):
    manage.main(
        ["create-member", "--email", "New@Example.com", "--first-name", "Sam",
         "--last-name", "Reed"]
    )

    rows = read_members(cli_db)

    assert len(rows) == 1
    # Stored lowercased, so capitals typed at sign-in still match.
    assert rows[0]["email"] == "new@example.com"
    assert rows[0]["is_admin"] == 0
    assert auth.verify_password("correct-horse", rows[0]["password_hash"])


def test_admin_flag_grants_admin_rights(cli_db):
    manage.main(
        ["create-member", "--email", "boss@example.com", "--first-name", "Ada",
         "--last-name", "Lane", "--admin"]
    )

    assert read_members(cli_db)[0]["is_admin"] == 1


# member-2 style ids belong to the test fixtures; a real account must not
# collide with them.
def test_created_ids_are_random(cli_db):
    for email in ["one@example.com", "two@example.com"]:
        manage.main(
            ["create-member", "--email", email, "--first-name", "A",
             "--last-name", "B"]
        )

    ids = {row["id"] for row in read_members(cli_db)}

    assert len(ids) == 2
    assert not any(member_id.startswith("member-") for member_id in ids)


def test_duplicate_email_exits_without_a_traceback(cli_db, capsys):
    base = ["create-member", "--email", "dupe@example.com", "--first-name", "A",
            "--last-name", "B"]
    manage.main(base)

    with pytest.raises(SystemExit) as exit_info:
        manage.main(base + [])

    assert exit_info.value.code == 1
    assert "already exists" in capsys.readouterr().err
    assert len(read_members(cli_db)) == 1


def test_mismatched_passwords_create_nothing(cli_db, monkeypatch):
    typed = iter(["first-try", "second-try"])
    monkeypatch.setattr(manage.getpass, "getpass", lambda *args: next(typed))

    with pytest.raises(SystemExit) as exit_info:
        manage.main(
            ["create-member", "--email", "typo@example.com", "--first-name", "A",
             "--last-name", "B"]
        )

    assert exit_info.value.code == 1
    assert read_members(cli_db) == []


def test_reset_password_replaces_the_hash(cli_db, monkeypatch):
    manage.main(
        ["create-member", "--email", "reset@example.com", "--first-name", "A",
         "--last-name", "B"]
    )
    old_hash = read_members(cli_db)[0]["password_hash"]

    monkeypatch.setattr(manage.getpass, "getpass", lambda *args: "brand-new-pass")
    manage.main(["reset-password", "--email", "Reset@Example.com"])

    row = read_members(cli_db)[0]
    assert row["password_hash"] != old_hash
    assert auth.verify_password("brand-new-pass", row["password_hash"])


# A member may hold both a password and a Google login; resetting one must not
# remove the other.
def test_reset_password_leaves_google_sub_alone(cli_db):
    manage.main(
        ["create-member", "--email", "both@example.com", "--first-name", "A",
         "--last-name", "B"]
    )
    conn = sqlite3.connect(cli_db)
    conn.execute("UPDATE members SET google_sub = ?", ("google-123",))
    conn.commit()
    conn.close()

    manage.main(["reset-password", "--email", "both@example.com"])

    assert read_members(cli_db)[0]["google_sub"] == "google-123"


def test_reset_password_for_an_unknown_email_exits(cli_db, capsys):
    with pytest.raises(SystemExit) as exit_info:
        manage.main(["reset-password", "--email", "ghost@example.com"])

    assert exit_info.value.code == 1
    assert "No member found" in capsys.readouterr().err
