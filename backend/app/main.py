from collections import Counter
from datetime import datetime, timezone

from app.database import get_connection
from app.models import CheckInRequest
from app.sessions import active_hunt_count, is_hunt_overdue, session_boundary
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Authentication replaces this development identity in Slice 3.
CURRENT_MEMBER_ID = "member-1"

# Frontend addresses that are allowed to call this API during local development.
origins = [
    "http://localhost:5500",
    "http://127.0.0.1:5500",
    "http://192.168.50.75:5500",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def error_detail(code, message, stand=None):
    detail = {"code": code, "message": message}
    if stand is not None:
        detail["stand_id"] = stand["id"]
        detail["stand_name"] = stand["name"]
    return detail


def missing_stand_detail(stand_id):
    return {
        "code": "stand_not_found",
        "message": f"Stand {stand_id} was not found",
        "stand_id": stand_id,
    }


# Will probably be edited later
def display_name(active_hunt):
    if active_hunt["guest_name"]:
        return active_hunt["guest_name"]

    if active_hunt["first_name"]:
        last_initial = (
            f" {active_hunt['last_name'][0]}." if active_hunt["last_name"] else ""
        )
        return f"{active_hunt['first_name']}{last_initial}"

    return active_hunt["member_id"]


def initials(name):
    parts = [part for part in name.replace(".", "").split() if part]
    return "".join(part[0].upper() for part in parts[:2])


def utc_now():
    return datetime.now(timezone.utc)


# Returns every non-retired stand in the database.
@app.get("/api/stands")
def list_stands():
    conn = get_connection()
    try:
        return conn.execute("SELECT * FROM stands WHERE is_retired = 0").fetchall()
    finally:
        conn.close()


# Returns every map feature (gates, parking, camp, etc.).
@app.get("/api/map-features")
def list_map_features():
    conn = get_connection()
    try:
        return conn.execute("SELECT * FROM map_features").fetchall()
    finally:
        conn.close()


# Handles a member checking into a stand.
@app.post("/api/hunts")
def check_in(request: CheckInRequest):
    conn = get_connection()
    now = utc_now()
    requested_seats = Counter(
        [request.stand_id, *(guest.stand_id for guest in request.guests)]
    )
    requested_ids = sorted(requested_seats)

    try:
        # SQLite grants one writer the lock before any occupancy checks run.
        conn.execute("BEGIN IMMEDIATE")

        placeholders = ", ".join("?" for _ in requested_ids)
        rows = conn.execute(
            f"SELECT * FROM stands WHERE id IN ({placeholders}) ORDER BY id",
            requested_ids,
        ).fetchall()
        stands_by_id = {stand["id"]: stand for stand in rows}

        # Validate every requested stand before writing the host or any guest row.
        for stand_id in requested_ids:
            stand = stands_by_id.get(stand_id)
            if stand is None:
                raise HTTPException(
                    status_code=404, detail=missing_stand_detail(stand_id)
                )
            if stand["is_retired"]:
                raise HTTPException(
                    status_code=409,
                    detail=error_detail(
                        "stand_retired", f"{stand['name']} is retired", stand
                    ),
                )
            occupied_count = active_hunt_count(conn, stand_id, now)
            available_seats = max(stand["capacity"] - occupied_count, 0)
            seats_needed = requested_seats[stand_id]

            if seats_needed > available_seats:
                if available_seats == 0 and seats_needed == 1:
                    raise HTTPException(
                        status_code=409,
                        detail=error_detail(
                            "stand_occupied", f"{stand['name']} is occupied", stand
                        ),
                    )

                seat_word = "seat" if available_seats == 1 else "seats"
                verb = "was" if seats_needed == 1 else "were"
                detail = error_detail(
                    "stand_capacity_exceeded",
                    f"{stand['name']} has {available_seats} {seat_word} available, "
                    f"but {seats_needed} {verb} requested",
                    stand,
                )
                detail.update(
                    {
                        "capacity": stand["capacity"],
                        "occupied_count": occupied_count,
                        "requested_seats": seats_needed,
                        "available_seats": available_seats,
                    }
                )
                raise HTTPException(
                    status_code=409,
                    detail=detail,
                )

        host_cursor = conn.execute(
            "INSERT INTO hunts (stand_id, member_id, checked_in_at) VALUES (?, ?, ?)",
            (request.stand_id, CURRENT_MEMBER_ID, now.isoformat()),
        )
        host_hunt_id = host_cursor.lastrowid

        for guest in request.guests:
            conn.execute(
                """
                INSERT INTO hunts (
                    stand_id, member_id, host_hunt_id, checked_in_at,
                    guest_name, guest_phone
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    guest.stand_id,
                    CURRENT_MEMBER_ID,
                    host_hunt_id,
                    now.isoformat(),
                    guest.name,
                    guest.phone,
                ),
            )

        conn.commit()
        return {"status": "checked in", "host_hunt_id": host_hunt_id}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Checks out a member's own host hunt and every guest linked to it.
@app.post("/api/hunts/{hunt_id}/check-out")
def check_out(hunt_id: int):
    conn = get_connection()
    now = utc_now()

    try:
        conn.execute("BEGIN IMMEDIATE")
        hunt = conn.execute("SELECT * FROM hunts WHERE id = ?", (hunt_id,)).fetchone()

        if hunt is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "hunt_not_found", "message": "Hunt not found"},
            )
        if hunt["member_id"] != CURRENT_MEMBER_ID:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "checkout_forbidden",
                    "message": "You can only check out your own hunt",
                },
            )
        if hunt["host_hunt_id"] is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "guest_checkout_forbidden",
                    "message": "Guests are checked out with their host",
                },
            )
        if hunt["checked_out_at"] is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "hunt_already_checked_out",
                    "message": "Hunt already checked out",
                },
            )
        if datetime.fromisoformat(hunt["checked_in_at"]) <= session_boundary(now):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "hunt_not_active",
                    "message": "This hunt is no longer active",
                },
            )

        checked_out_at = now.isoformat()
        conn.execute(
            """
            UPDATE hunts
            SET checked_out_at = ?, checkout_source = 'member'
            WHERE id = ? OR host_hunt_id = ?
            """,
            (checked_out_at, hunt_id, hunt_id),
        )
        conn.commit()
        return {"status": "checked out", "checked_out_at": checked_out_at}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Returns everything the map needs in one call.
@app.get("/api/map-state")
def get_map_state():
    conn = get_connection()
    now = utc_now()
    boundary = session_boundary(now).isoformat()

    try:
        stands = conn.execute(
            "SELECT * FROM stands WHERE is_retired = 0 ORDER BY name"
        ).fetchall()
        stand_states = []

        for stand in stands:
            active_hunts = conn.execute(
                """
                SELECT hunts.*, members.first_name, members.last_name
                FROM hunts
                LEFT JOIN members ON members.id = hunts.member_id
                WHERE hunts.stand_id = ?
                AND hunts.checked_out_at IS NULL
                AND hunts.checked_in_at > ?
                ORDER BY
                    CASE WHEN hunts.guest_name IS NULL THEN 0 ELSE 1 END,
                    hunts.checked_in_at,
                    hunts.id
                """,
                (stand["id"], boundary),
            ).fetchall()

            if not active_hunts:
                stand_states.append(
                    {
                        "id": stand["id"],
                        "name": stand["name"],
                        "type": stand["type"],
                        "lat": stand["lat"],
                        "lng": stand["lng"],
                        "capacity": stand["capacity"],
                        "occupied_count": 0,
                        "available_seats": stand["capacity"],
                        "occupants": [],
                        "status": "open",
                        "occupied_by": None,
                        "occupant_initials": None,
                        "occupant_type": None,
                        "guest_of": None,
                        "checked_in_at": None,
                        "can_check_out": False,
                        "hunt_id": None,
                    }
                )
                continue

            occupants = []
            for active_hunt in active_hunts:
                occupant_name = display_name(active_hunt)
                member_data = {**dict(active_hunt), "guest_name": None}
                member_name = display_name(member_data)
                is_guest = active_hunt["guest_name"] is not None
                can_check_out = (
                    not is_guest and active_hunt["member_id"] == CURRENT_MEMBER_ID
                )
                occupants.append(
                    {
                        "display_name": occupant_name,
                        "initials": initials(occupant_name),
                        "occupant_type": "guest" if is_guest else "member",
                        "guest_of": member_name if is_guest else None,
                        "checked_in_at": active_hunt["checked_in_at"],
                        "can_check_out": can_check_out,
                        "hunt_id": active_hunt["id"] if can_check_out else None,
                    }
                )

            primary_occupant = occupants[0]
            checkout_occupant = next(
                (occupant for occupant in occupants if occupant["can_check_out"]), None
            )
            occupied_count = len(occupants)

            stand_states.append(
                {
                    "id": stand["id"],
                    "name": stand["name"],
                    "type": stand["type"],
                    "lat": stand["lat"],
                    "lng": stand["lng"],
                    "capacity": stand["capacity"],
                    "occupied_count": occupied_count,
                    "available_seats": max(stand["capacity"] - occupied_count, 0),
                    "occupants": occupants,
                    "status": "overdue"
                    if any(
                        is_hunt_overdue(active_hunt["checked_in_at"], now)
                        for active_hunt in active_hunts
                    )
                    else "active",
                    "occupied_by": ", ".join(
                        occupant["display_name"] for occupant in occupants
                    ),
                    "occupant_initials": primary_occupant["initials"],
                    "occupant_type": primary_occupant["occupant_type"],
                    "guest_of": primary_occupant["guest_of"],
                    "checked_in_at": primary_occupant["checked_in_at"],
                    "can_check_out": checkout_occupant is not None,
                    "hunt_id": checkout_occupant["hunt_id"]
                    if checkout_occupant
                    else None,
                }
            )

        features = conn.execute("SELECT * FROM map_features").fetchall()
        live_count = conn.execute(
            """
            SELECT COUNT(*) FROM hunts
            WHERE checked_out_at IS NULL
            AND checked_in_at > ?
            """,
            (boundary,),
        ).fetchone()[0]

        return {
            "stands": stand_states,
            "map_features": features,
            "live_count": live_count,
        }
    finally:
        conn.close()
