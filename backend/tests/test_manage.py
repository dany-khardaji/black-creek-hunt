import app.main as main_module
import manage
import pytest
from app import auth
from fastapi.testclient import TestClient
from sqlalchemy import text


# manage.py opens and commits its own connections, so it cannot join a test
# transaction. Rows are written for real and removed afterwards.
@pytest.fixture
def cli_db(committed_db, monkeypatch):
    monkeypatch.setattr(manage.getpass, "getpass", lambda *args: "correct-horse")
    return committed_db


def read_members(engine):
    with engine.connect() as conn:
        return conn.execute(text("SELECT * FROM members")).mappings().fetchall()


def test_create_member_inserts_a_usable_account(cli_db):
    manage.main(
        ["create-member", "--email", "New@Example.com", "--first-name", "Sam",
         "--last-name", "Reed"]
    )

    rows = read_members(cli_db)

    assert len(rows) == 1
    # Stored lowercased, so capitals typed at sign-in still match.
    assert rows[0]["email"] == "new@example.com"
    assert rows[0]["is_admin"] is False
    assert auth.verify_password("correct-horse", rows[0]["password_hash"])


def test_admin_flag_grants_admin_rights(cli_db):
    manage.main(
        ["create-member", "--email", "boss@example.com", "--first-name", "Ada",
         "--last-name", "Lane", "--admin"]
    )

    assert read_members(cli_db)[0]["is_admin"] is True


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
    with cli_db.connect() as conn:
        conn.execute(text("UPDATE members SET google_sub = 'google-123'"))
        conn.commit()

    manage.main(["reset-password", "--email", "both@example.com"])

    assert read_members(cli_db)[0]["google_sub"] == "google-123"


def test_reset_password_for_an_unknown_email_exits(cli_db, capsys):
    with pytest.raises(SystemExit) as exit_info:
        manage.main(["reset-password", "--email", "ghost@example.com"])

    assert exit_info.value.code == 1
    assert "No member found" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["create-member", "reset-password"])
@pytest.mark.parametrize(
    "password, message",
    [
        ("", "cannot be empty"),
        ("   ", "cannot start or end with whitespace"),
        (" good-pass", "cannot start or end with whitespace"),
        ("good-pass ", "cannot start or end with whitespace"),
        ("\tgood-pass", "cannot start or end with whitespace"),
        ("good-pass\n", "cannot start or end with whitespace"),
        ("x" * 1025, "cannot exceed 1,024 characters"),
    ],
)
def test_unusable_passwords_leave_members_unchanged(
    cli_db, monkeypatch, capsys, command, password, message
):
    create_args = [
        "create-member", "--email", "test@example.com",
        "--first-name", "Sam", "--last-name", "Reed",
    ]
    if command == "reset-password":
        manage.main(create_args)
    before = [dict(row) for row in read_members(cli_db)]
    monkeypatch.setattr(manage.getpass, "getpass", lambda *args: password)

    args = create_args if command == "create-member" else [
        "reset-password", "--email", "test@example.com",
    ]
    with pytest.raises(SystemExit) as exit_info:
        manage.main(args)

    assert exit_info.value.code == 1
    assert message in capsys.readouterr().err
    assert [dict(row) for row in read_members(cli_db)] == before


@pytest.mark.parametrize("password", ["good pass", "x" * 1024])
def test_accepted_passwords_work_at_login(cli_db, monkeypatch, password):
    monkeypatch.setattr(manage.getpass, "getpass", lambda *args: password)
    manage.main([
        "create-member", "--email", "test@example.com",
        "--first-name", "Sam", "--last-name", "Reed",
    ])

    response = TestClient(main_module.app).post(
        "/api/auth/login", json={"email": "test@example.com", "password": password}
    )

    assert response.status_code == 200
