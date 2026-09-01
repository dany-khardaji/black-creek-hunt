import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import app.main as main_module  # The module holding get_connection, so we can swap it out
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
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    conn.execute(
        "INSERT INTO stands (id, name, type, lat, lng, is_retired) VALUES (?, ?, ?, ?, ?, ?)",
        ("test-stand-1", "Test Stand 1", "ladder", 35.0, -78.0, 0),
    )
    conn.commit()

    monkeypatch.setattr(main_module, "get_connection", lambda: conn)

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
