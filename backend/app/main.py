from datetime import datetime, timezone

from app.database import get_connection
from app.models import CheckInRequest
from app.sessions import is_stand_occupied
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()


origins = [
    "http://localhost:5500",
    "http://127.0.0.1:5500",
    "http://192.168.50.75:5500",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # List of allowed origins
    allow_credentials=True,  # Allow cookies and authentication headers
    allow_methods=["*"],  # Allow all standard HTTP methods (GET, POST, etc.)
    allow_headers=["*"],  # Allow all browser headers
)


@app.get("/api/stands")
def list_stands():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM stands").fetchall()
    conn.close()
    return rows


@app.get("/api/map-features")
def list_map_features():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM map_features").fetchall()
    conn.close()
    return rows


@app.post("/api/hunts")
def check_in(request: CheckInRequest):
    conn = get_connection()
    now = datetime.now(timezone.utc)

    stand = conn.execute(
        "SELECT * FROM stands WHERE id = ?", (request.stand_id,)
    ).fetchone()

    if stand is None:
        raise HTTPException(status_code=404, detail="Stand not found")

    if is_stand_occupied(conn, request.stand_id, now):
        raise HTTPException(status_code=409, detail="Stand is occupied")

    conn.execute(
        "INSERT INTO hunts (stand_id, member_id, checked_in_at) VALUES (?, ?, ?)",
        (request.stand_id, "member-1", now.isoformat()),
    )
    conn.commit()
    conn.close()

    return {"status": "checked in"}
