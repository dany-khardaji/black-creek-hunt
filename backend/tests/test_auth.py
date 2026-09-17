from datetime import datetime, timedelta, timezone

import jwt
import pytest
from app import auth, config
from conftest import seed_member
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


# A hashed password is not recoverable from the hash
def test_hash_does_not_contain_the_password():
    digest = auth.hash_password("swamp-oak-42")

    assert "swamp-oak-42" not in digest
    assert digest.startswith("$argon2")


# The correct password verifies against its own hash
def test_correct_password_verifies():
    digest = auth.hash_password("swamp-oak-42")

    assert auth.verify_password("swamp-oak-42", digest) is True


# A wrong password does not verify
def test_wrong_password_does_not_verify():
    digest = auth.hash_password("swamp-oak-42")

    assert auth.verify_password("swamp-oak-43", digest) is False


# Hashing is salted, so the same password twice gives different hashes
def test_same_password_hashes_differently():
    assert auth.hash_password("swamp-oak-42") != auth.hash_password("swamp-oak-42")


# A member who only signs in with Google has a null hash: that is a failed
# sign-in, not a crash
def test_null_hash_fails_cleanly():
    assert auth.verify_password("anything", None) is False


# Test fixtures seed a placeholder hash, which must not raise either
@pytest.mark.parametrize("stored", ["", "not-a-real-hash", "$argon2id$broken"])
def test_malformed_hash_fails_cleanly(stored):
    assert auth.verify_password("anything", stored) is False


# Email is compared case-insensitively and without padding
@pytest.mark.parametrize(
    "raw",
    [
        "mike@example.com",
        "Mike@Example.com",
        "  MIKE@EXAMPLE.COM  ",
        "\tmike@example.com\n",
    ],
)
def test_email_normalizes_to_one_form(raw):
    assert auth.normalize_email(raw) == "mike@example.com"


# A missing email normalizes to empty rather than raising
def test_none_email_normalizes_to_empty():
    assert auth.normalize_email(None) == ""


# A freshly signed token decodes back to the member who owns it
def test_token_round_trips():
    token = auth.create_session_token("member-1")

    assert auth.decode_session_token(token) == "member-1"


# The token carries only sub/exp/iat: an is_admin claim would stay stale for
# the life of the token after a demotion
def test_token_payload_carries_no_authorization():
    token = auth.create_session_token("member-1")

    payload = jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])

    assert set(payload) == {"sub", "iat", "exp"}


# An expired token is rejected
def test_expired_token_is_rejected():
    issued = datetime.now(timezone.utc) - timedelta(
        minutes=config.JWT_EXPIRE_MINUTES + 1
    )

    token = auth.create_session_token("member-1", now=issued)

    assert auth.decode_session_token(token) is None


# A complete, working token payload, so each test below can break exactly one
# thing and know that is why it failed.
def valid_payload(**overrides):
    now = int(datetime.now(timezone.utc).timestamp())
    payload = {"sub": "member-1", "iat": now, "exp": now + 60}
    payload.update(overrides)
    return payload


# The baseline this file's forgeries are built from is itself accepted, so a
# rejection below is caused by the change that test makes and nothing else
def test_valid_payload_baseline_is_accepted():
    token = jwt.encode(
        valid_payload(), config.JWT_SECRET, algorithm=config.JWT_ALGORITHM
    )

    assert auth.decode_session_token(token) == "member-1"


# A token signed with a different secret is rejected
def test_token_signed_with_another_secret_is_rejected():
    forged = jwt.encode(
        valid_payload(),
        "a-different-secret-long-enough-to-not-warn",
        algorithm="HS256",
    )

    assert auth.decode_session_token(forged) is None


# A token that claims to need no signature at all is rejected
def test_unsigned_token_is_rejected():
    forged = jwt.encode(valid_payload(), None, algorithm="none")

    assert auth.decode_session_token(forged) is None


# Garbage in the cookie is treated as signed out, not as a server error
@pytest.mark.parametrize("value", [None, "", "not-a-token", "a.b.c", "....."])
def test_malformed_token_is_rejected(value):
    assert auth.decode_session_token(value) is None


# A token with no subject is not a session
def test_token_without_subject_is_rejected():
    payload = valid_payload()
    del payload["sub"]

    token = jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)

    assert auth.decode_session_token(token) is None


# A token missing any required part is rejected. Without this, one with no
# expiry date would work forever.
@pytest.mark.parametrize("missing", ["sub", "iat", "exp"])
def test_token_missing_a_required_claim_is_rejected(missing):
    payload = valid_payload()
    del payload[missing]

    token = jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)

    assert auth.decode_session_token(token) is None


# The tokens this module mints carry every claim it requires back
def test_minted_token_satisfies_its_own_requirements():
    assert (
        auth.decode_session_token(auth.create_session_token("member-1")) == "member-1"
    )


# The member row is found by id
def test_load_member_reads_the_row(conn):
    seed_member(conn, "member-7", email="seven@example.com")

    assert auth.load_member(conn, "member-7")["email"] == "seven@example.com"


# An unknown id is None rather than an error
def test_load_member_returns_none_for_unknown_id(conn):
    assert auth.load_member(conn, "nobody") is None


# Lookup normalizes, so the casing a member types does not matter
@pytest.mark.parametrize(
    "typed", ["mike@example.com", "Mike@Example.COM", "  MIKE@EXAMPLE.COM "]
)
def test_find_member_by_email_normalizes(conn, typed):
    seed_member(conn, "member-7", email="mike@example.com")

    assert auth.find_member_by_email(conn, typed)["id"] == "member-7"


# An email that is not on the allowlist is None
def test_find_member_by_email_returns_none_for_unknown(conn):
    assert auth.find_member_by_email(conn, "stranger@example.com") is None


# PLAN.md excludes emails, phone numbers, password hashes, and Google subject
# IDs from every response
def test_public_member_omits_secrets(conn):
    seed_member(
        conn, "member-7", email="seven@example.com", password_hash="$argon2-secret"
    )
    row = auth.load_member(conn, "member-7")

    public = auth.public_member(row)

    assert set(public) == {"id", "first_name", "last_name", "is_admin"}
    assert "$argon2-secret" not in repr(public)
    assert "seven@example.com" not in repr(public)


# is_admin is stored as 0/1 but must reach the frontend as a real boolean
@pytest.mark.parametrize("stored,expected", [(0, False), (1, True)])
def test_public_member_exposes_is_admin_as_bool(conn, stored, expected):
    seed_member(conn, "member-7", is_admin=stored)
    row = auth.load_member(conn, "member-7")

    assert auth.public_member(row)["is_admin"] is expected


# A throwaway app for testing the guards the way real routes use them, which
# catches wiring mistakes that calling them directly would not.
def guarded_probe_app(dependency):
    probe = FastAPI()

    @probe.get("/guarded")
    def guarded(member=Depends(dependency)):
        return auth.public_member(member)

    return probe


# No cookie is an unauthenticated API call, not a malformed one
def test_api_guard_refuses_without_a_cookie(client):
    probe = TestClient(guarded_probe_app(auth.require_api_member))

    assert probe.get("/guarded").status_code == 401


# A signed cookie reaches the route with the member it names
def test_api_guard_accepts_a_signed_cookie(client, conn):
    seed_member(conn, "member-7", email="seven@example.com")
    probe = TestClient(guarded_probe_app(auth.require_api_member))
    probe.cookies.set(config.SESSION_COOKIE_NAME, auth.create_session_token("member-7"))

    response = probe.get("/guarded")

    assert response.status_code == 200
    assert response.json()["id"] == "member-7"


# Garbage in the cookie is signed out, not a server error
def test_api_guard_refuses_a_garbage_cookie(client):
    probe = TestClient(guarded_probe_app(auth.require_api_member))
    probe.cookies.set(config.SESSION_COOKIE_NAME, "not-a-real-token")

    assert probe.get("/guarded").status_code == 401


# A token that outlived its member is refused: this is why the guard reads the
# row on every request instead of trusting the token's subject
def test_api_guard_refuses_a_token_for_a_deleted_member(client):
    probe = TestClient(guarded_probe_app(auth.require_api_member))
    probe.cookies.set(
        config.SESSION_COOKIE_NAME, auth.create_session_token("deleted-member")
    )

    assert probe.get("/guarded").status_code == 401


# The page guard raises for a redirect instead of answering 401
def test_page_guard_raises_redirect_without_a_cookie(client):
    probe = TestClient(guarded_probe_app(auth.require_page_member))

    with pytest.raises(auth.RedirectToLogin):
        probe.get("/guarded")
