import os

# Settings are read once when the app starts. Anything wrong stops it starting,
# so a bad deploy fails right away instead of running with broken sign-in.

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off", ""}


def _flag(name, default=False):
    raw_value = os.environ.get(name)

    if raw_value is None:
        return default

    value = raw_value.strip().lower()

    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False

    # A typo like "treu" must not quietly count as off, which would turn off
    # cookie security and skip the secret checks below.
    raise RuntimeError(
        f"{name} must be one of 1/true/yes/on or 0/false/no/off, not {value!r}."
    )


SESSION_COOKIE_SECURE = _flag("SESSION_COOKIE_SECURE")

# Written here in the open, so it is fine for local work and never for a real
# site.
DEV_JWT_SECRET = "dev-insecure-jwt-secret-do-not-use-in-production"

# The fake value .env.example ships, which is easy to copy and forget to change.
_PLACEHOLDER_SECRET = "replace_me"

_MIN_SECRET_BYTES = 32

# Trimmed so that blanks and spaces count as "not set".
JWT_SECRET = os.environ.get("JWT_SECRET", "").strip() or DEV_JWT_SECRET

# Secure cookies mean this is a real site, so refuse to start on a secret that
# is public, still the placeholder, or too short to have been generated.
if SESSION_COOKIE_SECURE:
    if JWT_SECRET == DEV_JWT_SECRET:
        raise RuntimeError(
            "JWT_SECRET must be set when SESSION_COOKIE_SECURE is enabled."
        )
    # Checked as "contains", so padding the placeholder out to a passing length
    # does not slip through.
    if _PLACEHOLDER_SECRET in JWT_SECRET.lower():
        raise RuntimeError(
            "JWT_SECRET is still built from the .env.example placeholder. "
            "Generate one with: "
            'python3 -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
    if len(JWT_SECRET.encode("utf-8")) < _MIN_SECRET_BYTES:
        raise RuntimeError(
            f"JWT_SECRET must be at least {_MIN_SECRET_BYTES} bytes when "
            "SESSION_COOKIE_SECURE is enabled."
        )

JWT_ALGORITHM = "HS256"

JWT_EXPIRE_MINUTES = int(os.environ.get("JWT_EXPIRE_MINUTES") or "1440")

# A day at most, and never zero: an already-expired session would send members
# straight back to the login page.
if not 1 <= JWT_EXPIRE_MINUTES <= 1440:
    raise RuntimeError(
        f"JWT_EXPIRE_MINUTES must be between 1 and 1440, not {JWT_EXPIRE_MINUTES}."
    )

SESSION_COOKIE_NAME = "bch_session"
LOGIN_PATH = "/login"
