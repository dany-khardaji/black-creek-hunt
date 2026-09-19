# Slice 7 — Deploy the beta to Vercel

## Context

Slices 1-6 are done and merged to `feature/v1-beta`. The app runs locally against Neon Postgres: login (password and Google), locked routes, real identity, admin checkout, sign-out, and the three pages all work in the browser.

Nothing is deployed. There is no `vercel.json`, no `pyproject.toml`, no root `requirements.txt`, and no README. The Google Cloud OAuth client only knows about `http://localhost:8000`.

This slice produces two things:

1. **The small code changes** that let Vercel find and run the app.
2. **The manual steps** the user follows by hand. Most of Slice 7 is account setup and secret-pasting that only the user can do.

Decisions already made with the user:

- Deploy `feature/v1-beta` as the Vercel Production Branch. Merge to `main` later.
- Repo is on GitHub (`dany-khardaji/black-creek-hunt`). No Vercel account yet — created during the manual steps.
- Tables are created by running `seed.py` once from the laptop against production. No startup hook.
- README deferred.
- Demo data goes live first ([PLAN.md:193](PLAN.md#L193)); real coordinates only before club invitations.

## What the research found

Verified against current Vercel and Neon docs (Aug 2026):

- **Vercel supports Python 3.14** and native FastAPI detection. The entrypoint is set with `[tool.vercel] entrypoint = "<module>:app"` in a root `pyproject.toml`, resolved from the repo root. No `api/` directory.
- **The backend imports as `app.*` from inside `backend/`** ([main.py:5](../backend/app/main.py#L5), same for 10 other files including `seed.py` and `manage.py`). `backend.app.main:app` fails with `ModuleNotFoundError: No module named 'app'`. A one-file shim at the repo root fixes it without touching those files.
- **Root Directory must stay `.`.** Vercel docs: an app "will not be able to access files outside of that directory" and "cannot use `..`". [main.py:705](../backend/app/main.py#L705) climbs three levels to `frontend/`, so setting Root Directory to `backend` would 404 (page not found) every page.
- **Dependencies are read from a root `requirements.txt`.** Ours is in `backend/`. Vercel won't find it there.
- **`SessionMiddleware` changes static-file handling.** Vercel normally promotes `app.mount("/static", ...)` files to its CDN, which would bypass the `Depends(auth.require_page_member)` guards. Because the app has top-level middleware, Vercel automatically keeps all static files inside the function. This is the correct behavior for guarded pages — **do not set `cdn = true`**.
- **Bundle limit is 500 MB.** Well clear; the frontend is a few files and one logo.
- **`open()` uses the project root as cwd.** `FRONTEND` and seed paths are computed from `__file__`, so they're unaffected.
- **Neon free tier:** 0.5 GB, 100 compute-hours/month, 10 branches. **Auto-suspends after 5 minutes idle** and cannot be disabled on Free. First request after a quiet stretch will be slow (a few seconds). Acceptable for a 15-20 member club.
- **`DATABASE_URL_POOLED` is read first** ([database.py:116](../backend/app/database.py#L116)). Correct for serverless.
- **`SESSION_COOKIE_SECURE=1` makes `JWT_SECRET` mandatory** and ≥32 bytes ([config.py:70-87](../backend/app/config.py#L70-L87)). A misconfigured deploy fails at import, which is the intended behavior.
- **`init_db()` is never called by the app** ([database.py:142](../backend/app/database.py#L142)). Only `seed.py`, `manage.py`, and tests call it. Consistent with the user's choice.

## Code changes

Four new files, two edits. All at the repo root unless noted.

### 1. `pyproject.toml` (new)

Tells Vercel where the app is and which Python to use.

```toml
[project]
name = "black-creek-hunt"
version = "0.1.0"
requires-python = ">=3.14"

[tool.vercel]
entrypoint = "vercel_app:app"
```

`requires-python` pins Vercel to 3.14, matching local. Without it Vercel defaults to 3.12 and the pinned wheels (`pydantic_core`, `psycopg-binary`, `cryptography`) may not resolve.

### 2. `requirements.txt` (new, root)

Vercel reads this location. Copy `backend/requirements.txt` **minus the dev-only packages**: `pytest`, `pluggy`, `iniconfig`, `Pygments`, `pytokens`, `mypy_extensions`, `pathspec`, `platformdirs`, `packaging`. Keep `click` (uvicorn needs it).

Keep `backend/requirements.txt` as-is for local dev and tests. Two files is a known duplication.

### 3. `vercel.json` (new)

Excludes tests and local-only files from the function bundle. Keyed by the resolved entrypoint file.

```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "functions": {
    "vercel_app.py": {
      "excludeFiles": "{backend/tests/**,backend/venv/**,docs/**,*.db,.env*}"
    }
  }
}
```

### 4. `vercel_app.py` (new)

The shim Vercel imports. Adds `backend/` to `sys.path`, then re-exports the real app. Eight lines, no logic.

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.main import app  # noqa: E402
```

Verified locally: `backend/venv/bin/python -c "from vercel_app import app"` from the repo root loads a `FastAPI` instance and `FRONTEND` resolves to `<repo>/frontend`.

### 5. `.env.example` (edit)

Line 13 still says `# Path to the SQLite file, relative to backend/` above `DATABASE_URL`. Replace with a comment describing the Neon connection string and that `DATABASE_URL_POOLED` is preferred in production. Add a `# --- Production only ---` block noting `SESSION_COOKIE_SECURE=1` and `APP_ORIGIN=https://<your-app>.vercel.app`.

### 6. `.gitignore` (edit)

Add `.vercel/` — the CLI writes project-link metadata there.

## Manual steps — the user does these by hand, in order

**0. Before you start**
- Checklist: GitHub repo pushed, Neon project exists, Google Cloud client exists, `.env` works locally, suite green (`backend/venv/bin/pytest -q backend`).
- Generate a **new** `JWT_SECRET` for production — never reuse the local one. Command: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.

**1. Create the Vercel account and import the repo**
- Sign up at vercel.com with GitHub.
- Add New → Project → import `black-creek-hunt`.
- Framework preset: Vercel should auto-detect FastAPI. If it shows "Other", that's fine — `pyproject.toml` drives it.
- **Root Directory: leave as `.`** (repo root). The entrypoint path in `pyproject.toml` is relative to it.
- **Do not deploy yet.** Click "Environment Variables" first.

**2. Set the environment variables**
Every variable, where the value comes from, and which environment (Production):

| Variable | Value | Notes |
|---|---|---|
| `DATABASE_URL_POOLED` | Neon pooled string | The `-pooler` host. Primary. |
| `DATABASE_URL` | Neon direct string | Fallback; harmless to set both. |
| `JWT_SECRET` | freshly generated | ≥32 chars. App refuses to start otherwise. |
| `SESSION_COOKIE_SECURE` | `1` | Required over HTTPS. |
| `APP_ORIGIN` | `https://<name>.vercel.app` | **Unknown until first deploy** — see step 4. |
| `GOOGLE_CLIENT_ID` | from Google Cloud | |
| `GOOGLE_CLIENT_SECRET` | from Google Cloud | |
| `JWT_EXPIRE_MINUTES` | `1440` | optional, this is the default |

Do **not** set `TEST_DATABASE_URL` or `SKIP_ENV_FILE` in Vercel.

**3. Set the Production Branch**
- Project Settings → Git → Production Branch → `feature/v1-beta`.
- Every push to that branch redeploys production. Other branches get preview URLs.

**4. First deploy and the APP_ORIGIN loop**
- Deploy. It will build and go live at `https://<name>.vercel.app`.
- If `APP_ORIGIN` is still unset, pages will **not** 500 (server error) — it defaults to localhost, so password login works but Google sign-in fails until step 5 is done.
- Copy the real URL. Set `APP_ORIGIN` to it. Redeploy (Deployments → ⋯ → Redeploy).

**5. Add the production redirect URI to Google Cloud**
- console.cloud.google.com/auth/clients → the web client → Authorized redirect URIs → Add: `https://<name>.vercel.app/api/auth/google/callback`.
- Keep the localhost one.
- **This fails silently if wrong.** No error, just a login that doesn't complete.
- Also: Audience → Test users → confirm the user's Gmail is listed.

**6. Create the tables and seed demo data**
- From the laptop, with `DATABASE_URL` temporarily pointed at the **production** Neon string:
  ```
  DATABASE_URL="<production string>" DATABASE_URL_POOLED="" backend/venv/bin/python backend/seed.py
  ```
- Why `DATABASE_URL_POOLED=""`: `create_all` runs DDL, and Neon's pooler can reject that. Use the direct connection for schema work.
- Then create the first account the same way with `manage.py create-member --admin`.

**7. Verify**
- Signed-out: `https://<name>.vercel.app/` → redirects to `/login`.
- Password login works.
- Google login works (proves step 5).
- Homepage shows three properties; a property page shows the map.
- Check in, see it on the map, check out.
- **On a phone, on cellular, not Wi-Fi** — proves it's reachable from outside.
- Sign out returns to `/login`.
- Vercel → Deployments → Functions → Logs: no errors.

**8. Known gaps and what's next**
- Neon cold start after 5 min idle.
- `requirements.txt` is duplicated (root vs `backend/`).
- No README yet.
- Demo data is live; real coordinates come later via `seed.py` against production.
- Custom domain deferred per [PLAN.md:131](PLAN.md#L131).

**9. Rollback**
- Vercel → Deployments → pick an earlier one → Promote to Production. Instant.
- Database: Neon → Branches → restore from a point in time (free tier keeps 6 hours? verify in console — check your plan).

## Order of work

1. Code changes 1-6 above. Small, mechanical.
2. Run the suite locally — should stay at 193, nothing here touches app logic.
3. Commit: `feat: add vercel deployment config`.
4. **Hand off to the user.** They follow the manual steps above. Steps 1-7 are theirs — I can't create the Vercel account or paste secrets.
5. User reports back after step 7. Fix whatever broke.

## Verification

- `backend/venv/bin/pytest -q backend` — 193 passing, unchanged.
- `git diff --check` — clean.
- `python3 -c "import tomllib; tomllib.load(open('pyproject.toml','rb'))"` — TOML parses.
- `python3 -c "import json; json.load(open('vercel.json'))"` — JSON parses.
- Root `requirements.txt` has no `pytest` line: `! grep -q '^pytest' requirements.txt`.
- **The real verification is step 7 of the manual steps**, done by the user on a live URL. Nothing here can be confirmed until then.

## Risks

- **`psycopg-binary` / `cryptography` / `pydantic_core` on Vercel's Linux runtime.** All three publish manylinux wheels for 3.14, so this should resolve. If the build fails on one, the fix is dropping the pin to let pip pick a compatible version.
- **Neon pooler and DDL.** `init_db` over the pooled connection may error. Step 6 uses the direct URL for seeding to sidestep it.
- **`APP_ORIGIN` chicken-and-egg.** The URL doesn't exist until first deploy. Step 4's two-pass approach handles it, but it's the step most likely to be done out of order.
- **Google redirect URI.** Silent failure. Step 5 calls it out.
