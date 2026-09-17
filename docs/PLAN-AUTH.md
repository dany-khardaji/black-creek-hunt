# Slice 4a — Password Authentication

## Context

Every API route in this app is currently anonymous, and identity is a single
hardcoded constant: `CURRENT_MEMBER_ID = "member-1"` at
[main.py:15](backend/app/main.py#L15). Four places depend on it — two write it
onto hunt rows, one enforces checkout ownership, one decides whether the UI
shows a check-out button. Because every request is the same member, the
ownership check at [main.py:253](backend/app/main.py#L253) can never fail, and
`/api/map-state` hands any anonymous caller member names and precise stand
coordinates.

This slice makes identity real for password accounts. Google OAuth is a
deliberate second commit: it needs a Google Cloud project, consent screen, and
redirect URIs that don't exist yet, and none of that is required to make
password login work end to end. The design below leaves the OAuth seam open
rather than building it early.

PLAN.md §4 is the source for the decisions already settled: Argon2 via pwdlib,
24-hour JWT in an `HttpOnly` cookie, one allowlist, no public signup.

## Decisions made with the user

- Password auth now; Google OAuth as a separate follow-up commit.
- Protected `/api/*` returns **401 JSON**; protected **pages redirect** to `/login`.
- Members created by an admin CLI (`backend/manage.py`). No public signup.
- `/api/live-count` is protected like everything else — no exceptions.
- **Approved dependencies:** `pyjwt`, `pwdlib[argon2]`. (Authlib is a separate
  approval at the Google commit.)
- A missing `JWT_SECRET` is fatal only when `SESSION_COOKIE_SECURE` is on,
  reusing the variable `.env.example` already defines rather than inventing an
  `APP_ENV` that could drift out of sync with it.
- New auth tests go in `backend/tests/test_auth.py`; route-behavior changes stay
  in `test_main.py` beside their peers.

## Two facts verified before planning

1. **FastAPI dependencies run before body validation.** Confirmed by experiment:
   a route with a 401-raising dependency returns 401, not 422, for a malformed
   body. This breaks two existing tests — see Step 7.
2. **Neither** `pyjwt` **nor** `pwdlib` **is installed** in `backend/venv`.

## New files

### `backend/app/config.py`

Plain `os.environ` into module constants. No Pydantic Settings, no dotenv — one
module of ~30 lines, no extra dependency, and `monkeypatch.setenv` +
`importlib.reload` makes it testable.

```python
SESSION_COOKIE_SECURE = _flag("SESSION_COOKIE_SECURE")
DEV_JWT_SECRET = "dev-insecure-jwt-secret-do-not-use-in-production"
JWT_SECRET = os.environ.get("JWT_SECRET") or DEV_JWT_SECRET

# Secure cookies mean HTTPS, which means a real deployment. Refuse to start
# rather than sign production sessions with a key that is public in this file.
if SESSION_COOKIE_SECURE and JWT_SECRET == DEV_JWT_SECRET:
    raise RuntimeError("JWT_SECRET must be set when SESSION_COOKIE_SECURE is enabled.")

JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.environ.get("JWT_EXPIRE_MINUTES", "1440"))
SESSION_COOKIE_NAME = "bch_session"
LOGIN_PATH = "/login"
```

Use `or`, not a `.get` default, so `JWT_SECRET=""` counts as unset.

**Deliberately not read yet:** `DATABASE_PATH`, `APP_ORIGIN`, the Google pair.
Unused constants are the speculative abstraction AGENTS.md warns against.
`DATABASE_PATH` especially — wiring it now would change which file `seed.py` and
local dev touch, an unrelated behavior change inside an auth commit, and Slice 6
replaces it with `DATABASE_URL` anyway.

### `backend/app/auth.py`

```python
hash_password(password) -> str
verify_password(password, stored_hash) -> bool
normalize_email(email) -> str
create_session_token(member_id, now=None) -> str
decode_session_token(token) -> str | None
set_session_cookie(response, token) / clear_session_cookie(response)
load_member(conn, member_id) / find_member_by_email(conn, email)
public_member(row) -> dict
current_member_or_none(request)
require_api_member(request)      # raises 401
require_page_member(request)     # raises RedirectToLogin
```

The non-obvious parts:

- `verify_password` **returns** `False` **on a falsy or malformed hash**, never
  raises. `password_hash` is already nullable, so a Google-only member must give
  a clean 401 rather than a 500. This also covers the literal
  `"not-a-real-hash"` that test fixtures seed.
- **Token payload is exactly** `{sub, exp, iat}`**.** No `is_admin` — an admin flag
  in the token stays stale for 24 hours after a demotion. Authorization reads
  `is_admin` from the row per request.
- `decode_session_token` **passes** `algorithms=[JWT_ALGORITHM]` **explicitly.**
  That is what rejects an `alg: none` forgery.
- `SameSite=Lax`**, not** `Strict`**.** Required for the later OAuth commit: the
  browser returns from Google's domain via a cross-site redirect, and `Strict`
  withholds the cookie on that landing, producing a login loop.
- `clear_session_cookie` **must mirror** `path`**/**`httponly`**/**`samesite`**/**`secure`
  **exactly**, or some browsers leave the cookie in place.
- `public_member` **returns only** `id`, `email`, `first_name`, `last_name`,
  `is_admin`. One function owns the rule that `password_hash` and `google_sub`
  never leave the process.

**Two dependencies, not one parameterized.** `require_api_member` and
`require_page_member` share a private resolver. A factory like
`require_member(redirect=True)` would make the route signature stop saying what
happens on failure; two names keep it readable at each call site.

Page redirects use a custom exception plus an `@app.exception_handler` returning
**303** — the browser must issue a GET for `/login`, which 303 states
unambiguously.

**One ugly but necessary detail:** `_resolve_member` does `from app import main`
_inside the function_ to reach `get_connection`. This is not stylistic.
`conftest.py` swaps the database with
`monkeypatch.setattr(main_module, "get_connection", ...)`. A module-level
`from app.database import get_connection` would hold its own reference, and every
authenticated test request would read the real `backend/blackcreek.db` while
routes read the temp file — **tests passing while touching real data.** The
clean fix is a `get_connection` dependency on all six routes; that is a Slice 6
refactor, not this one. Leave a comment saying so.

### `backend/manage.py`

`argparse` with subparsers (stdlib, gives `--help`):

```
python backend/manage.py create-member --email E --first-name F --last-name L [--admin]
python backend/manage.py reset-password --email E
```

- **Password never comes from argv** — `getpass.getpass()`, prompted twice and
  compared. A password in `argv` lands in shell history and `ps`.
- Normalize through `auth.normalize_email` on insert _and_ lookup.
- Call `init_db()` first so a fresh checkout works.
- Exit non-zero with a plain message on duplicate or unknown email, not an
  `IntegrityError` traceback.
- IDs are `uuid4().hex` — **not** a continuation of `member-1`, which would
  collide with test fixtures.
- `reset-password` does **not** clear `google_sub`: the schema comment at
  [database.py:51](backend/app/database.py#L51) says one person may hold both.
- Needs a `sys.path` insert so the documented command works from the repo root,
  matching how pytest is invoked in AGENTS.md.

## Changed files

### `backend/app/models.py`

Add one model. `str`, not `EmailStr` — `email-validator` isn't installed, and a
field only ever compared against an allowlist gains nothing from it.

```python
class LoginRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=1024)
```

`max_length` on the password matters: Argon2 cost scales with input, so an
unbounded field is a cheap DoS.

### `backend/app/main.py`

**Delete** `CURRENT_MEMBER_ID` at line 15 along with its comment. Keeping it as
a fallback preserves the exact bug this slice removes.

**Three auth routes** in an `# --- Authentication ---` block before
`/api/properties`. The banner is where Google's two slot in later.

- `POST /api/auth/login` — normalize email, look up, verify, write
  `last_login_at`, set cookie, return `public_member`. **Unknown email and wrong
  password return one identical 401** (`invalid_credentials`); the allowlist is
  confidential, and [login.js:33](frontend/login/login.js#L33) already comments
  on this.
- `GET /api/auth/me` — guarded, returns `public_member`.
- `POST /api/auth/logout` — **deliberately unguarded.** Logout must work with an
  expired or malformed session, or a member with a bad cookie cannot clear it.

_Accepted tradeoff:_ an unknown email skips the Argon2 work and answers
measurably faster — a timing oracle for enumeration. For a 15–20 member club
with no public signup, hashing against a dummy on the miss path isn't worth the
code. Noted, not fixed.

**Guards** — append `member=Depends(auth.require_api_member)` to all six API
routes. For `check_in`, `member` must come **after** `request`; a defaulted
parameter cannot precede a non-defaulted one.

**The four call sites:**

| Line | Change                                                                                                |
| ---- | ----------------------------------------------------------------------------------------------------- |
| 203  | host insert → `member["id"]`                                                                          |
| 218  | guest rows → `member["id"]` (intentionally the _host's_ ID; that is what makes cascade checkout work) |
| 253  | `if hunt["member_id"] != member["id"] and not member["is_admin"]`                                     |
| 392  | `can_check_out = not is_guest and (active_hunt["member_id"] == member["id"] or member["is_admin"])`   |

Lines 253 and 392 **must express the same rule**, or the UI shows a button the
API then rejects with 403. Consequence worth stating: for an admin, every
occupied stand reports `can_check_out: true`. That is intended.

**Page routes** (478–491) — must stay declared **before** the `/static` mount at
498; the comment at 472 explains why.

- `GET /` and `GET /property/{slug}` → `Depends(auth.require_page_member)`.
- `GET /login` stays unguarded but gains the **opposite** behavior: a caller who
  already has a valid session is redirected to `/`. Otherwise a signed-in member
  bookmarking `/login` sees a form for an account they are already in.

### `backend/tests/conftest.py`

- Lines 179 and 213 stop reaching for `main_module.CURRENT_MEMBER_ID` and use
  the existing `DEFAULT_MEMBER_ID` — same string, no expectation shifts.
- `seed_member(password=None)` — hash when given, otherwise keep
  `"not-a-real-hash"`. **Keeping the junk default is deliberate:** Argon2 is
  intentionally slow and `seed_hunt` calls `seed_member` on nearly every test;
  hashing unconditionally would add seconds to a 0.17s suite. It also gives
  `verify_password`'s malformed-hash path live coverage.
- `client` **sets a real signed cookie.** This exercises the whole chain —
  `create_session_token` → cookie → `decode_session_token` → `load_member` — on
  every test using the fixture. `app.dependency_overrides` would leave the real
  dependency untested by the existing suite. Cookie jars are per-TestClient, so
  authed and anonymous fixtures cannot leak into each other.
- New: `admin_client`, `anonymous_client`, and an `authed_client(db_path)`
  **helper** for the `file_db` tests that build their own client inline.
  `anonymous_client` still takes `conn` — otherwise a guard bug would let a
  request reach the real database.

### `backend/tests/test_main.py`

`TestClient(app)` is constructed at **11 sites** outside the fixture. Once guards
land, every one hitting a guarded route returns 401. Three need specific care:

- **Lines 84 and 300** assert **422** and use no database fixture at all — they
  rely on validation rejecting the body first. Since dependencies run _before_
  validation (verified above), both now return **401**. They need a DB fixture
  and an authenticated client. **This is the change most likely to be missed.**
- **Lines 65 and 386** build the client inside a worker thread; set the cookie
  there, keeping the concurrency semantics unchanged.
- **Line 677** asserts two deleted routes 404 — routing resolves before
  dependencies, so it passes unchanged.

### Frontend

- `home.js` — `requestJson` currently discards the status code. Have it
  throw an error carrying `response.status` (mirroring `property.js`'s existing
  `ApiError` shape rather than inventing a second one), and redirect on 401 via
  `window.location.replace("/login")` — `replace`, not `assign`, so the back
  button doesn't bounce into a loop. **Leave** `Promise.all` **alone**; with guards
  both calls now fail together, so its coupling stops mattering here.
- `property.js` — `ApiError` already carries `status`. Add the same redirect
  at the three catches that surface errors (refresh, check-in, checkout). Needs a
  module-level `isRedirecting` guard: the 30-second poll plus a user action
  could both 401 at once.
- `login.js` **and** `login/index.html` **— no changes.** The login POST contract was
  written to match what is already there. **Known temporary dead link:** the
  "Continue with Google" button at
  [login/index.html:30](frontend/login/index.html#L30) will 404 until the OAuth
  commit.
- **No logout control yet** — PLAN.md puts it on the homepage in Slice 5. The
  endpoint exists after this commit; nothing calls it.

## Order of work

Steps 5 and 8 are deliberately separated so the suite stays green through the
additive half and every guard-induced failure lands in one isolated step.

1. Install and pin `pyjwt` + `pwdlib[argon2]`.
2. `config.py` + its test.
3. `auth.py` + unit tests (no app wiring yet, so these run clean).
4. `models.py` — `LoginRequest`.
5. `main.py`: delete `CURRENT_MEMBER_ID`, add handler + three auth routes.
   **Suite still green here.**
6. `conftest.py`: authed `client`, new fixtures, `seed_member(password=...)`,
   de-couple `file_db`.
7. Fix the 11 bare-`TestClient` sites — **before** guards, so the next step's
   failures are all real.
8. `main.py`: guards, `/login` reverse-redirect, four call-site replacements.
9. Run suite. Any 401 is a missed site from step 7.
10. Route tests: identity, admin checkout, `can_check_out` parity.
11. `manage.py` + its tests.
12. Frontend 401 handling.

## Verification

- `backend/venv/bin/pytest -q backend` — 49 existing plus ~35 new.
- `node --check` on `frontend/home/home.js`, `frontend/login/login.js`,
  `frontend/property/property.js`.
- `git diff --check`.

Tests that matter most:

- **Identity derives from the session** — two different members on two clients
  produce two different `hunts.member_id` values. This is the regression test
  against a hardcoded identity sneaking back.
- **A mixed-case, whitespace-padded email logs in** — the portability guarantee
  that keeps SQLite and Postgres behaving identically.
- `GET /login` **unauthenticated returns 200**, not a redirect — loop check.
- **Garbage cookie is treated as unauthenticated**, not a 500.
- **A valid token for a deleted member is rejected** — why the dependency hits
  the database instead of trusting the token.

Manual pass: signed-out `/` redirects to `/login`; login works; check-in writes
the signed-in member; logout returns to `/login`.

## Risks to later slices

**Google OAuth (next commit) — unblocked on purpose.** `SameSite=Lax` is
encoded here because `Strict` breaks the OAuth return. `set_session_cookie` and
`create_session_token` are method-agnostic, so the callback reuses them verbatim
— PLAN.md's "identical session handling." `normalize_email` is the shared
allowlist chokepoint. `verify_password` already tolerates `NULL` hashes.

**Postgres (Slice 6).** `config.py` reads no `DATABASE_PATH`, so nothing
entrenches the SQLite path. Email lowercasing lives in Python only, so no SQLite
collation behavior needs reproducing. Two carry-overs: `auth._resolve_member`'s
deferred import should become a proper connection dependency, and
`conftest.build_connection` bypasses `ensure_current_schema`.

**Vercel (Slice 7).** `SESSION_COOKIE_SECURE=1` now also makes `JWT_SECRET`
mandatory — a missing secret fails at import, i.e. at deploy, which is the
intent. A serverless cold start re-imports `config.py`, so a misconfigured
deploy fails fast rather than intermittently. **Confirm** `argon2-cffi` **resolves a
Linux wheel for the Vercel Python runtime.** `manage.py` is a local tool; the
first production member needs a one-off run against Neon, or a seeded demo
account as PLAN.md Slice 6 anticipates.
