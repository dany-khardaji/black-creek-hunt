import os

# Settings read from the environment once, at import. Values that are wrong in a
# way that would weaken sessions raise here rather than at request time, so a
# misconfigured deploy fails while deploying instead of serving broken auth.
#
# Not read yet, though .env.example defines them: DATABASE_PATH, APP_ORIGIN, and
# the Google pair. They arrive with the slices that use them.

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off", ""}


def _flag(name, default=False):
    """Read a boolean environment variable, refusing anything ambiguous.

    An unrecognized value such as "treu" must not quietly read as false: for
    SESSION_COOKIE_SECURE that would drop the cookie's Secure attribute and
    skip the production secret check below, and the app would start and appear
    to work. Unset falls back to the caller's default, and an empty value is an
    ordinary way to write "off", so neither needs configuration locally.
    """
    raw_value = os.environ.get(name)

    if raw_value is None:
        return default

    value = raw_value.strip().lower()

    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False

    raise RuntimeError(
        f"{name} must be one of 1/true/yes/on or 0/false/no/off, not {value!r}."
    )


SESSION_COOKIE_SECURE = _flag("SESSION_COOKIE_SECURE")

# Committed in this file, so it is public. Fine for local http development,
# never acceptable for a real deployment.
DEV_JWT_SECRET = "dev-insecure-jwt-secret-do-not-use-in-production"

# The value .env.example ships on the JWT_SECRET line. Copying that file and
# filling in only the Google settings is the realistic way a deploy ends up
# signing sessions with a string published in this repository.
_PLACEHOLDER_SECRET = "replace_me"

# Matches the token_urlsafe(32) that .env.example documents, and the length
# below which PyJWT warns for HS256. Measured in bytes because that is what
# feeds the HMAC. This catches a truncated or forgotten secret. It cannot
# measure entropy: 32 repeated characters pass.
_MIN_SECRET_BYTES = 32

# "or" rather than a .get default: JWT_SECRET="" must count as unset, otherwise
# an empty value set in a deploy dashboard would silently sign every session.
# Stripped so that whitespace-only is unset too, rather than a three-space key.
JWT_SECRET = os.environ.get("JWT_SECRET", "").strip() or DEV_JWT_SECRET

# Secure cookies mean HTTPS, which means a real deployment. Refuse to start
# rather than sign production sessions with a key that is public, placeholder,
# or too short to have been generated the documented way.
if SESSION_COOKIE_SECURE:
    if JWT_SECRET == DEV_JWT_SECRET:
        raise RuntimeError(
            "JWT_SECRET must be set when SESSION_COOKIE_SECURE is enabled."
        )
    # Substring, not equality: padding the placeholder out to the length below
    # would otherwise pass both checks and deploy.
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

# PLAN.md sets a 24-hour session. A shorter window is a valid production choice,
# so the range is bounded rather than fixed. Zero or negative would mint tokens
# that are already expired, which presents as an immediate bounce back to the
# login page. Raising the ceiling is a deliberate edit, not a deploy setting.
JWT_EXPIRE_MINUTES = int(os.environ.get("JWT_EXPIRE_MINUTES") or "1440")

if not 1 <= JWT_EXPIRE_MINUTES <= 1440:
    raise RuntimeError(
        f"JWT_EXPIRE_MINUTES must be between 1 and 1440, not {JWT_EXPIRE_MINUTES}."
    )

SESSION_COOKIE_NAME = "bch_session"
LOGIN_PATH = "/login"
