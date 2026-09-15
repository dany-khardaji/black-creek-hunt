import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import app.main as main_module  # The module holding get_connection, so we can swap it out
import pytest
from app.database import (  # CREATE TABLE statements, so test DBs match production
    PRIMARY_PROPERTY_ID,
    SCHEMA,
    migrate_members_for_auth,
)
from app.main import app  # The actual FastAPI app we're testing
from fastapi.testclient import TestClient  # Lets us send fake HTTP requests to that app


def seed_primary_property(conn):
    """Give a test database the property that stands default to.

    /api/map-state refuses an unknown slug, so any test reading it needs the
    property row to exist even when the test is not about properties.
    """
    conn.execute(
        """
        INSERT OR IGNORE INTO properties (
            id, slug, name, center_lat, center_lng, default_zoom
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (PRIMARY_PROPERTY_ID, PRIMARY_PROPERTY_ID, "Black Creek", 35.0, -78.0, 15),
    )


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
    seed_primary_property(conn)

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


def test_guest_can_share_host_stand_when_capacity_allows(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO stands (id, name, type, lat, lng, capacity) VALUES (?, ?, ?, ?, ?, ?)",
        ("stand-1", "Double Stand", "box", 35.0, -78.0, 2),
    )
    conn.commit()
    monkeypatch.setattr(main_module, "get_connection", lambda: conn)

    response = TestClient(app).post(
        "/api/hunts",
        json={
            "stand_id": "stand-1",
            "guests": [
                {"name": "Guest A", "phone": "555-0100", "stand_id": "stand-1"}
            ],
        },
    )

    assert response.status_code == 200


def test_two_guests_can_share_a_two_seat_stand(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO stands (id, name, type, lat, lng, capacity) VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("stand-1", "Host Stand", "ladder", 35.0, -78.0, 1),
            ("stand-2", "Double Stand", "box", 35.1, -78.1, 2),
        ],
    )
    conn.commit()
    monkeypatch.setattr(main_module, "get_connection", lambda: conn)

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

    assert response.status_code == 200


def test_checkin_over_capacity_rejects_every_row(monkeypatch, tmp_path):
    db_path = tmp_path / "capacity_rollback.db"
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO stands (id, name, type, lat, lng, capacity) VALUES (?, ?, ?, ?, ?, ?)",
        ("stand-1", "Double Stand", "box", 35.0, -78.0, 2),
    )
    conn.commit()
    conn.close()

    def fake_get_connection():
        connection = sqlite3.connect(db_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(main_module, "get_connection", fake_get_connection)
    response = TestClient(app).post(
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

    check_conn = sqlite3.connect(db_path)
    row_count = check_conn.execute("SELECT COUNT(*) FROM hunts").fetchone()[0]
    check_conn.close()
    assert row_count == 0


def test_concurrent_checkins_only_one_claims_final_seat(monkeypatch, tmp_path):
    db_path = tmp_path / "capacity_concurrent.db"
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO stands (id, name, type, lat, lng, capacity) VALUES (?, ?, ?, ?, ?, ?)",
        ("stand-1", "Double Stand", "box", 35.0, -78.0, 2),
    )
    conn.execute(
        "INSERT INTO hunts (stand_id, member_id, checked_in_at) VALUES (?, ?, ?)",
        ("stand-1", "member-2", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()

    def fake_get_connection():
        connection = sqlite3.connect(db_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(main_module, "get_connection", fake_get_connection)
    results = []

    def make_request():
        response = TestClient(app).post(
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
    check_conn = sqlite3.connect(db_path)
    active_count = check_conn.execute(
        "SELECT COUNT(*) FROM hunts WHERE checked_out_at IS NULL"
    ).fetchone()[0]
    check_conn.close()
    assert active_count == 2


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
    seed_primary_property(conn)
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
    seed_primary_property(conn)
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
    seed_primary_property(conn)
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


def test_map_state_returns_capacity_and_both_occupants(monkeypatch):
    now = datetime(2026, 11, 10, 17, 0, tzinfo=timezone.utc)
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    seed_primary_property(conn)
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
    conn.execute(
        "INSERT INTO stands (id, name, type, lat, lng, capacity) VALUES (?, ?, ?, ?, ?, ?)",
        ("stand-1", "Double Stand", "box", 35.0, -78.0, 2),
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
            "stand-1",
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
def test_map_state_rejects_unknown_property(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    seed_primary_property(conn)
    conn.execute(
        "INSERT INTO stands (id, name, type, lat, lng) VALUES (?, ?, ?, ?, ?)",
        ("stand-1", "Creek Stand", "ladder", 35.0, -78.0),
    )
    conn.commit()
    monkeypatch.setattr(main_module, "get_connection", lambda: conn)

    response = TestClient(app).get("/api/map-state?property=no-such-property")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "property_not_found"
    # No stand coordinates may leak through the refusal.
    assert "stands" not in response.json()


# An inactive property is refused the same way an unknown slug is.
def test_map_state_rejects_inactive_property(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute(
        """
        INSERT INTO properties (
            id, slug, name, center_lat, center_lng, default_zoom, is_active
        ) VALUES (?, ?, ?, ?, ?, ?, 0)
        """,
        ("retired-tract", "retired-tract", "Retired Tract", 35.0, -78.0, 15),
    )
    conn.commit()
    monkeypatch.setattr(main_module, "get_connection", lambda: conn)

    response = TestClient(app).get("/api/map-state?property=retired-tract")

    assert response.status_code == 404


# Google members have no password, so the column must accept NULL.
def test_members_table_accepts_google_only_member():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

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
def test_members_table_rejects_duplicate_google_sub():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
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
def test_members_table_allows_many_null_google_subs():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
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
    conn.executescript(SCHEMA.replace("CREATE TABLE IF NOT EXISTS members", "CREATE TABLE IF NOT EXISTS members_unused"))
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
        ("m2", "gale@example.com", "108xyz", "Gale", "Ray", "2026-09-15T00:00:00+00:00"),
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


# A table that already has google_sub but predates last_login_at gets the
# column added in place. Rebuilding would risk the Google subject already
# stored on the row, and returning early would leave auth writing to a
# column that does not exist.
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
