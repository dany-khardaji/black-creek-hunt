import sqlite3

import app.main as main_module
from app.database import SCHEMA
from app.main import app
from fastapi.testclient import TestClient


def test_retired_stand_rejected(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO stands (id, name, type, lat, lng, is_retired) VALUES (?, ?, ?, ?, ?, ?)",
        ("test-stand-1", "Test Stand 1", "ladder", 35.0, -78.0, 1),
    )
    conn.commit()

    monkeypatch.setattr(main_module, "get_connection", lambda: conn)

    client = TestClient(app)
    response = client.post(
        "/api/hunts", json={"stand_id": "test-stand-1", "guests": []}
    )
    assert response.status_code == 409


def test_checkin_succeeds_after_checkout(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO stands (id, name, type, lat, lng, is_retired) VALUES (?, ?, ?, ?, ?, ?)",
        ("test-stand-1", "Test Stand 1", "ladder", 35.0, -78.0, 0),
    )
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

    client = TestClient(app)
    response = client.post(
        "/api/hunts", json={"stand_id": "test-stand-1", "guests": []}
    )
    assert response.status_code == 200
