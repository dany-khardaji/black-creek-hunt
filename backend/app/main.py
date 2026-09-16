from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from app import auth, config
from app.database import PRIMARY_PROPERTY_ID, get_connection
from app.models import CheckInRequest, LoginRequest
from app.sessions import active_hunt_count, is_hunt_overdue, session_boundary
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# Authentication replaces this development identity in Slice 3.
CURRENT_MEMBER_ID = "member-1"


# Sends someone who is not signed in to the login page. 303 tells the browser to
# ask for that page normally, whatever kind of request it was making.
@app.exception_handler(auth.RedirectToLogin)
def redirect_to_login(request: Request, exc: auth.RedirectToLogin):
    return RedirectResponse(config.LOGIN_PATH, status_code=303)


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


# Shows a guest's full name, or a member as "Mike D." to keep it short.
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


# --- Authentication ---------------------------------------------------------


@app.post("/api/auth/login")
def login(payload: LoginRequest, response: Response):
    conn = get_connection()
    now = utc_now()

    # The password is checked before any write starts. Checking one takes about
    # 25ms, and holding the database open for that would stall check-ins and
    # checkouts happening at the same time.
    try:
        member = auth.find_member_by_email(conn, payload.email)
        stored_hash = member["password_hash"] if member is not None else None
        password_valid = auth.verify_password(payload.password, stored_hash)

        # An unknown email and a wrong password give the same answer on purpose,
        # so nobody can work out who has an account here.
        if member is None or not password_valid:
            raise HTTPException(
                status_code=401,
                detail={
                    "code": "invalid_credentials",
                    "message": "Those sign-in details were not recognized.",
                },
            )

        cursor = conn.execute(
            "UPDATE members SET last_login_at = ? WHERE id = ?",
            (now.isoformat(), member["id"]),
        )

        # Nothing updated means the member was removed since the lookup a moment
        # ago, so no session is handed out.
        if cursor.rowcount != 1:
            raise HTTPException(
                status_code=401,
                detail={
                    "code": "invalid_credentials",
                    "message": "Those sign-in details were not recognized.",
                },
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    auth.set_session_cookie(response, auth.create_session_token(member["id"], now=now))
    return auth.public_member(member)


@app.get("/api/auth/me")
def read_current_member(member=Depends(auth.require_api_member)):
    return auth.public_member(member)


# Not guarded on purpose: someone holding a broken or expired cookie still has
# to be able to clear it.
@app.post("/api/auth/logout")
def logout(response: Response):
    auth.clear_session_cookie(response)
    return {"signed_out": True}


# Lists the properties shown on the homepage.
@app.get("/api/properties")
def list_properties():
    conn = get_connection()
    try:
        return conn.execute(
            """
            SELECT id, slug, name, description, center_lat, center_lng, default_zoom
            FROM properties
            WHERE is_active = 1
            ORDER BY name
            """
        ).fetchall()
    finally:
        conn.close()


# Returns one property, including the map center and zoom its page needs.
@app.get("/api/properties/{slug}")
def get_property(slug: str):
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT id, slug, name, description, center_lat, center_lng, default_zoom
            FROM properties
            WHERE slug = ? AND is_active = 1
            """,
            (slug,),
        ).fetchone()

        if row is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "property_not_found",
                    "message": f"Property {slug} was not found",
                },
            )
        return row
    finally:
        conn.close()


# How many people are out across the whole club, for the homepage counter.
@app.get("/api/live-count")
def get_live_count():
    conn = get_connection()
    boundary = session_boundary(utc_now()).isoformat()

    try:
        live_count = conn.execute(
            """
            SELECT COUNT(*) FROM hunts
            WHERE checked_out_at IS NULL
            AND checked_in_at > ?
            """,
            (boundary,),
        ).fetchone()[0]
        return {"live_count": live_count}
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

        # A hunt happens on one property: a host cannot seat a guest elsewhere.
        property_ids = {stand["property_id"] for stand in rows}
        if len(property_ids) > 1:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "stands_span_properties",
                    "message": "Every stand in a check-in must be on the same property",
                },
            )

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


# Returns everything one property's map needs in one call.
@app.get("/api/map-state")
def get_map_state(property: str = PRIMARY_PROPERTY_ID):
    conn = get_connection()
    now = utc_now()
    boundary = session_boundary(now).isoformat()

    try:
        property_row = conn.execute(
            "SELECT id FROM properties WHERE slug = ? AND is_active = 1",
            (property,),
        ).fetchone()
        # An unknown property name is refused rather than quietly showing the
        # main one, which would put the wrong stands and locations on the map.
        if property_row is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "property_not_found",
                    "message": f"Property {property} was not found",
                },
            )
        property_id = property_row["id"]

        stands = conn.execute(
            """
            SELECT * FROM stands
            WHERE is_retired = 0 AND property_id = ?
            ORDER BY name
            """,
            (property_id,),
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

        features = conn.execute(
            "SELECT * FROM map_features WHERE property_id = ?", (property_id,)
        ).fetchall()
        # Scoped to this property: the map shows who is out here, not club-wide.
        live_count = conn.execute(
            """
            SELECT COUNT(*) FROM hunts
            JOIN stands ON stands.id = hunts.stand_id
            WHERE hunts.checked_out_at IS NULL
            AND hunts.checked_in_at > ?
            AND stands.property_id = ?
            """,
            (boundary, property_id),
        ).fetchone()[0]

        return {
            "stands": stand_states,
            "map_features": features,
            "live_count": live_count,
        }
    finally:
        conn.close()


# --- Static pages -----------------------------------------------------------
# Pages are served by this same app so the site and its API share one address.
# They must stay below the /api routes, because the catch-all at the bottom of
# this file would otherwise swallow them.

FRONTEND = Path(__file__).parent.parent.parent / "frontend"


@app.get("/", include_in_schema=False)
def home_page():
    return FileResponse(FRONTEND / "home" / "index.html")


@app.get("/login", include_in_schema=False)
def login_page():
    return FileResponse(FRONTEND / "login" / "index.html")


# One page serves every property; property.js reads the slug from the path.
@app.get("/property/{slug}", include_in_schema=False)
def property_page(slug: str):
    return FileResponse(FRONTEND / "property" / "index.html")


# Files live under /static/ so that /property/anything does not accidentally
# match a stylesheet and return the page instead.
app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
