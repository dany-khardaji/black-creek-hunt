from datetime import datetime, timezone

from app.database import get_connection
from app.models import CheckInRequest
from app.sessions import is_stand_occupied
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Frontend addresses that are allowed to call this api
origins = [
    "http://localhost:5500",
    "http://127.0.0.1:5500",
    "http://192.168.50.75:5500",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # list of allowed origins
    allow_credentials=True,  # allow cookies and authentication headers
    allow_methods=["*"],  # allow all standard HTTP methods (GET, POST, etc.)
    allow_headers=["*"],  # allow all browser headers
)


# Returns every stand in the database
@app.get("/api/stands")
def list_stands():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM stands").fetchall()
    conn.close()
    return rows


# Returns every map feature (gates, parking, camp, etc.)
@app.get("/api/map-features")
def list_map_features():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM map_features").fetchall()
    conn.close()
    return rows


# Handles a member checking into a stand
@app.post("/api/hunts")
def check_in(request: CheckInRequest):
    conn = get_connection()
    now = datetime.now(timezone.utc)

    # look up the stand being checked into
    stand = conn.execute(
        "SELECT * FROM stands WHERE id = ?", (request.stand_id,)
    ).fetchone()

    if stand is None:
        raise HTTPException(status_code=404, detail="Stand not found")

    # retired stands can never be checked into, occupied or not
    if stand["is_retired"]:
        raise HTTPException(status_code=409, detail="Stand is retired")

    # someone else already has an active session on this stand
    if is_stand_occupied(conn, request.stand_id, now):
        raise HTTPException(status_code=409, detail="Stand is occupied")

    # all checks passed, create the hunt row
    conn.execute(
        "INSERT INTO hunts (stand_id, member_id, checked_in_at) VALUES (?, ?, ?)",
        (request.stand_id, "member-1", now.isoformat()),
    )
    conn.commit()
    conn.close()

    return {"status": "checked in"}
