# The fixtures hand out session cookies before any route checks for one, so a
# broken cookie would look identical to a working one. These check directly.

from conftest import ADMIN_MEMBER_ID, DEFAULT_MEMBER_ID


def test_client_is_signed_in_as_the_default_member(client):
    body = client.get("/api/auth/me").json()

    assert body["id"] == DEFAULT_MEMBER_ID
    assert body["is_admin"] is False


def test_admin_client_is_signed_in_with_admin_rights(admin_client):
    body = admin_client.get("/api/auth/me").json()

    assert body["id"] == ADMIN_MEMBER_ID
    assert body["is_admin"] is True


def test_anonymous_client_has_no_session(anonymous_client):
    assert anonymous_client.get("/api/auth/me").status_code == 401
