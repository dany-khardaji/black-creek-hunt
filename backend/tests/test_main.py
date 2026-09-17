import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import app.main as main_module  # The module holding get_connection, so we can swap it out
import pytest
from app import auth, config
from app.database import (  # CREATE TABLE statements, so test DBs match production
    SCHEMA,
    migrate_members_for_auth,
)
from app.main import app  # The actual FastAPI app we're testing

# Shared database fixtures and seed helpers. conftest.py is loaded by pytest
# automatically; these names are imported only where a test calls them directly.
from conftest import (
    DEFAULT_MEMBER_ID,
    authed_client,
    build_connection,
    inspect_file_db,
    seed_hunt,
    seed_member,
    seed_primary_property,
    seed_stand,
)
from fastapi.testclient import TestClient  # Lets us send fake HTTP requests to that app


# Retired stand should be rejected with 409
def test_retired_stand_rejected(conn, client):
    seed_stand(conn, "test-stand-1", is_retired=True)

    response = client.post(
        "/api/hunts", json={"stand_id": "test-stand-1", "guests": []}
    )

    assert response.status_code == 409


# Stand that was checked out should be checkable again
def test_checkin_succeeds_after_checkout(conn, client):
    seed_stand(conn, "test-stand-1")
    seed_hunt(
        conn,
        "test-stand-1",
        checked_in_at="2026-11-10T12:00:00+00:00",
        checked_out_at="2026-11-10T15:00:00+00:00",
    )

    response = client.post(
        "/api/hunts", json={"stand_id": "test-stand-1", "guests": []}
    )

    assert response.status_code == 200


# Two people check in at once, only one should win
def test_concurrent_checkin_only_one_wins(file_db):
    setup = build_connection(file_db)
    seed_stand(setup, "test-stand-1")
    setup.close()

    # shared list both threads report their result into
    results = []

    def make_request():
        response = authed_client().post(
            "/api/hunts", json={"stand_id": "test-stand-1", "guests": []}
        )
        results.append(response.status_code)

    # two hunters, sent in at the same time
    threads = [threading.Thread(target=make_request) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # exactly one wins, one loses
    assert results.count(200) == 1
    assert results.count(409) == 1


# The 422 comes from the validator, but the guard runs first and needs a real member to exist.
def test_too_many_guests_rejected(conn, client):
    response = client.post(
        "/api/hunts",
        json={
            "stand_id": "test-stand-1",
            "guests": [
                {"name": "A", "phone": "111", "stand_id": "stand-a"},
                {"name": "B", "phone": "222", "stand_id": "stand-b"},
                {"name": "C", "phone": "333", "stand_id": "stand-c"},
            ],
        },
    )
    assert response.status_code == 422


# Session from 30 days ago, never checked out. Stale and should not block a new check-in
def test_checkin_succeeds_when_only_session_is_stale(conn, client):
    seed_stand(conn, "test-stand-1")

    # 30 days ago, guaranteed stale no matter what day this test runs
    stale_time = datetime.now(timezone.utc) - timedelta(days=30)
    seed_hunt(conn, "test-stand-1", checked_in_at=stale_time.isoformat())

    response = client.post(
        "/api/hunts", json={"stand_id": "test-stand-1", "guests": []}
    )

    assert response.status_code == 200


# Checking out an open hunt should succeed and confirm the checkout time
def test_checkout_succeeds(conn, client):
    seed_stand(conn, "test-stand-1")
    hunt_id = seed_hunt(conn, "test-stand-1")

    response = client.post(f"/api/hunts/{hunt_id}/check-out")

    assert response.status_code == 200
    assert response.json()["checked_out_at"] is not None


# Checking out a hunt_id that doesn't exist should 404
def test_checkout_nonexistent_hunt_rejected(client):
    response = client.post("/api/hunts/999/check-out")

    assert response.status_code == 404


# Routes close the connection they are handed, so a shared one would leave the
# second request operating on a closed database.
def test_client_supports_multiple_database_requests(client):
    first = client.get("/api/properties")
    second = client.get("/api/properties")

    assert first.status_code == 200
    assert second.status_code == 200


# Checking out an already-closed hunt should be rejected with 409.
# Uses a file so each request reopens its own connection, as production does.
def test_checkout_twice_rejected(monkeypatch, tmp_path):
    db_path = tmp_path / "checkout_twice.db"
    setup = build_connection(db_path)
    seed_stand(setup, "test-stand-1")
    hunt_id = seed_hunt(setup, "test-stand-1")
    setup.close()

    def fake_get_connection():
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        return c

    monkeypatch.setattr(main_module, "get_connection", fake_get_connection)
    client = authed_client()

    first_response = client.post(f"/api/hunts/{hunt_id}/check-out")
    second_response = client.post(f"/api/hunts/{hunt_id}/check-out")

    assert first_response.status_code == 200
    assert second_response.status_code == 409


# Second check-in attempt on an occupied stand should not touch the original row
def test_second_checkin_does_not_overwrite_original(file_db):
    setup = build_connection(file_db)
    seed_stand(setup, "test-stand-1")
    setup.close()
    client = authed_client()

    first = client.post("/api/hunts", json={"stand_id": "test-stand-1", "guests": []})
    second = client.post("/api/hunts", json={"stand_id": "test-stand-1", "guests": []})

    assert first.status_code == 200
    assert second.status_code == 409

    # Only one row should exist, proving the original was never overwritten.
    check = inspect_file_db(file_db)
    rows = check.execute(
        "SELECT * FROM hunts WHERE stand_id = ?", ("test-stand-1",)
    ).fetchall()
    check.close()
    assert len(rows) == 1


# Host with two guests should create 3 rows total
def test_checkin_with_two_guests_creates_three_rows(file_db):
    setup = build_connection(file_db)
    for stand_id in ["test-stand-1", "test-stand-2", "test-stand-3"]:
        seed_stand(setup, stand_id)
    setup.close()

    response = authed_client().post(
        "/api/hunts",
        json={
            "stand_id": "test-stand-1",
            "guests": [
                {"name": "Guest A", "phone": "111", "stand_id": "test-stand-2"},
                {"name": "Guest B", "phone": "222", "stand_id": "test-stand-3"},
            ],
        },
    )

    assert response.status_code == 200
    check = inspect_file_db(file_db)
    rows = check.execute("SELECT * FROM hunts").fetchall()
    check.close()
    assert len(rows) == 3


# One guest's stand is already occupied: the whole submission is rejected and
# the host's row is rolled back with it.
def test_checkin_guest_stand_occupied_rejects_all(file_db):
    setup = build_connection(file_db)
    for stand_id in ["test-stand-1", "test-stand-2"]:
        seed_stand(setup, stand_id)
    # The guest's stand is already occupied by someone else.
    seed_hunt(setup, "test-stand-2", member_id="member-2")
    setup.close()

    response = authed_client().post(
        "/api/hunts",
        json={
            "stand_id": "test-stand-1",
            "guests": [
                {"name": "Guest A", "phone": "111", "stand_id": "test-stand-2"},
            ],
        },
    )

    assert response.status_code == 409

    # The host's row must never have been written either.
    check = inspect_file_db(file_db)
    rows = check.execute(
        "SELECT * FROM hunts WHERE stand_id = ?", ("test-stand-1",)
    ).fetchall()
    check.close()
    assert len(rows) == 0


# map-state should show an occupied stand as active, others as open, with correct live count
def test_map_state_reflects_active_checkin(conn, client):
    for stand_id in ["test-stand-1", "test-stand-2"]:
        seed_stand(conn, stand_id)
    # Only stand-1 has an active hunt.
    seed_hunt(conn, "test-stand-1")

    data = client.get("/api/map-state").json()

    # Find each stand by id, since order is not guaranteed.
    stand_1 = next(s for s in data["stands"] if s["id"] == "test-stand-1")
    stand_2 = next(s for s in data["stands"] if s["id"] == "test-stand-2")

    assert stand_1["status"] == "active"
    assert stand_2["status"] == "open"
    assert data["live_count"] == 1


# Checking out the host should also close any guest rows from the same check-in
def test_checkout_cascades_to_guests(file_db):
    setup = build_connection(file_db)
    for stand_id in ["test-stand-1", "test-stand-2"]:
        seed_stand(setup, stand_id)
    setup.close()
    client = authed_client()

    checkin_response = client.post(
        "/api/hunts",
        json={
            "stand_id": "test-stand-1",
            "guests": [
                {"name": "Guest A", "phone": "111", "stand_id": "test-stand-2"},
            ],
        },
    )
    assert checkin_response.status_code == 200
    host_hunt_id = checkin_response.json()["host_hunt_id"]

    checkout_response = client.post(f"/api/hunts/{host_hunt_id}/check-out")

    assert checkout_response.status_code == 200
    check = inspect_file_db(file_db)
    guest_row = check.execute(
        "SELECT * FROM hunts WHERE stand_id = ?", ("test-stand-2",)
    ).fetchone()
    check.close()
    assert guest_row["checked_out_at"] is not None
    assert guest_row["host_hunt_id"] == host_hunt_id


@pytest.mark.parametrize("field", ["name", "phone"])
def test_blank_guest_fields_rejected(conn, client, field):
    guest = {"name": "Guest A", "phone": "555-0100", "stand_id": "stand-2"}
    guest[field] = "   "

    response = client.post(
        "/api/hunts",
        json={"stand_id": "stand-1", "guests": [guest]},
    )

    assert response.status_code == 422


def test_guest_can_share_host_stand_when_capacity_allows(conn, client):
    seed_stand(conn, "stand-1", name="Double Stand", type="box", capacity=2)

    response = client.post(
        "/api/hunts",
        json={
            "stand_id": "stand-1",
            "guests": [{"name": "Guest A", "phone": "555-0100", "stand_id": "stand-1"}],
        },
    )

    assert response.status_code == 200


def test_two_guests_can_share_a_two_seat_stand(conn, client):
    seed_stand(conn, "stand-1", name="Host Stand")
    seed_stand(conn, "stand-2", name="Double Stand", type="box", capacity=2)

    response = client.post(
        "/api/hunts",
        json={
            "stand_id": "stand-1",
            "guests": [
                {"name": "Guest A", "phone": "555-0100", "stand_id": "stand-2"},
                {"name": "Guest B", "phone": "555-0101", "stand_id": "stand-2"},
            ],
        },
    )

    assert response.status_code == 200


def test_checkin_over_capacity_rejects_every_row(file_db):
    setup = build_connection(file_db)
    seed_stand(setup, "stand-1", name="Double Stand", type="box", capacity=2)
    setup.close()

    response = authed_client().post(
        "/api/hunts",
        json={
            "stand_id": "stand-1",
            "guests": [
                {"name": "Guest A", "phone": "555-0100", "stand_id": "stand-1"},
                {"name": "Guest B", "phone": "555-0101", "stand_id": "stand-1"},
            ],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "stand_capacity_exceeded",
        "message": "Double Stand has 2 seats available, but 3 were requested",
        "stand_id": "stand-1",
        "stand_name": "Double Stand",
        "capacity": 2,
        "occupied_count": 0,
        "requested_seats": 3,
        "available_seats": 2,
    }

    check = inspect_file_db(file_db)
    row_count = check.execute("SELECT COUNT(*) FROM hunts").fetchone()[0]
    check.close()
    assert row_count == 0


def test_concurrent_checkins_only_one_claims_final_seat(file_db):
    setup = build_connection(file_db)
    seed_stand(setup, "stand-1", name="Double Stand", type="box", capacity=2)
    # One of the two seats is already taken, so only one request can win.
    seed_hunt(setup, "stand-1", member_id="member-2")
    setup.close()

    results = []

    def make_request():
        response = authed_client().post(
            "/api/hunts", json={"stand_id": "stand-1", "guests": []}
        )
        results.append(response.status_code)

    threads = [threading.Thread(target=make_request) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count(200) == 1
    assert results.count(409) == 1
    check = inspect_file_db(file_db)
    active_count = check.execute(
        "SELECT COUNT(*) FROM hunts WHERE checked_out_at IS NULL"
    ).fetchone()[0]
    check.close()
    assert active_count == 2


def test_nonexistent_guest_stand_rejected(conn, client):
    seed_stand(conn, "stand-1", name="Host Stand")

    response = client.post(
        "/api/hunts",
        json={
            "stand_id": "stand-1",
            "guests": [{"name": "Guest A", "phone": "555-0100", "stand_id": "missing"}],
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "stand_not_found"
    assert response.json()["detail"]["stand_id"] == "missing"


def test_retired_guest_stand_rejected(conn, client):
    seed_stand(conn, "stand-1", name="Host Stand")
    seed_stand(conn, "stand-2", name="Retired Stand", is_retired=True)

    response = client.post(
        "/api/hunts",
        json={
            "stand_id": "stand-1",
            "guests": [{"name": "Guest A", "phone": "555-0100", "stand_id": "stand-2"}],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "stand_retired"
    assert response.json()["detail"]["stand_name"] == "Retired Stand"


def test_checkin_conflict_identifies_taken_stand(conn, client):
    seed_stand(conn, "stand-1", name="Ridge Stand")
    seed_hunt(conn, "stand-1", member_id="member-2")

    response = client.post("/api/hunts", json={"stand_id": "stand-1", "guests": []})

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "stand_occupied",
        "message": "Ridge Stand is occupied",
        "stand_id": "stand-1",
        "stand_name": "Ridge Stand",
    }


def test_checkout_someone_elses_hunt_rejected(conn, client):
    seed_stand(conn, "stand-1")
    hunt_id = seed_hunt(conn, "stand-1", member_id="member-2")

    response = client.post(f"/api/hunts/{hunt_id}/check-out")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "checkout_forbidden"


def test_stale_hunt_cannot_be_checked_out(conn, client):
    seed_stand(conn, "stand-1")
    stale_time = datetime.now(timezone.utc) - timedelta(days=2)
    hunt_id = seed_hunt(conn, "stand-1", checked_in_at=stale_time.isoformat())

    response = client.post(f"/api/hunts/{hunt_id}/check-out")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "hunt_not_active"


def test_map_state_marks_long_hunt_overdue(conn, client, monkeypatch):
    # 18:00 UTC is 13:00 Eastern: a hunt nine hours old still falls after the
    # 3:00 AM session boundary, so it reads as overdue rather than stale.
    now = datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(main_module, "utc_now", lambda: now)
    seed_stand(conn, "stand-1", name="Long Sit", type="box")
    seed_member(conn, "member-2", first_name="Marcus", last_name="")
    seed_hunt(
        conn,
        "stand-1",
        member_id="member-2",
        checked_in_at=(now - timedelta(hours=9)).isoformat(),
    )

    response = client.get("/api/map-state")
    stand = response.json()["stands"][0]

    assert response.status_code == 200
    assert stand["status"] == "overdue"
    assert stand["occupant_type"] == "member"
    assert stand["occupant_initials"] == "M"


def test_map_state_hides_stale_hunt(conn, client, frozen_now):
    seed_stand(conn, "stand-1", name="Old Sit", type="ground")
    seed_hunt(
        conn,
        "stand-1",
        member_id="member-2",
        checked_in_at=(frozen_now - timedelta(days=2)).isoformat(),
    )

    data = client.get("/api/map-state").json()

    assert data["stands"][0]["status"] == "open"
    assert data["stands"][0]["occupied_by"] is None
    assert data["live_count"] == 0


def test_map_state_returns_safe_host_and_guest_details(conn, client, frozen_now):
    seed_member(conn, DEFAULT_MEMBER_ID, first_name="Mike", last_name="Doe")
    seed_stand(conn, "stand-1", name="Host Stand")
    seed_stand(conn, "stand-2", name="Guest Stand", type="box")
    checked_in_at = (frozen_now - timedelta(hours=1)).isoformat()
    host_hunt_id = seed_hunt(conn, "stand-1", checked_in_at=checked_in_at)
    seed_hunt(
        conn,
        "stand-2",
        host_hunt_id=host_hunt_id,
        checked_in_at=checked_in_at,
        guest_name="Jane Smith",
        guest_phone="555-0100",
    )

    data = client.get("/api/map-state").json()
    host = next(stand for stand in data["stands"] if stand["id"] == "stand-1")
    guest = next(stand for stand in data["stands"] if stand["id"] == "stand-2")

    assert host["occupied_by"] == "Mike D."
    assert host["occupant_initials"] == "MD"
    assert host["occupant_type"] == "member"
    assert host["can_check_out"] is True
    assert host["hunt_id"] == host_hunt_id
    assert guest["occupied_by"] == "Jane Smith"
    assert guest["occupant_initials"] == "JS"
    assert guest["occupant_type"] == "guest"
    assert guest["guest_of"] == "Mike D."
    assert guest["can_check_out"] is False
    assert "guest_phone" not in guest
    assert data["live_count"] == 2


def test_map_state_returns_capacity_and_both_occupants(conn, client, frozen_now):
    seed_member(conn, DEFAULT_MEMBER_ID, first_name="Mike", last_name="Doe")
    seed_stand(conn, "stand-1", name="Double Stand", type="box", capacity=2)
    checked_in_at = (frozen_now - timedelta(hours=1)).isoformat()
    host_hunt_id = seed_hunt(conn, "stand-1", checked_in_at=checked_in_at)
    seed_hunt(
        conn,
        "stand-1",
        host_hunt_id=host_hunt_id,
        checked_in_at=checked_in_at,
        guest_name="Jane Smith",
        guest_phone="555-0100",
    )

    response = client.get("/api/map-state")
    stand = response.json()["stands"][0]

    assert response.status_code == 200
    assert stand["capacity"] == 2
    assert stand["occupied_count"] == 2
    assert stand["available_seats"] == 0
    assert [occupant["display_name"] for occupant in stand["occupants"]] == [
        "Mike D.",
        "Jane Smith",
    ]
    assert stand["occupants"][1]["occupant_type"] == "guest"
    assert stand["occupants"][1]["guest_of"] == "Mike D."
    assert "guest_phone" not in stand["occupants"][1]


# An unknown property must be refused, not silently served as the primary one.
def test_map_state_rejects_unknown_property(conn, client):
    seed_stand(conn, "stand-1", name="Creek Stand")

    response = client.get("/api/map-state?property=no-such-property")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "property_not_found"
    # No stand coordinates may leak through the refusal.
    assert "stands" not in response.json()


# An inactive property is refused the same way an unknown slug is.
def test_map_state_rejects_inactive_property(conn, client):
    conn.execute(
        """
        INSERT INTO properties (
            id, slug, name, center_lat, center_lng, default_zoom, is_active
        ) VALUES (?, ?, ?, ?, ?, ?, 0)
        """,
        ("retired-tract", "retired-tract", "Retired Tract", 35.0, -78.0, 15),
    )
    conn.commit()

    response = client.get("/api/map-state?property=retired-tract")

    assert response.status_code == 404


# Google members have no password, so the column must accept NULL.
def test_members_table_accepts_google_only_member(conn):

    conn.execute(
        """
        INSERT INTO members (
            id, email, google_sub, first_name, last_name, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "member-google",
            "gale@example.com",
            "108xyz",
            "Gale",
            "Ray",
            "2026-09-15T00:00:00+00:00",
        ),
    )
    conn.commit()

    row = conn.execute(
        "SELECT password_hash, google_sub, last_login_at FROM members"
    ).fetchone()
    assert row["password_hash"] is None
    assert row["google_sub"] == "108xyz"
    assert row["last_login_at"] is None


# Two members must never share one Google identity.
def test_members_table_rejects_duplicate_google_sub(conn):
    for member_id, email in (("m1", "one@example.com"), ("m2", "two@example.com")):
        try:
            conn.execute(
                """
                INSERT INTO members (
                    id, email, google_sub, first_name, last_name, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (member_id, email, "shared-sub", "A", "B", "2026-09-15T00:00:00+00:00"),
            )
        except sqlite3.IntegrityError:
            assert member_id == "m2"
            return
    raise AssertionError("duplicate google_sub was accepted")


# Password-only members all leave google_sub NULL; that must not collide.
def test_members_table_allows_many_null_google_subs(conn):
    conn.executemany(
        """
        INSERT INTO members (
            id, email, password_hash, first_name, last_name, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            ("m1", "one@example.com", "hash-1", "A", "B", "2026-09-15T00:00:00+00:00"),
            ("m2", "two@example.com", "hash-2", "C", "D", "2026-09-15T00:00:00+00:00"),
        ],
    )
    conn.commit()

    assert conn.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 2


# The retired endpoints exposed every property's coordinates without auth.
def test_removed_unscoped_endpoints_are_gone():
    client = TestClient(app)
    assert client.get("/api/stands").status_code == 404
    assert client.get("/api/map-features").status_code == 404


# The pre-auth members table, as databases created before this slice have it.
LEGACY_MEMBERS_SCHEMA = """
    CREATE TABLE members (
        id TEXT PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        is_admin INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0, 1)),
        first_name TEXT NOT NULL,
        last_name TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
"""


def legacy_members_connection(tmp_path):
    """A database whose members table predates Google sign-in."""
    conn = sqlite3.connect(tmp_path / "legacy.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        SCHEMA.replace(
            "CREATE TABLE IF NOT EXISTS members",
            "CREATE TABLE IF NOT EXISTS members_unused",
        )
    )
    conn.executescript("DROP TABLE IF EXISTS members_unused;" + LEGACY_MEMBERS_SCHEMA)
    seed_primary_property(conn)
    conn.execute(
        """
        INSERT INTO members (
            id, email, password_hash, is_admin, first_name, last_name, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "member-1",
            "Mike@Example.com",
            "argon2-hash",
            1,
            "Mike",
            "Doe",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    conn.execute(
        "INSERT INTO stands (id, name, type, lat, lng) VALUES (?, ?, ?, ?, ?)",
        ("stand-1", "Creek Stand", "ladder", 35.0, -78.0),
    )
    conn.execute(
        "INSERT INTO hunts (stand_id, member_id, checked_in_at) VALUES (?, ?, ?)",
        ("stand-1", "member-1", "2026-01-01T12:00:00+00:00"),
    )
    conn.commit()
    return conn


# Rebuilding members must not disturb the hunts that reference it.
def test_migration_preserves_members_and_hunts(tmp_path):
    conn = legacy_members_connection(tmp_path)
    conn.execute("PRAGMA foreign_keys = ON")

    migrate_members_for_auth(conn)

    member = conn.execute("SELECT * FROM members").fetchone()
    assert member["id"] == "member-1"
    assert member["password_hash"] == "argon2-hash"
    assert member["is_admin"] == 1
    assert member["first_name"] == "Mike"
    assert conn.execute("SELECT COUNT(*) FROM hunts").fetchone()[0] == 1
    # The rebuild must not orphan the hunts pointing at the old table.
    assert conn.execute("PRAGMA foreign_key_check(hunts)").fetchall() == []
    # Enforcement is turned off for the rebuild and must be restored.
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


# Existing mixed-case emails are normalized so the allowlist match is reliable.
def test_migration_lowercases_existing_emails(tmp_path):
    conn = legacy_members_connection(tmp_path)

    migrate_members_for_auth(conn)

    assert conn.execute("SELECT email FROM members").fetchone()[0] == "mike@example.com"


# After migrating, the column accepts the Google-only member it exists for.
def test_migration_allows_google_member_afterwards(tmp_path):
    conn = legacy_members_connection(tmp_path)

    migrate_members_for_auth(conn)
    conn.execute(
        """
        INSERT INTO members (
            id, email, google_sub, first_name, last_name, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "m2",
            "gale@example.com",
            "108xyz",
            "Gale",
            "Ray",
            "2026-09-15T00:00:00+00:00",
        ),
    )
    conn.commit()

    assert conn.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 2


# get_connection runs this on every connection, so repeats must be free.
def test_migration_is_idempotent(tmp_path):
    conn = legacy_members_connection(tmp_path)

    migrate_members_for_auth(conn)
    migrate_members_for_auth(conn)
    migrate_members_for_auth(conn)

    assert conn.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM hunts").fetchone()[0] == 1


# A database already carrying the current shape is left untouched.
def test_migration_skips_current_schema():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    migrate_members_for_auth(conn)

    names = {
        row["name"] for row in conn.execute("PRAGMA table_info(members)").fetchall()
    }
    assert "google_sub" in names
    assert "members_migrated" not in {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


# An older members table gains the missing last-login column without being
# rebuilt, which would put the stored Google details at risk.
def test_migration_adds_missing_last_login_without_losing_google_sub():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE members (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT,
            google_sub TEXT UNIQUE,
            is_admin INTEGER NOT NULL DEFAULT 0,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO members (
            id, email, google_sub, first_name, last_name, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "member-1",
            "mike@example.com",
            "google-sub-1",
            "Mike",
            "Doe",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    conn.commit()

    # Runs on every connection, so a second pass must be a no-op.
    migrate_members_for_auth(conn)
    migrate_members_for_auth(conn)

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(members)")}
    member = conn.execute("SELECT google_sub, last_login_at FROM members").fetchone()

    assert "last_login_at" in columns
    assert member["google_sub"] == "google-sub-1"
    assert member["last_login_at"] is None


# --- Authentication routes ---


# Signing in with the right password returns the member and sets a cookie
def test_login_succeeds_with_correct_password(anonymous_client, conn):
    seed_member(conn, "member-9", email="nine@example.com", password="swamp-oak-42")

    response = anonymous_client.post(
        "/api/auth/login",
        json={"email": "nine@example.com", "password": "swamp-oak-42"},
    )

    assert response.status_code == 200
    assert response.json()["id"] == "member-9"
    assert anonymous_client.cookies.get(config.SESSION_COOKIE_NAME)


# The cookie from signing in works on a guarded route
def test_login_cookie_identifies_the_member(anonymous_client, conn):
    seed_member(conn, "member-9", email="nine@example.com", password="swamp-oak-42")

    anonymous_client.post(
        "/api/auth/login",
        json={"email": "nine@example.com", "password": "swamp-oak-42"},
    )

    assert anonymous_client.get("/api/auth/me").json()["id"] == "member-9"


# Capital letters and stray spaces in the email must not stop a sign-in
@pytest.mark.parametrize(
    "typed", ["Nine@Example.com", "  NINE@EXAMPLE.COM  ", "nine@example.com "]
)
def test_login_accepts_any_casing_of_the_email(anonymous_client, conn, typed):
    seed_member(conn, "member-9", email="nine@example.com", password="swamp-oak-42")

    response = anonymous_client.post(
        "/api/auth/login", json={"email": typed, "password": "swamp-oak-42"}
    )

    assert response.status_code == 200


# A wrong password is refused
def test_login_rejects_a_wrong_password(anonymous_client, conn):
    seed_member(conn, "member-9", email="nine@example.com", password="swamp-oak-42")

    response = anonymous_client.post(
        "/api/auth/login",
        json={"email": "nine@example.com", "password": "not-the-password"},
    )

    assert response.status_code == 401


# An unknown email gives exactly the same answer as a wrong password, so nobody
# can tell which addresses have accounts
def test_login_hides_whether_the_email_exists(anonymous_client, conn):
    seed_member(conn, "member-9", email="nine@example.com", password="swamp-oak-42")

    wrong_password = anonymous_client.post(
        "/api/auth/login",
        json={"email": "nine@example.com", "password": "not-the-password"},
    )
    unknown_email = anonymous_client.post(
        "/api/auth/login",
        json={"email": "stranger@example.com", "password": "swamp-oak-42"},
    )

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


# A member with no password set, such as a Google-only account, cannot sign in
# with one
def test_login_rejects_a_member_without_a_password(anonymous_client, conn):
    seed_member(conn, "member-9", email="nine@example.com", password_hash=None)

    response = anonymous_client.post(
        "/api/auth/login",
        json={"email": "nine@example.com", "password": "anything"},
    )

    assert response.status_code == 401


# A failed sign-in must not leave a cookie behind
def test_failed_login_sets_no_cookie(anonymous_client, conn):
    seed_member(conn, "member-9", email="nine@example.com", password="swamp-oak-42")

    anonymous_client.post(
        "/api/auth/login",
        json={"email": "nine@example.com", "password": "not-the-password"},
    )

    assert anonymous_client.cookies.get(config.SESSION_COOKIE_NAME) is None


# Signing in records when it happened
def test_login_records_the_time(anonymous_client, conn):
    seed_member(conn, "member-9", email="nine@example.com", password="swamp-oak-42")

    anonymous_client.post(
        "/api/auth/login",
        json={"email": "nine@example.com", "password": "swamp-oak-42"},
    )

    row = conn.execute(
        "SELECT last_login_at FROM members WHERE id = ?", ("member-9",)
    ).fetchone()
    assert row["last_login_at"] is not None


# A refused sign-in leaves the last sign-in time alone
def test_failed_login_records_nothing(anonymous_client, conn):
    seed_member(conn, "member-9", email="nine@example.com", password="swamp-oak-42")

    anonymous_client.post(
        "/api/auth/login",
        json={"email": "nine@example.com", "password": "not-the-password"},
    )

    row = conn.execute(
        "SELECT last_login_at FROM members WHERE id = ?", ("member-9",)
    ).fetchone()
    assert row["last_login_at"] is None


# The sign-in reply never carries an email or a password hash
def test_login_reply_carries_no_private_details(anonymous_client, conn):
    seed_member(conn, "member-9", email="nine@example.com", password="swamp-oak-42")

    response = anonymous_client.post(
        "/api/auth/login",
        json={"email": "nine@example.com", "password": "swamp-oak-42"},
    )

    assert set(response.json()) == {"id", "first_name", "last_name", "is_admin"}
    assert "nine@example.com" not in response.text


# Asking who you are without signing in is refused
def test_me_requires_signing_in(anonymous_client):
    assert anonymous_client.get("/api/auth/me").status_code == 401


# Signing out clears the cookie
def test_logout_clears_the_cookie(anonymous_client, conn):
    seed_member(conn, "member-9", email="nine@example.com", password="swamp-oak-42")
    anonymous_client.post(
        "/api/auth/login",
        json={"email": "nine@example.com", "password": "swamp-oak-42"},
    )

    response = anonymous_client.post("/api/auth/logout")

    assert response.status_code == 200
    assert not anonymous_client.cookies.get(config.SESSION_COOKIE_NAME)


# Signing out works even with a broken cookie, or nobody could ever clear one
def test_logout_works_without_a_valid_session(anonymous_client):
    anonymous_client.cookies.set(config.SESSION_COOKIE_NAME, "not-a-real-token")

    assert anonymous_client.post("/api/auth/logout").status_code == 200


# Every sign-in attempt must do the same password-checking work, whatever the
# email was. Counting the checks rather than timing them keeps this test from
# failing on a busy machine.
@pytest.mark.parametrize(
    "email", ["nine@example.com", "stranger@example.com", "google@example.com"]
)
def test_every_login_attempt_checks_a_password(
    anonymous_client, conn, monkeypatch, email
):
    seed_member(conn, "member-9", email="nine@example.com", password="swamp-oak-42")
    seed_member(conn, "member-8", email="google@example.com", password_hash=None)

    checks = []
    real_verify = auth._password_hash.verify
    monkeypatch.setattr(
        auth._password_hash,
        "verify",
        lambda *args, **kwargs: (checks.append(1), real_verify(*args, **kwargs))[1],
    )

    anonymous_client.post(
        "/api/auth/login", json={"email": email, "password": "wrong-password"}
    )

    assert len(checks) == 1


# A damaged stored hash must not answer quicker than a real one either
def test_a_broken_stored_hash_still_checks_a_password(
    anonymous_client, conn, monkeypatch
):
    seed_member(conn, "member-9", email="nine@example.com", password_hash="not-a-hash")

    checks = []
    real_verify = auth._password_hash.verify
    monkeypatch.setattr(
        auth._password_hash,
        "verify",
        lambda *args, **kwargs: (checks.append(1), real_verify(*args, **kwargs))[1],
    )

    response = anonymous_client.post(
        "/api/auth/login", json={"email": "nine@example.com", "password": "anything"}
    )

    assert response.status_code == 401
    assert len(checks) >= 1
