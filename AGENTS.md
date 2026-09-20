# Black Creek Hunt Project Guidance

## Project

Black Creek Hunt is a members-only hunting-club application for viewing properties, checking hunters and guests into stands, and checking them out safely. Treat member information, phone numbers, and property coordinates as sensitive.

`docs/PLAN.md` records the current roadmap and architectural decisions. Follow the user's current request when it intentionally changes that plan.

## Stack

- Backend: Python 3.14, FastAPI, SQLAlchemy 2.0 Core over PostgreSQL (Neon), and Pytest.
- Frontend: vanilla JavaScript, semantic HTML, CSS, and Leaflet. No build step.
- Deployed on Vercel. Tests run against a separate Neon branch through `TEST_DATABASE_URL`.
- Do not describe planned technology as already implemented.

## Working Agreements

- Make the smallest complete change that satisfies the request; avoid speculative abstractions.
- Preserve unrelated working-tree changes. Do not commit, discard, or rewrite them unless asked.
- Do not add frameworks or production dependencies without explicit approval. Leaflet is approved.
- Keep comments for business rules, safety constraints, and non-obvious workarounds; omit comments that merely restate the code.
- Keep user-facing errors concise and useful. Never expose secrets or sensitive member data.

## Backend

- Keep synchronous database work in synchronous `def` routes. Use `async def` only when the full I/O path is asynchronous.
- Preserve transactional check-in and checkout behavior, deterministic stand locking, session boundaries, and property scoping.
- Use shared, deterministic Pytest fixtures for database and client setup.
- Use SQLAlchemy 2.0 Core tables, connections, and transactions—not legacy ORM `Query` APIs.
- PostgreSQL only. SQLite is no longer supported anywhere, including tests.

## Frontend

- Use vanilla DOM APIs; do not introduce React, Vue, jQuery, Tailwind, or another frontend framework.
- Keep each page's HTML, CSS, and JavaScript together under `frontend/home`, `frontend/login`, or `frontend/property`; shared files belong under `frontend/shared`.
- Prefer semantic HTML and CSS classes. Inline custom properties are acceptable for data-driven map values.
- Preserve keyboard access, readable status messages, 44px touch targets, reduced-motion support, and mobile viewport behavior.

## Verification

- Backend: `backend/venv/bin/pytest -q backend`
- JavaScript syntax: `node --check frontend/home/home.js`, `node --check frontend/login/login.js`, and `node --check frontend/property/property.js`
- Run checks relevant to the changed files and report what ran and what could not be verified.

## Code Review Rules

- Report only concrete failures with an exact file and line, the triggering condition, and a specific fix.
- Prioritize correctness, authorization, data exposure, transaction safety, portability, and regressions.
- Do not pad reviews with generic advice, style preferences, or requests for unspecified tests.
- State the final verdict and any behavior that could not be verified.
- Always specify what file and line.
- If I ask how to change something manually, specify the file and line as well.
- Check that changes will not block or complicate later slices in `docs/PLAN.md` (for example, the Postgres move, sign-in, or Vercel deploy). Flag code that would need rework later, but do not ask for future features to be built early.
