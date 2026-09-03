import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import app.main as main_module  # The module holding get_connection, so we can swap it out
import pytest
from app.database import SCHEMA  # CREATE TABLE statements, so test DBs match production
from app.main import app  # The actual FastAPI app we're testing
from fastapi.testclient import TestClient  # Lets us send fake HTTP requests to that app


# Retired stand should be rejected with 409
def test_retired_stand_rejected(monkeypatch):
    # fake in-memory database for this test
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    # seed one retired stand
    conn.execute(
        "INSERT INTO stands (id, name, type, lat, lng, is_retired) VALUES (?, ?, ?, ?, ?, ?)",
        ("test-stand-1", "Test Stand 1", "ladder", 35.0, -78.0, 1),
    )
    conn.commit()

    # make check_in use this fake db
    monkeypatch.setattr(main_module, "get_connection", lambda: conn)

    # try to check in, expect rejection
    client = TestClient(app)
    response = client.post(
        "/api/hunts", json={"stand_id": "test-stand-1", "guests": []}
    )
    assert response.status_code == 409


# Stand that was checked out should be checkable again
def test_checkin_succeeds_after_checkout(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    # seed one open stand
    conn.execute(
        "INSERT INTO stands (id, name, type, lat, lng, is_retired) VALUES (?, ?, ?, ?, ?, ?)",
        ("test-stand-1", "Test Stand 1", "ladder", 35.0, -78.0, 0),
    )

    # seed a hunt that already checked out
    conn.execute(
        "INSERT INTO hunts (stand_id, member_id, checked_in_at, checked_out_at) VALUES (?, ?, ?, ?)",
        (
            "test-stand-1",
            "member-1",
            "2026-11-10T12:00:00+00:00",
            "2026-11-10T15:00:00+00:00",
        ),
    )
    conn.commit()

    monkeypatch.setattr(main_module, "get_connection", lambda: conn)

    # should succeed since old session is closed
    client = TestClient(app)
    response = client.post(
        "/api/hunts", json={"stand_id": "test-stand-1", "guests": []}
    )
    assert response.status_code == 200


# Two people check in at once, only one should win
def test_concurrent_checkin_only_one_wins(monkeypatch):
    db_path = "test_concurrent.db"

    # real file on disk, so each thread can open its own independent connection
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    # seed one open stand
    conn.execute(
        "INSERT INTO stands (id, name, type, lat, lng, is_retired) VALUES (?, ?, ?, ?, ?, ?)",
        ("test-stand-1", "Test Stand 1", "ladder", 35.0, -78.0, 0),
    )
    conn.commit()
    conn.close()

    # each call opens a fresh connection to the same file, mimicking real concurrent traffic
    def fake_get_connection():
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(main_module, "get_connection", fake_get_connection)

    # shared list both threads report their result into
    results = []

    # one hunter's check-in attempt
    def make_request():
        client = TestClient(app)
        response = client.post(
            "/api/hunts", json={"stand_id": "test-stand-1", "guests": []}
        )
        results.append(response.status_code)

    # two hunters, sent in at the same time
    thread1 = threading.Thread(target=make_request)
    thread2 = threading.Thread(target=make_request)
    thread1.start()
    thread2.start()
    thread1.join()
    thread2.join()

    # exactly one wins, one loses
    assert results.count(200) == 1
    assert results.count(409) == 1

    # cleanup the temp db file
    os.remove(db_path)


# More than 2 guests should be rejected by the validator, no database needed
def test_too_many_guests_rejected():
    client = TestClient(app)
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
def test_checkin_succeeds_when_only_session_is_stale(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    # seed one normal, open stand
    conn.execute(
        "INSERT INTO stands (id, name, type, lat, lng, is_retired) VALUES (?, ?, ?, ?, ?, ?)",
        ("test-stand-1", "Test Stand 1", "ladder", 35.0, -78.0, 0),
    )

    # 30 days ago, guaranteed stale no matter what day this test runs
    stale_time = datetime.now(timezone.utc) - timedelta(days=30)

    # this hunt was never checked out, just old and past the boundary
    conn.execute(
        "INSERT INTO hunts (stand_id, member_id, checked_in_at) VALUES (?, ?, ?)",
        ("test-stand-1", "member-1", stale_time.isoformat()),
    )
    conn.commit()

    # point check_in() at this fake database
    monkeypatch.setattr(main_module, "get_connection", lambda: conn)

    # a new check-in should succeed since the old session is stale
    client = TestClient(app)
    response = client.post(
        "/api/hunts", json={"stand_id": "test-stand-1", "guests": []}
    )
    assert response.status_code == 200


# Checking out an open hunt should succeed and confirm the checkout time
def test_checkout_succeeds(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    # seed one open, non-retired stand
    conn.execute(
        "INSERT INTO stands (id, name, type, lat, lng, is_retired) VALUES (?, ?, ?, ?, ?, ?)",
        ("test-stand-1", "Test Stand 1", "ladder", 35.0, -78.0, 0),
    )
    # seed one open hunt (no checked_out_at yet)
    conn.execute(
        "INSERT INTO hunts (stand_id, member_id, checked_in_at) VALUES (?, ?, ?)",
        ("test-stand-1", "member-1", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()

    # point check_out() at this fake database
    monkeypatch.setattr(main_module, "get_connection", lambda: conn)

    # check out hunt id 1, should succeed
    client = TestClient(app)
    response = client.post("/api/hunts/1/check-out")

    # confirm success and that a checkout time was actually returned
    assert response.status_code == 200
    data = response.json()
    assert data["checked_out_at"] is not None


# Checking out a hunt_id that doesn't exist should 404
def test_checkout_nonexistent_hunt_rejected(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()

    # point check_out() at this fake, empty database
    monkeypatch.setattr(main_module, "get_connection", lambda: conn)

    # no hunt with id 999 exists, should 404
    client = TestClient(app)
    response = client.post("/api/hunts/999/check-out")

    assert response.status_code == 404


# Checking out an already-closed hunt should be rejected with 409 (used temp file for this)
def test_checkout_twice_rejected(monkeypatch):
    db_path = "test_checkout_twice.db"

    # real file on disk, so a new connection can reopen the same data
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    # seed one open stand
    conn.execute(
        "INSERT INTO stands (id, name, type, lat, lng, is_retired) VALUES (?, ?, ?, ?, ?, ?)",
        ("test-stand-1", "Test Stand 1", "ladder", 35.0, -78.0, 0),
    )
    # seed one open hunt
    conn.execute(
        "INSERT INTO hunts (stand_id, member_id, checked_in_at) VALUES (?, ?, ?)",
        ("test-stand-1", "member-1", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()

    # each call opens a fresh connection to the same file, mimicking production
    def fake_get_connection():
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(main_module, "get_connection", fake_get_connection)

    # first check-out, should succeed
    client = TestClient(app)
    first_response = client.post("/api/hunts/1/check-out")
    assert first_response.status_code == 200

    # second check-out on the same hunt, should be rejected
    second_response = client.post("/api/hunts/1/check-out")
    assert second_response.status_code == 409

    # cleanup the temp db file
    os.remove(db_path)


# Second check-in attempt on an occupied stand should not touch the original row (used temp file for this)
def test_second_checkin_does_not_overwrite_original(monkeypatch):
    db_path = "test_second_checkin.db"

    # real file on disk, so a fresh connection can reopen it after check_in closes its own
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    # seed one open stand
    conn.execute(
        "INSERT INTO stands (id, name, type, lat, lng, is_retired) VALUES (?, ?, ?, ?, ?, ?)",
        ("test-stand-1", "Test Stand 1", "ladder", 35.0, -78.0, 0),
    )
    conn.commit()
    conn.close()

    # each call opens a fresh connection to the same file, mimicking production
    def fake_get_connection():
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(main_module, "get_connection", fake_get_connection)
    client = TestClient(app)

    # first check-in, should succeed
    first = client.post("/api/hunts", json={"stand_id": "test-stand-1", "guests": []})
    assert first.status_code == 200

    # second check-in on the same stand, should be rejected
    second = client.post("/api/hunts", json={"stand_id": "test-stand-1", "guests": []})
    assert second.status_code == 409

    # open a fresh connection to check the final state
    check_conn = sqlite3.connect(db_path, check_same_thread=False)
    check_conn.row_factory = sqlite3.Row
    rows = check_conn.execute(
        "SELECT * FROM hunts WHERE stand_id = ?", ("test-stand-1",)
    ).fetchall()
    check_conn.close()

    # only one row should exist, proving the original was never touched or duplicated
    assert len(rows) == 1

    # cleanup the temp db file
    os.remove(db_path)


# Host with two guests should create 3 rows total (used temp file for this)
def test_checkin_with_two_guests_creates_three_rows(monkeypatch):
    db_path = "test_two_guests.db"

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    # seed three open stands: host + 2 guest stands
    for stand_id in ["test-stand-1", "test-stand-2", "test-stand-3"]:
        conn.execute(
            "INSERT INTO stands (id, name, type, lat, lng, is_retired) VALUES (?, ?, ?, ?, ?, ?)",
            (stand_id, stand_id, "ladder", 35.0, -78.0, 0),
        )
    conn.commit()
    conn.close()

    def fake_get_connection():
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(main_module, "get_connection", fake_get_connection)

    client = TestClient(app)
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

    # open a fresh connection to check the final state
    check_conn = sqlite3.connect(db_path, check_same_thread=False)
    check_conn.row_factory = sqlite3.Row
    rows = check_conn.execute("SELECT * FROM hunts").fetchall()
    check_conn.close()

    assert len(rows) == 3

    os.remove(db_path)


# One guest's stand is already occupied submission should be rejected, no rows written
def test_checkin_guest_stand_occupied_rejects_all(monkeypatch):
    db_path = "test_guest_rollback.db"

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    # seed two open stands, host + one guest stand
    for stand_id in ["test-stand-1", "test-stand-2"]:
        conn.execute(
            "INSERT INTO stands (id, name, type, lat, lng, is_retired) VALUES (?, ?, ?, ?, ?, ?)",
            (stand_id, stand_id, "ladder", 35.0, -78.0, 0),
        )

    # the guest's stand is ALREADY occupied by someone else
    conn.execute(
        "INSERT INTO hunts (stand_id, member_id, checked_in_at) VALUES (?, ?, ?)",
        ("test-stand-2", "member-2", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()

    def fake_get_connection():
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(main_module, "get_connection", fake_get_connection)

    client = TestClient(app)
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

    # confirm the host's row was NEVER written either
    check_conn = sqlite3.connect(db_path, check_same_thread=False)
    check_conn.row_factory = sqlite3.Row
    rows = check_conn.execute(
        "SELECT * FROM hunts WHERE stand_id = ?", ("test-stand-1",)
    ).fetchall()
    check_conn.close()

    assert len(rows) == 0

    os.remove(db_path)


# map-state should show an occupied stand as active, others as open, with correct live count
def test_map_state_reflects_active_checkin(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    # seed two open stands
    for stand_id in ["test-stand-1", "test-stand-2"]:
        conn.execute(
            "INSERT INTO stands (id, name, type, lat, lng, is_retired) VALUES (?, ?, ?, ?, ?, ?)",
            (stand_id, stand_id, "ladder", 35.0, -78.0, 0),
        )

    # only stand-1 has an active hunt
    conn.execute(
        "INSERT INTO hunts (stand_id, member_id, checked_in_at) VALUES (?, ?, ?)",
        ("test-stand-1", "member-1", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()

    # point get_map_state() at this fake database
    monkeypatch.setattr(main_module, "get_connection", lambda: conn)

    client = TestClient(app)
    response = client.get("/api/map-state")
    data = response.json()

    # find each stand in the returned list by id, since order isn't guaranteed
    stand_1 = next(s for s in data["stands"] if s["id"] == "test-stand-1")
    stand_2 = next(s for s in data["stands"] if s["id"] == "test-stand-2")

    # stand-1 should be active, stand-2 should still be open
    assert stand_1["status"] == "active"
    assert stand_2["status"] == "open"
    assert data["live_count"] == 1


# Checking out the host should also close any guest rows from the same check-in (used temp file for this)
def test_checkout_cascades_to_guests(monkeypatch):
    db_path = "test_cascade.db"

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    for stand_id in ["test-stand-1", "test-stand-2"]:
        conn.execute(
            "INSERT INTO stands (id, name, type, lat, lng, is_retired) VALUES (?, ?, ?, ?, ?, ?)",
            (stand_id, stand_id, "ladder", 35.0, -78.0, 0),
        )
    conn.commit()
    conn.close()

    def fake_get_connection():
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(main_module, "get_connection", fake_get_connection)
    client = TestClient(app)

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

    # fresh connection to find the host's hunt id
    check_conn = sqlite3.connect(db_path, check_same_thread=False)
    check_conn.row_factory = sqlite3.Row
    host_row = check_conn.execute(
        "SELECT * FROM hunts WHERE stand_id = ? AND guest_name IS NULL",
        ("test-stand-1",),
    ).fetchone()
    check_conn.close()

    checkout_response = client.post(f"/api/hunts/{host_row['id']}/check-out")
    assert checkout_response.status_code == 200

    # another fresh connection to confirm the guest row closed too
    final_conn = sqlite3.connect(db_path, check_same_thread=False)
    final_conn.row_factory = sqlite3.Row
    guest_row = final_conn.execute(
        "SELECT * FROM hunts WHERE stand_id = ?", ("test-stand-2",)
    ).fetchone()
    final_conn.close()

    assert guest_row["checked_out_at"] is not None
    assert guest_row["host_hunt_id"] == host_row["id"]

    os.remove(db_path)


@pytest.mark.parametrize("field", ["name", "phone"])
def test_blank_guest_fields_rejected(field):
    guest = {"name": "Guest A", "phone": "555-0100", "stand_id": "stand-2"}
    guest[field] = "   "

    response = TestClient(app).post(
        "/api/hunts",
        json={"stand_id": "stand-1", "guests": [guest]},
    )

    assert response.status_code == 422


def test_guest_cannot_use_host_stand():
    response = TestClient(app).post(
        "/api/hunts",
        json={
            "stand_id": "stand-1",
            "guests": [
                {"name": "Guest A", "phone": "555-0100", "stand_id": "stand-1"}
            ],
        },
    )

    assert response.status_code == 422


def test_two_guests_cannot_share_a_stand():
    response = TestClient(app).post(
        "/api/hunts",
        json={
            "stand_id": "stand-1",
            "guests": [
                {"name": "Guest A", "phone": "555-0100", "stand_id": "stand-2"},
                {"name": "Guest B", "phone": "555-0101", "stand_id": "stand-2"},
            ],
        },
    )

    assert response.status_code == 422


def test_nonexistent_guest_stand_rejected(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO stands (id, name, type, lat, lng) VALUES (?, ?, ?, ?, ?)",
        ("stand-1", "Host Stand", "ladder", 35.0, -78.0),
    )
    conn.commit()
    monkeypatch.setattr(main_module, "get_connection", lambda: conn)

    response = TestClient(app).post(
        "/api/hunts",
        json={
            "stand_id": "stand-1",
            "guests": [
                {"name": "Guest A", "phone": "555-0100", "stand_id": "missing"}
            ],
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "stand_not_found"
    assert response.json()["detail"]["stand_id"] == "missing"


def test_retired_guest_stand_rejected(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO stands (id, name, type, lat, lng, is_retired) VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("stand-1", "Host Stand", "ladder", 35.0, -78.0, 0),
            ("stand-2", "Retired Stand", "ladder", 35.1, -78.1, 1),
        ],
    )
    conn.commit()
    monkeypatch.setattr(main_module, "get_connection", lambda: conn)

    response = TestClient(app).post(
        "/api/hunts",
        json={
            "stand_id": "stand-1",
            "guests": [
                {"name": "Guest A", "phone": "555-0100", "stand_id": "stand-2"}
            ],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "stand_retired"
    assert response.json()["detail"]["stand_name"] == "Retired Stand"


def test_checkin_conflict_identifies_taken_stand(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO stands (id, name, type, lat, lng) VALUES (?, ?, ?, ?, ?)",
        ("stand-1", "Ridge Stand", "ladder", 35.0, -78.0),
    )
    conn.execute(
        "INSERT INTO hunts (stand_id, member_id, checked_in_at) VALUES (?, ?, ?)",
        ("stand-1", "member-2", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    monkeypatch.setattr(main_module, "get_connection", lambda: conn)

    response = TestClient(app).post(
        "/api/hunts", json={"stand_id": "stand-1", "guests": []}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "stand_occupied",
        "message": "Ridge Stand is occupied",
        "stand_id": "stand-1",
        "stand_name": "Ridge Stand",
    }


def test_checkout_someone_elses_hunt_rejected(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO hunts (stand_id, member_id, checked_in_at) VALUES (?, ?, ?)",
        ("stand-1", "member-2", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    monkeypatch.setattr(main_module, "get_connection", lambda: conn)

    response = TestClient(app).post("/api/hunts/1/check-out")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "checkout_forbidden"


def test_stale_hunt_cannot_be_checked_out(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    stale_time = datetime.now(timezone.utc) - timedelta(days=2)
    conn.execute(
        "INSERT INTO hunts (stand_id, member_id, checked_in_at) VALUES (?, ?, ?)",
        ("stand-1", "member-1", stale_time.isoformat()),
    )
    conn.commit()
    monkeypatch.setattr(main_module, "get_connection", lambda: conn)

    response = TestClient(app).post("/api/hunts/1/check-out")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "hunt_not_active"


def test_map_state_marks_long_hunt_overdue(monkeypatch):
    now = datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc)
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO stands (id, name, type, lat, lng) VALUES (?, ?, ?, ?, ?)",
        ("stand-1", "Long Sit", "box", 35.0, -78.0),
    )
    conn.execute(
        "INSERT INTO hunts (stand_id, member_id, checked_in_at) VALUES (?, ?, ?)",
        (
            "stand-1",
            "member-2",
            (now - timedelta(hours=9)).isoformat(),
        ),
    )
    conn.commit()
    monkeypatch.setattr(main_module, "get_connection", lambda: conn)
    monkeypatch.setattr(main_module, "utc_now", lambda: now)

    response = TestClient(app).get("/api/map-state")
    stand = response.json()["stands"][0]

    assert response.status_code == 200
    assert stand["status"] == "overdue"
    assert stand["occupant_type"] == "member"
    assert stand["occupant_initials"] == "M"


def test_map_state_hides_stale_hunt(monkeypatch):
    now = datetime(2026, 11, 10, 17, 0, tzinfo=timezone.utc)
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO stands (id, name, type, lat, lng) VALUES (?, ?, ?, ?, ?)",
        ("stand-1", "Old Sit", "ground", 35.0, -78.0),
    )
    conn.execute(
        "INSERT INTO hunts (stand_id, member_id, checked_in_at) VALUES (?, ?, ?)",
        (
            "stand-1",
            "member-2",
            (now - timedelta(days=2)).isoformat(),
        ),
    )
    conn.commit()
    monkeypatch.setattr(main_module, "get_connection", lambda: conn)
    monkeypatch.setattr(main_module, "utc_now", lambda: now)

    response = TestClient(app).get("/api/map-state")
    data = response.json()

    assert data["stands"][0]["status"] == "open"
    assert data["stands"][0]["occupied_by"] is None
    assert data["live_count"] == 0


def test_map_state_returns_safe_host_and_guest_details(monkeypatch):
    now = datetime(2026, 11, 10, 17, 0, tzinfo=timezone.utc)
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute(
        """
        INSERT INTO members (
            id, email, password_hash, first_name, last_name, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "member-1",
            "mike@example.com",
            "not-a-real-hash",
            "Mike",
            "Doe",
            now.isoformat(),
        ),
    )
    conn.executemany(
        "INSERT INTO stands (id, name, type, lat, lng) VALUES (?, ?, ?, ?, ?)",
        [
            ("stand-1", "Host Stand", "ladder", 35.0, -78.0),
            ("stand-2", "Guest Stand", "box", 35.1, -78.1),
        ],
    )
    host_cursor = conn.execute(
        "INSERT INTO hunts (stand_id, member_id, checked_in_at) VALUES (?, ?, ?)",
        ("stand-1", "member-1", (now - timedelta(hours=1)).isoformat()),
    )
    conn.execute(
        """
        INSERT INTO hunts (
            stand_id, member_id, host_hunt_id, checked_in_at,
            guest_name, guest_phone
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "stand-2",
            "member-1",
            host_cursor.lastrowid,
            (now - timedelta(hours=1)).isoformat(),
            "Jane Smith",
            "555-0100",
        ),
    )
    conn.commit()
    monkeypatch.setattr(main_module, "get_connection", lambda: conn)
    monkeypatch.setattr(main_module, "utc_now", lambda: now)

    response = TestClient(app).get("/api/map-state")
    data = response.json()
    host = next(stand for stand in data["stands"] if stand["id"] == "stand-1")
    guest = next(stand for stand in data["stands"] if stand["id"] == "stand-2")

    assert host["occupied_by"] == "Mike D."
    assert host["occupant_initials"] == "MD"
    assert host["occupant_type"] == "member"
    assert host["can_check_out"] is True
    assert host["hunt_id"] == host_cursor.lastrowid
    assert guest["occupied_by"] == "Jane Smith"
    assert guest["occupant_initials"] == "JS"
    assert guest["occupant_type"] == "guest"
    assert guest["guest_of"] == "Mike D."
    assert guest["can_check_out"] is False
    assert "guest_phone" not in guest
    assert data["live_count"] == 2
