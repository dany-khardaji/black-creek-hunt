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
    """Check a password against a stored hash, never raising.

    password_hash is null for members who only sign in with Google, and test
    fixtures seed a placeholder string. Both must read as a failed sign-in
    rather than a 500 that exposes a stack trace.
    """
    if not stored_hash:
        return False

    try:
        return _password_hash.verify(password, stored_hash)
    except Exception:
        return False


def normalize_email(email):
    """The single place email casing and padding are decided.

    Applied on insert and on lookup, so a member who types Mike@Example.com
    signs in. Keeping this in Python rather than SQL means SQLite and Postgres
    agree without relying on either one's collation.
    """
    return (email or "").strip().lower()


def create_session_token(member_id, now=None):
    """Sign a session token for a member.

    The payload is deliberately only sub/exp/iat. An is_admin claim would stay
    stale for the life of the token, so authorization reads is_admin from the
    member row on each request instead.
    """
    issued_at = now or datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(minutes=config.JWT_EXPIRE_MINUTES)

    payload = {
        "sub": member_id,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
    }

    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def decode_session_token(token):
    """Return the member id in a valid token, or None.

    algorithms is passed explicitly: without it a token claiming "alg": "none"
    would be accepted unsigned. The claims are required rather than merely
    checked when present: exp is only enforced if it exists, so a signed token
    carrying just a subject would otherwise never expire. Every failure -
    expired, tampered, malformed, incomplete, wrong signature - is the same
    None, because the caller treats them all as "not signed in".
    """
    if not token:
        return None

    try:
        payload = jwt.decode(
            token,
            config.JWT_SECRET,
            algorithms=[config.JWT_ALGORITHM],
            options={"require": ["sub", "iat", "exp"]},
        )
    except jwt.PyJWTError:
        return None

    member_id = payload.get("sub")
    return member_id or None


def set_session_cookie(response, token):
    """Attach the session cookie.

    SameSite=Lax rather than Strict: the Google sign-in commit returns the
    browser from Google's domain by redirect, and Strict withholds the cookie
    on that landing, which reads as a failed sign-in and loops.
    """
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
    """Remove the session cookie.

    The attributes must mirror set_session_cookie exactly. A delete that
    differs on path, samesite, or secure leaves the original cookie in place
    in some browsers, so signing out would appear to do nothing.
    """
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


def public_member(row):
    """The only shape a member is allowed to leave the process in.

    PLAN.md excludes phone numbers, emails, password hashes, and Google subject
    IDs from every response, so none of them appear here. Every response that
    carries member details goes through this function so that rule lives in one
    place rather than being re-decided per route.
    """
    return {
        "id": row["id"],
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "is_admin": bool(row["is_admin"]),
    }


class RedirectToLogin(Exception):
    """Raised by require_page_member. main.py turns this into a 303."""


def _resolve_member(request: Request):
    """Look up the member for a request's session cookie, or None.

    The token is not trusted on its own: a member deleted or revoked since
    sign-in must stop being authenticated before the token expires, so the row
    is read on every request.

    The import is deliberately inside the function. conftest.py redirects the
    test database by patching main.get_connection, and a module-level import
    here would bind the real one - authenticated tests would then read the
    production database while routes read the temp file. Slice 6 replaces this
    with a proper connection dependency on the routes.
    """
    from app import main

    member_id = decode_session_token(request.cookies.get(config.SESSION_COOKIE_NAME))
    if member_id is None:
        return None

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
