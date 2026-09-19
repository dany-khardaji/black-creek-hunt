# Google sign-in, with Google itself replaced. The round trip to Google is not
# something a test can make, so these cover the part this app owns: deciding who
# a set of Google claims belongs to, and refusing everyone else.

import app.main as main_module
import httpx
import pytest
from authlib.jose.errors import BadSignatureError
from app import auth, config
from conftest import DEFAULT_MEMBER_ID, seed_member
from sqlalchemy import text


# Stands in for Google returning a signed-in user.
def fake_google(monkeypatch, claims=None, fail=False, raises=None):
    class FakeGoogle:
        async def authorize_access_token(self, request):
            if raises is not None:
                raise raises
            if fail:
                from authlib.integrations.starlette_client import OAuthError

                raise OAuthError("access_denied")
            return {"userinfo": claims or {}}

    # Authlib builds the client on demand through __getattr__; setting the
    # attribute directly takes priority over that lookup.
    monkeypatch.setattr(main_module.oauth, "google", FakeGoogle(), raising=False)
    monkeypatch.setattr(main_module.config, "GOOGLE_SIGN_IN_ENABLED", True)


def callback(client):
    return client.get("/api/auth/google/callback", follow_redirects=False)


# --- Matching rules ---------------------------------------------------------


def test_member_matched_by_google_sub(conn):
    seed_member(conn, "member-g", email="g@example.com")
    conn.execute(
        text("UPDATE members SET google_sub = :sub WHERE id = :id"),
        {"sub": "sub-1", "id": "member-g"},
    )
    conn.commit()

    member = auth.member_for_google_claims(conn, "sub-1", "other@example.com", True)

    # Matched on the id, so a changed email address does not matter.
    assert member["id"] == "member-g"


def test_first_google_sign_in_links_by_verified_email(conn):
    seed_member(conn, DEFAULT_MEMBER_ID, email="mike@example.com")

    member = auth.member_for_google_claims(conn, "sub-new", "mike@example.com", True)

    assert member["id"] == DEFAULT_MEMBER_ID
    assert member["google_sub"] == "sub-new"


def test_unverified_email_never_links(conn):
    seed_member(conn, DEFAULT_MEMBER_ID, email="mike@example.com")

    assert auth.member_for_google_claims(conn, "sub-x", "mike@example.com", False) is None
    assert auth.load_member(conn, DEFAULT_MEMBER_ID)["google_sub"] is None


def test_unknown_email_is_not_admitted(conn):
    assert auth.member_for_google_claims(conn, "sub-y", "stranger@example.com", True) is None


# A member already tied to one Google account is never moved to another.
def test_member_with_a_different_google_sub_is_not_relinked(conn):
    seed_member(conn, "member-g", email="g@example.com")
    conn.execute(
        text("UPDATE members SET google_sub = :sub WHERE id = :id"),
        {"sub": "sub-1", "id": "member-g"},
    )
    conn.commit()

    assert auth.member_for_google_claims(conn, "sub-2", "g@example.com", True) is None


def test_missing_sub_is_refused(conn):
    seed_member(conn, DEFAULT_MEMBER_ID, email="mike@example.com")

    assert auth.member_for_google_claims(conn, None, "mike@example.com", True) is None


# --- The callback route -----------------------------------------------------


def test_callback_signs_a_member_in(anonymous_client, conn, monkeypatch):
    seed_member(conn, DEFAULT_MEMBER_ID, email="mike@example.com")
    fake_google(monkeypatch, {"sub": "sub-1", "email": "mike@example.com", "email_verified": True})

    response = callback(anonymous_client)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    # The same cookie password sign-in sets, so the rest of the app is unchanged.
    assert auth.decode_session_token(
        response.cookies[config.SESSION_COOKIE_NAME]
    ) == DEFAULT_MEMBER_ID


def test_callback_records_the_sign_in_time(anonymous_client, conn, monkeypatch):
    seed_member(conn, DEFAULT_MEMBER_ID, email="mike@example.com")
    fake_google(monkeypatch, {"sub": "sub-1", "email": "mike@example.com", "email_verified": True})

    callback(anonymous_client)

    assert auth.load_member(conn, DEFAULT_MEMBER_ID)["last_login_at"] is not None


def test_callback_turns_a_non_member_away(anonymous_client, conn, monkeypatch):
    fake_google(monkeypatch, {"sub": "sub-z", "email": "stranger@example.com", "email_verified": True})

    response = callback(anonymous_client)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?error=not_a_member"
    assert config.SESSION_COOKIE_NAME not in response.cookies


def test_callback_handles_a_refused_consent_screen(anonymous_client, conn, monkeypatch):
    fake_google(monkeypatch, fail=True)

    response = callback(anonymous_client)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?error=google_failed"
    assert config.SESSION_COOKIE_NAME not in response.cookies


# Without credentials configured, the button must fail politely rather than
# raising, so password sign-in keeps working.
def test_routes_are_disabled_without_credentials(anonymous_client, monkeypatch):
    monkeypatch.setattr(main_module.config, "GOOGLE_SIGN_IN_ENABLED", False)

    started = anonymous_client.get("/api/auth/google/login", follow_redirects=False)
    returned = callback(anonymous_client)

    assert started.headers["location"] == "/login?error=google_unavailable"
    assert returned.headers["location"] == "/login?error=google_unavailable"


# A token that fails its signature check and an unreachable Google are not
# OAuthError, so catching that alone returned 500 (server error) instead.
@pytest.mark.parametrize(
    "error",
    [
        BadSignatureError("bad signature"),
        httpx.ConnectError("google is unreachable"),
    ],
)
def test_callback_survives_token_and_network_failures(
    anonymous_client, conn, monkeypatch, error
):
    fake_google(monkeypatch, raises=error)

    response = callback(anonymous_client)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?error=google_failed"
