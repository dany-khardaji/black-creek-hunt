from datetime import datetime, timedelta, timezone

import jwt
from app import config
from fastapi import HTTPException, Request
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from sqlalchemy import text

_password_hash = PasswordHash((Argon2Hasher(),))

# Checking a password is slow on purpose. An account with no password still gets
# checked against this, so a sign-in attempt takes the same time either way and
# the delay never gives away who has an account.
_DUMMY_PASSWORD_HASH = _password_hash.hash(
    "dummy-password-used-only-to-equalize-login-time"
)


def hash_password(password):
    return _password_hash.hash(password)


def verify_password(password, stored_hash):
    candidate_hash = stored_hash or _DUMMY_PASSWORD_HASH

    try:
        matches = _password_hash.verify(password, candidate_hash)
    except Exception:
        # A damaged stored value fails, but still pays the usual cost so it does
        # not answer faster than a real one.
        _password_hash.verify(password, _DUMMY_PASSWORD_HASH)
        return False

    # Typing the dummy password must never sign anyone in.
    return bool(stored_hash) and matches


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
    return (
        conn.execute(
            text("SELECT * FROM members WHERE id = :id"),
            {"id": member_id},
        )
        .mappings()
        .fetchone()
    )


def find_member_by_email(conn, email):
    return (
        conn.execute(
            text("SELECT * FROM members WHERE email = :email"),
            {"email": normalize_email(email)},
        )
        .mappings()
        .fetchone()
    )


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


def find_member_by_google_sub(conn, google_sub):
    return (
        conn.execute(
            text("SELECT * FROM members WHERE google_sub = :google_sub"),
            {"google_sub": google_sub},
        )
        .mappings()
        .fetchone()
    )


def link_google_account(conn, member_id, google_sub):
    conn.execute(
        text("UPDATE members SET google_sub = :google_sub WHERE id = :id"),
        {"google_sub": google_sub, "id": member_id},
    )


# Finds the member behind a Google sign-in, or None if they are not in the club.
# Google proving who someone is does not make them a member; the allowlist does.
def member_for_google_claims(conn, google_sub, email, email_verified):
    if not google_sub:
        return None

    # Matched on Google's subject id, which never changes. An address can be
    # reassigned; the id cannot, so it is checked first and on its own.
    member = find_member_by_google_sub(conn, google_sub)
    if member is not None:
        return member

    # First Google sign-in for an existing password account. The email is only
    # trusted when Google says it verified it, or an unverified address could
    # claim someone else's account.
    if not email_verified:
        return None

    member = find_member_by_email(conn, email)
    if member is None:
        return None

    # A member already tied to a different Google account is never relinked.
    if member["google_sub"]:
        return None

    link_google_account(conn, member["id"], google_sub)
    return find_member_by_google_sub(conn, google_sub)
