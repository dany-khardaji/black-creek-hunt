from datetime import datetime, timedelta, timezone

import jwt
from app import config
from fastapi import HTTPException, Request
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

_password_hash = PasswordHash((Argon2Hasher(),))


def hash_password(password):
    return _password_hash.hash(password)


def verify_password(password, stored_hash):
    # A member with no password set, or a damaged stored value, is a failed
    # sign-in rather than a server error.
    if not stored_hash:
        return False

    try:
        return _password_hash.verify(password, stored_hash)
    except Exception:
        return False


# Email is stored and compared in one form, so capital letters or stray spaces
# in what a member types never stop them signing in.
def normalize_email(email):
    return (email or "").strip().lower()


def create_session_token(member_id, now=None):
    # The token says who you are, not what you may do. Admin rights are read
    # from the member's row each time, so removing them takes effect at once.
    issued_at = now or datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(minutes=config.JWT_EXPIRE_MINUTES)

    payload = {
        "sub": member_id,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
    }

    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def decode_session_token(token):
    # Anything wrong with the token - expired, edited, incomplete, or signed
    # by someone else - is treated the same as not being signed in.
    if not token:
        return None

    try:
        payload = jwt.decode(
            token,
            config.JWT_SECRET,
            algorithms=[config.JWT_ALGORITHM],
            # Listing what must be present: a missing expiry is only checked
            # if it is there, so without this a token could last forever.
            options={"require": ["sub", "iat", "exp"]},
        )
    except jwt.PyJWTError:
        return None

    member_id = payload.get("sub")
    return member_id or None


def set_session_cookie(response, token):
    # "lax" not "strict": signing in with Google sends the browser back from
    # Google's site, and "strict" would hold the cookie back on that trip.
    response.set_cookie(
        config.SESSION_COOKIE_NAME,
        token,
        max_age=config.JWT_EXPIRE_MINUTES * 60,
        httponly=True,
        samesite="lax",
        secure=config.SESSION_COOKIE_SECURE,
        path="/",
    )


def clear_session_cookie(response):
    # These settings must match set_session_cookie above, or some browsers
    # keep the old cookie and signing out does nothing.
    response.delete_cookie(
        config.SESSION_COOKIE_NAME,
        httponly=True,
        samesite="lax",
        secure=config.SESSION_COOKIE_SECURE,
        path="/",
    )


def load_member(conn, member_id):
    return conn.execute(
        "SELECT * FROM members WHERE id = ?",
        (member_id,),
    ).fetchone()


def find_member_by_email(conn, email):
    return conn.execute(
        "SELECT * FROM members WHERE email = ?",
        (normalize_email(email),),
    ).fetchone()


# Every response that includes a member goes through here. Emails, phone
# numbers, password hashes, and Google IDs are left out on purpose.
def public_member(row):
    return {
        "id": row["id"],
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "is_admin": bool(row["is_admin"]),
    }


# Signals that a page needs signing in. main.py turns this into a redirect.
class RedirectToLogin(Exception):
    pass


def _resolve_member(request: Request):
    # Imported here, not at the top of the file: the tests swap the database by
    # replacing main.get_connection, and a top-level import would miss that and
    # read the real one instead. Slice 6 replaces this with a proper dependency.
    from app import main

    member_id = decode_session_token(request.cookies.get(config.SESSION_COOKIE_NAME))
    if member_id is None:
        return None

    # The member is looked up every time rather than trusted from the token, so
    # a removed member stops being signed in immediately.
    conn = main.get_connection()
    try:
        return load_member(conn, member_id)
    finally:
        conn.close()


def current_member_or_none(request: Request):
    return _resolve_member(request)


def require_api_member(request: Request):
    member = _resolve_member(request)

    if member is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "not_authenticated", "message": "Sign in to continue."},
        )

    return member


def require_page_member(request: Request):
    member = _resolve_member(request)

    if member is None:
        raise RedirectToLogin()

    return member
