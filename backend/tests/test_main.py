import threading
from datetime import datetime, timedelta, timezone

import app.main as main_module  # The module holding get_connection, so we can swap it out
import pytest
from app import auth, config
from app.main import app  # The actual FastAPI app we're testing

# Shared database fixtures and seed helpers. conftest.py is loaded by pytest
# automatically; these names are imported only where a test calls them directly.
from conftest import (
    DEFAULT_MEMBER_ID,
    authed_client,
    seed_hunt,
    seed_member,
    seed_primary_property,
    seed_stand,
)
from fastapi.testclient import TestClient  # Lets us send fake HTTP requests to that app
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


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
def test_concurrent_checkin_only_one_wins(committed_db):
    with committed_db.connect() as setup:
        seed_member(setup, DEFAULT_MEMBER_ID)
        seed_stand(setup, "test-stand-1")

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
def test_checkout_twice_rejected(conn, client):
    seed_stand(conn, "test-stand-1")
    hunt_id = seed_hunt(conn, "test-stand-1")

    first_response = client.post(f"/api/hunts/{hunt_id}/check-out")
    second_response = client.post(f"/api/hunts/{hunt_id}/check-out")

    assert first_response.status_code == 200
    assert second_response.status_code == 409


# Second check-in attempt on an occupied stand should not touch the original row
def test_second_checkin_does_not_overwrite_original(conn, client):
    seed_stand(conn, "test-stand-1")

    first = client.post("/api/hunts", json={"stand_id": "test-stand-1", "guests": []})
    second = client.post("/api/hunts", json={"stand_id": "test-stand-1", "guests": []})

    assert first.status_code == 200
    assert second.status_code == 409

    # Only one row should exist, proving the original was never overwritten.
    rows = conn.execute(
        text("SELECT * FROM hunts WHERE stand_id = :id"), {"id": "test-stand-1"}
    ).fetchall()
    assert len(rows) == 1


# Host with two guests should create 3 rows total
def test_checkin_with_two_guests_creates_three_rows(conn, client):
    for stand_id in ["test-stand-1", "test-stand-2", "test-stand-3"]:
        seed_stand(conn, stand_id)

    response = client.post(
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
    rows = conn.execute(text("SELECT * FROM hunts")).fetchall()
    assert len(rows) == 3


# One guest's stand is already occupied: the whole submission is rejected and
# the host's row is rolled back with it.
def test_checkin_guest_stand_occupied_rejects_all(conn, client):
    for stand_id in ["test-stand-1", "test-stand-2"]:
        seed_stand(conn, stand_id)
    # The guest's stand is already occupied by someone else.
    seed_hunt(conn, "test-stand-2", member_id="member-2")

    response = client.post(
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
    rows = conn.execute(
        text("SELECT * FROM hunts WHERE stand_id = :id"), {"id": "test-stand-1"}
    ).fetchall()
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
def test_checkout_cascades_to_guests(conn, client):
    for stand_id in ["test-stand-1", "test-stand-2"]:
        seed_stand(conn, stand_id)

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
    guest_row = (
        conn.execute(
            text("SELECT * FROM hunts WHERE stand_id = :id"), {"id": "test-stand-2"}
        )
        .mappings()
        .fetchone()
    )
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


def test_checkin_over_capacity_rejects_every_row(conn, client):
    seed_stand(conn, "stand-1", name="Double Stand", type="box", capacity=2)

    response = client.post(
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

    row_count = conn.execute(text("SELECT COUNT(*) FROM hunts")).scalar()
    assert row_count == 0


def test_concurrent_checkins_only_one_claims_final_seat(committed_db):
    with committed_db.connect() as setup:
        seed_member(setup, DEFAULT_MEMBER_ID)
        seed_stand(setup, "stand-1", name="Double Stand", type="box", capacity=2)
        # One of the two seats is already taken, so only one request can win.
        seed_hunt(setup, "stand-1", member_id="member-2")

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
    with committed_db.connect() as check:
        active_count = check.execute(
            text("SELECT COUNT(*) FROM hunts WHERE checked_out_at IS NULL")
        ).scalar()
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
        text(
            """
            INSERT INTO properties (
                id, slug, name, center_lat, center_lng, default_zoom, is_active
            ) VALUES (:id, :slug, :name, :lat, :lng, :zoom, false)
            """
        ),
        {
            "id": "retired-tract",
            "slug": "retired-tract",
            "name": "Retired Tract",
            "lat": 35.0,
            "lng": -78.0,
            "zoom": 15,
        },
    )
    conn.commit()

    response = client.get("/api/map-state?property=retired-tract")

    assert response.status_code == 404


# Google members have no password, so the column must accept NULL.
def test_members_table_accepts_google_only_member(conn):

    conn.execute(
        text(
            """
            INSERT INTO members (
                id, email, google_sub, first_name, last_name, created_at
            ) VALUES (:id, :email, :sub, :first, :last, :created)
            """
        ),
        {
            "id": "member-google",
            "email": "gale@example.com",
            "sub": "108xyz",
            "first": "Gale",
            "last": "Ray",
            "created": "2026-09-15T00:00:00+00:00",
        },
    )
    conn.commit()

    row = (
        conn.execute(
            text("SELECT password_hash, google_sub, last_login_at FROM members")
        )
        .mappings()
        .fetchone()
    )
    assert row["password_hash"] is None
    assert row["google_sub"] == "108xyz"
    assert row["last_login_at"] is None


# Two members must never share one Google identity.
def test_members_table_rejects_duplicate_google_sub(conn):
    insert = text(
        """
        INSERT INTO members (
            id, email, google_sub, first_name, last_name, created_at
        ) VALUES (:id, :email, 'shared-sub', 'A', 'B', '2026-09-15T00:00:00+00:00')
        """
    )
    conn.execute(insert, {"id": "m1", "email": "one@example.com"})
    conn.commit()

    with pytest.raises(IntegrityError):
        conn.execute(insert, {"id": "m2", "email": "two@example.com"})


# Password-only members all leave google_sub NULL; that must not collide.
def test_members_table_allows_many_null_google_subs(conn):
    conn.execute(
        text(
            """
            INSERT INTO members (
                id, email, password_hash, first_name, last_name, created_at
            ) VALUES (:id, :email, :hash, :first, :last, :created)
            """
        ),
        [
            {"id": "m1", "email": "one@example.com", "hash": "hash-1", "first": "A", "last": "B", "created": "2026-09-15T00:00:00+00:00"},
            {"id": "m2", "email": "two@example.com", "hash": "hash-2", "first": "C", "last": "D", "created": "2026-09-15T00:00:00+00:00"},
        ],
    )
    conn.commit()

    assert conn.execute(text("SELECT COUNT(*) FROM members")).scalar() == 2


# The retired endpoints exposed every property's coordinates without auth.
def test_removed_unscoped_endpoints_are_gone():
    client = TestClient(app)
    assert client.get("/api/stands").status_code == 404
    assert client.get("/api/map-features").status_code == 404


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

    row = (
        conn.execute(
            text("SELECT last_login_at FROM members WHERE id = :id"), {"id": "member-9"}
        )
        .mappings()
        .fetchone()
    )
    assert row["last_login_at"] is not None


# A refused sign-in leaves the last sign-in time alone
def test_failed_login_records_nothing(anonymous_client, conn):
    seed_member(conn, "member-9", email="nine@example.com", password="swamp-oak-42")

    anonymous_client.post(
        "/api/auth/login",
        json={"email": "nine@example.com", "password": "not-the-password"},
    )

    row = (
        conn.execute(
            text("SELECT last_login_at FROM members WHERE id = :id"), {"id": "member-9"}
        )
        .mappings()
        .fetchone()
    )
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


# a damaged stored hash must not answer quicker than a real one either
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


# --- Identity and authorization ---------------------------------------------
# the hunt records whoever was signed in, not one hardcoded member. This is the guard against a fixed identity creeping back in.
def test_check_in_records_the_signed_in_member(conn, client):
    seed_stand(conn, "stand-1")
    seed_stand(conn, "stand-2")
    seed_member(conn, "member-2", first_name="Sara")

    first = client.post(
        "/api/hunts", json={"stand_id": "stand-1", "guests": []}
    )
    second = authed_client("member-2").post(
        "/api/hunts", json={"stand_id": "stand-2", "guests": []}
    )

    assert first.status_code == 200
    assert second.status_code == 200

    owners = dict(
        conn.execute(text("SELECT stand_id, member_id FROM hunts")).fetchall()
    )

    assert owners["stand-1"] == DEFAULT_MEMBER_ID
    assert owners["stand-2"] == "member-2"


# an admin may close a stand someone else left open
def test_admin_can_check_out_another_members_hunt(conn, admin_client):
    seed_stand(conn, "stand-1")
    hunt_id = seed_hunt(conn, "stand-1", member_id=DEFAULT_MEMBER_ID)

    response = admin_client.post(f"/api/hunts/{hunt_id}/check-out")

    assert response.status_code == 200
    assert response.json()["checked_out_at"] is not None


# the button the map offers must match what checkout actually permits, or a
# member clicks something that then fails.
def test_can_check_out_matches_who_may_check_out(conn, client):
    seed_stand(conn, "stand-1")
    seed_stand(conn, "stand-2")
    seed_hunt(conn, "stand-1", member_id=DEFAULT_MEMBER_ID)
    seed_hunt(conn, "stand-2", member_id="member-2")

    data = client.get("/api/map-state").json()
    own = next(s for s in data["stands"] if s["id"] == "stand-1")
    other = next(s for s in data["stands"] if s["id"] == "stand-2")

    assert own["occupants"][0]["can_check_out"] is True
    assert other["occupants"][0]["can_check_out"] is False


# an admin sees the button on every occupied stand, which is intended
def test_admin_can_check_out_every_occupied_stand(conn, admin_client):
    seed_stand(conn, "stand-1")
    seed_hunt(conn, "stand-1", member_id=DEFAULT_MEMBER_ID)

    data = admin_client.get("/api/map-state").json()
    stand = next(s for s in data["stands"] if s["id"] == "stand-1")

    assert stand["occupants"][0]["can_check_out"] is True
