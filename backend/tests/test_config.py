import importlib

import pytest
from app import config as config_module


# config.py reads os.environ at import, so every test here has to re-import the
# module to see a changed variable. monkeypatch restores the environment, and
# the final reload puts the module back the way the rest of the suite found it.
def reload_config(monkeypatch, **env):
    for name in (
        "JWT_SECRET",
        "JWT_EXPIRE_MINUTES",
        "SESSION_COOKIE_SECURE",
    ):
        monkeypatch.delenv(name, raising=False)

    for name, value in env.items():
        monkeypatch.setenv(name, value)

    return importlib.reload(config_module)


@pytest.fixture(autouse=True)
def restore_config():
    yield
    importlib.reload(config_module)


# With nothing set, development defaults apply and the app still starts
def test_defaults_are_development_safe(monkeypatch):
    config = reload_config(monkeypatch)

    assert config.JWT_SECRET == config.DEV_JWT_SECRET
    assert config.SESSION_COOKIE_SECURE is False
    assert config.JWT_EXPIRE_MINUTES == 1440
    assert config.JWT_ALGORITHM == "HS256"
    assert config.SESSION_COOKIE_NAME == "bch_session"


# A real secret in the environment wins over the committed development one
def test_env_secret_overrides_default(monkeypatch):
    config = reload_config(monkeypatch, JWT_SECRET="a-real-secret-from-the-environment")

    assert config.JWT_SECRET == "a-real-secret-from-the-environment"


# An empty JWT_SECRET counts as unset, not as a valid empty signing key
def test_empty_secret_falls_back_to_default(monkeypatch):
    config = reload_config(monkeypatch, JWT_SECRET="")

    assert config.JWT_SECRET == config.DEV_JWT_SECRET


# Secure cookies mean production, so the public development secret is fatal
def test_secure_cookies_without_secret_refuses_to_start(monkeypatch):
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        reload_config(monkeypatch, SESSION_COOKIE_SECURE="1")


# The same check must catch an empty secret, not just a missing one
def test_secure_cookies_with_empty_secret_refuses_to_start(monkeypatch):
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        reload_config(monkeypatch, SESSION_COOKIE_SECURE="1", JWT_SECRET="")


# Secure cookies with a real secret is the valid production combination
def test_secure_cookies_with_real_secret_starts(monkeypatch):
    config = reload_config(
        monkeypatch, SESSION_COOKIE_SECURE="1", JWT_SECRET="a-real-production-secret-long-enough"
    )

    assert config.SESSION_COOKIE_SECURE is True
    assert config.JWT_SECRET == "a-real-production-secret-long-enough"


# "0" is the value .env.example ships, and must not read as truthy
def test_secure_flag_is_off_for_zero(monkeypatch):
    config = reload_config(monkeypatch, SESSION_COOKIE_SECURE="0")

    assert config.SESSION_COOKIE_SECURE is False


# Accept the spellings someone might reasonably put in a deploy dashboard
@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " 1 "])
def test_secure_flag_accepts_affirmative_spellings(monkeypatch, value):
    config = reload_config(
        monkeypatch, SESSION_COOKIE_SECURE=value, JWT_SECRET="a-real-production-secret-long-enough"
    )

    assert config.SESSION_COOKIE_SECURE is True


# Explicit negatives, and an empty value, keep local http development working
@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off", "", " "])
def test_secure_flag_is_off_for_explicit_negatives(monkeypatch, value):
    config = reload_config(monkeypatch, SESSION_COOKIE_SECURE=value)

    assert config.SESSION_COOKIE_SECURE is False


# A typo must not read as false: that would silently drop the cookie's Secure
# attribute and skip the production secret check entirely
@pytest.mark.parametrize("value", ["treu", "maybe", "2", "yes please"])
def test_secure_flag_refuses_ambiguous_values(monkeypatch, value):
    with pytest.raises(RuntimeError, match="SESSION_COOKIE_SECURE"):
        reload_config(monkeypatch, SESSION_COOKIE_SECURE=value)


# Session lifetime is configurable for a shorter production window
def test_expiry_reads_from_environment(monkeypatch):
    config = reload_config(monkeypatch, JWT_EXPIRE_MINUTES="60")

    assert config.JWT_EXPIRE_MINUTES == 60


# A non-numeric lifetime is fatal rather than a silently defaulted session
def test_non_numeric_expiry_refuses_to_start(monkeypatch):
    with pytest.raises(ValueError):
        reload_config(monkeypatch, JWT_EXPIRE_MINUTES="not-a-number")


# Zero or negative would mint already-expired tokens, so login would appear to
# succeed and then bounce straight back to the login page
@pytest.mark.parametrize("value", ["0", "-1", "-1440"])
def test_non_positive_expiry_refuses_to_start(monkeypatch, value):
    with pytest.raises(RuntimeError, match="JWT_EXPIRE_MINUTES"):
        reload_config(monkeypatch, JWT_EXPIRE_MINUTES=value)


# PLAN.md sets 24 hours as the session length; longer needs a deliberate edit
def test_expiry_beyond_one_day_refuses_to_start(monkeypatch):
    with pytest.raises(RuntimeError, match="JWT_EXPIRE_MINUTES"):
        reload_config(monkeypatch, JWT_EXPIRE_MINUTES="1441")


# The documented 24-hour maximum is itself valid
def test_expiry_at_the_ceiling_is_allowed(monkeypatch):
    config = reload_config(monkeypatch, JWT_EXPIRE_MINUTES="1440")

    assert config.JWT_EXPIRE_MINUTES == 1440


# Whitespace-only is unset, not a three-space signing key
@pytest.mark.parametrize("value", ["   ", "\t", "\n"])
def test_whitespace_secret_falls_back_to_default(monkeypatch, value):
    config = reload_config(monkeypatch, JWT_SECRET=value)

    assert config.JWT_SECRET == config.DEV_JWT_SECRET


# Whitespace around a real secret is stripped rather than signed with
def test_secret_is_stripped(monkeypatch):
    config = reload_config(monkeypatch, JWT_SECRET="  a-real-production-secret-value  ")

    assert config.JWT_SECRET == "a-real-production-secret-value"


# A whitespace-only secret in production is caught as an unset secret
def test_secure_cookies_with_whitespace_secret_refuses_to_start(monkeypatch):
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        reload_config(monkeypatch, SESSION_COOKIE_SECURE="1", JWT_SECRET="   ")


# .env.example ships JWT_SECRET=replace_me, so a copied file must not deploy
@pytest.mark.parametrize("value", ["replace_me", "REPLACE_ME", "  replace_me  "])
def test_secure_cookies_with_placeholder_secret_refuses_to_start(monkeypatch, value):
    with pytest.raises(RuntimeError, match="placeholder"):
        reload_config(monkeypatch, SESSION_COOKIE_SECURE="1", JWT_SECRET=value)


# A short secret is a truncated or hand-typed one, not a generated one
def test_secure_cookies_with_short_secret_refuses_to_start(monkeypatch):
    with pytest.raises(RuntimeError, match="32 bytes"):
        reload_config(monkeypatch, SESSION_COOKIE_SECURE="1", JWT_SECRET="tooshort")


# Padding the placeholder to clear the length rule must not deploy: the length
# error would otherwise read as "make it longer" rather than "generate one"
def test_secure_cookies_with_padded_placeholder_refuses_to_start(monkeypatch):
    with pytest.raises(RuntimeError, match="placeholder"):
        reload_config(
            monkeypatch,
            SESSION_COOKIE_SECURE="1",
            JWT_SECRET="replace_me_plus_padding_to_32_ch",
        )


# The floor counts bytes, not characters, because bytes are what feed the HMAC
def test_secret_length_is_measured_in_bytes(monkeypatch):
    # 16 characters, but 32 bytes once encoded
    multibyte = "é" * 16

    config = reload_config(
        monkeypatch, SESSION_COOKIE_SECURE="1", JWT_SECRET=multibyte
    )

    assert config.JWT_SECRET == multibyte


# A secret of the documented token_urlsafe(32) shape is accepted
def test_secure_cookies_with_generated_secret_starts(monkeypatch):
    generated = "x" * 43  # token_urlsafe(32) produces 43 characters

    config = reload_config(
        monkeypatch, SESSION_COOKIE_SECURE="1", JWT_SECRET=generated
    )

    assert config.JWT_SECRET == generated


# The placeholder and length rules are production-only: local http development
# over the dev secret must keep working with no configuration at all
def test_placeholder_secret_is_allowed_without_secure_cookies(monkeypatch):
    config = reload_config(monkeypatch, JWT_SECRET="replace_me")

    assert config.JWT_SECRET == "replace_me"
