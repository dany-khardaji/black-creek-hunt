# Black Creek Hunt: Beta Launch Plan

## Summary

Ship a secure, polished club beta in reviewable slices while keeping the system understandable.

- Continue on `feature/v1-beta`. Use one reviewable commit per slice.
- Three screens: sign-in, a homepage listing three properties, and the property map.
- Sign in with Google, or with a manually created email/password account. Pre-authorized members only; no public signup either way.
- Slices 1 and 2 are complete. Remaining work is properties, sign-in, pages, database portability, and deployment.
- All of it ships before the first deploy. Launching single-property first would mean migrating a live database and changing bookmarked URLs.
- No framework rewrite. The frontend stays vanilla HTML, CSS, and JavaScript with no build step. See "Later: Learning React."
- After every slice, pause for a structural walkthrough and a five-minute reading/modification exercise.
- Repository baseline: Slices 1 and 2 committed through `098b2e4`; 36 backend tests pass.
- Rollback point: git tag `vanilla-property-page`.

## Implementation Slices

### 1. Phase 4 backend — COMPLETE

- Commit `1e3db01` is the Slice 1 checkpoint.
- Transactional, deterministic stand validation and locking; simultaneous check-ins cannot both succeed.
- Explicit `host_hunt_id` relationships and atomic host-plus-guest checkout.
- `open`, `active`, and eight-hour `overdue` read-time statuses.
- 3:00 AM Eastern session boundary; stale hunts remain historical but inactive.
- SQLite for local development and tests.
- Coverage for guest conflicts, blank fields, missing/retired stands, rollback, ownership, overdue status, stale sessions, concurrency, and DST.

### 2. Vanilla frontend — COMPLETE

- Semantic guest form supporting zero, one, or two guests.
- Guest pickers exclude host, occupied, retired, and already-selected stands by available seats.
- Check-in, authorized host checkout, readable conflicts, preserved form values, immediate post-mutation refresh.
- Persistent Leaflet marker registry updated every 30 seconds; no duplicate-marker polling.
- Live count, initials, guest indicators, status colors, check-in time, loading state, network failures.
- Desktop side panel and mobile bottom sheet; accessible labels, keyboard support, 44px minimum targets.
- URL-linked stand panels; reduced-motion support; iOS Safari viewport, bfcache, and page-zoom handling.
- Dark map, frosted panel, blaze-orange active state, red overdue state, gray open state, seat-gauge markers.
- Known gap: `property.css` names Inter first but no font is loaded; the page falls back to `system-ui`. Load real fonts or drop the declaration.

### 3. Properties in the data model

Backend first. No new dependencies. Keeps the 36-test baseline green throughout.

**Schema**

- New `properties` table: `id`, `slug`, `name`, `description`, `center_lat`, `center_lng`, `default_zoom`, `is_active`.
- Add `property_id` to `stands` and `map_features`, with a default naming the primary property.
- The default is required: roughly 24 test fixtures insert stands with explicit column lists and would otherwise break.
- Follow the existing `ensure_current_schema` migration pattern in `app/database.py`.

**API**

- Scope the map-state query, the feature query, and the per-property live count by property.
- Add a club-wide live count across all properties for the homepage.
- Reject any check-in whose stands span more than one property.
- `app/sessions.py` needs no changes; its helpers are property-agnostic.

**Seed**

- Add a properties seed. Give `data/stands.json` and `data/map-features.json` a property association.
- The existing 7 stands and 4 features belong to the primary property.

**Frontend**

- `property.js` reads the property slug from the URL and takes map center and zoom from the API.
- Replaces the hardcoded `DEFAULT_MAP_CENTER` and `DEFAULT_MAP_ZOOM` constants. Nothing else changes.

### 4. Sign in

Two ways in, one allowlist. Begin by moving the frontend behind FastAPI on a single origin: OAuth needs a stable redirect target, and it removes the CORS configuration and the port-sniffing `API_URL` logic at the top of `property.js`.

**Allowlist**

- Pre-create a member row per authorized person, email included.
- Both sign-in methods check against it. An unknown email is refused either way.
- No public signup.

**Google sign-in**

- Server-side OAuth with Authlib. The redirect flow runs entirely on the server; the login page needs no JavaScript library.
- On first success, match the verified email to the allowlist and store Google's stable subject ID on that member row.
- Require Google's `email_verified` claim. Use the `state` parameter for CSRF protection. Match on email, never display name.

**Manual accounts**

- `password_hash` becomes nullable: Google members have none, manual members do, and one person may have both.
- Hash with `pwdlib` and Argon2.
- Admin CLI creates accounts and resets credentials.

**Sessions**

- 24-hour JWT in an `HttpOnly`, `Secure`, `SameSite=Lax` cookie; token carries only member ID and expiry.
- Identical session handling for both sign-in methods.

**Guards**

- Replace the hardcoded `CURRENT_MEMBER_ID` at its four call sites with the authenticated member.
- Protect properties, map state, check-in, and checkout.
- Permit checkout only for the owning host or an administrator; return `403` otherwise.
- Exclude phone numbers, emails, password hashes, and Google subject IDs from all responses.

**Setup pause**

- Requires a Google Cloud project, OAuth consent screen, client ID and secret, and authorized redirect URIs.
- Redirect URIs differ between local development and production.

### 5. The three pages

- FastAPI serves `/login`, `/` (homepage), and `/property/{slug}`. Three HTML files; no client-side routing.
- Stand deep links survive as `/property/{slug}?stand={id}`.
- Login page: Google as the primary action, the email/password form present but secondary.
- Homepage: logo, club-wide live counter at top right, three property cards with name and short description, logout control.
- Homepage stays bare bones by design. Richer cards are deferred.
- Property page: the existing map, plus a way back to the homepage.
- Every page loads `base.css` (color tokens, reset, base form controls) first, then its own stylesheet. No new color tokens.

### 6. Database portability

- Introduce SQLAlchemy Core as the compatibility layer for local SQLite and production Postgres.
- Use Neon through `DATABASE_URL`.
- Seed fresh demo data: three demo properties, demo stands and features, demo accounts. Do not copy local hunt history.
- Re-run the full backend suite against both SQLite and Postgres.

### 7. Deploy the beta

- Deploy the static frontend and FastAPI together on Vercel Hobby using relative `/api` requests and one origin.
- No build step: HTML, CSS, JS, and `assets/` deploy as-is.
- Configure `DATABASE_URL`, `JWT_SECRET`, Google client ID and secret, application origin, secure cookies, schema initialization, and demo seeding.
- Add the production redirect URI to the Google Cloud console. Sign-in fails silently without it.
- Use the generated HTTPS `vercel.app` address; defer a custom domain.
- Pause for the user to create or sign into Vercel, Neon, and Google Cloud and enter secrets, then verify the deployment.
- Document local setup, account creation for both methods, Google OAuth setup, deployment, database export/backup, and recovery in the README.
- Reconfirm current Vercel and Neon free-tier requirements immediately before deployment.

## Public Interfaces

**Pages**

- `GET /login`, `GET /`, `GET /property/{slug}`: server-served HTML. Unauthenticated requests to the latter two redirect to `/login`.

**Authentication**

- `GET /api/auth/google/login`: redirect to Google's consent screen.
- `GET /api/auth/google/callback`: verify the response, enforce the allowlist, set the session cookie, redirect to the homepage.
- `POST /api/auth/login`: accept email/password for manually created accounts, set the session cookie, return a safe member profile.
- `GET /api/auth/me`: return the authenticated member.
- `POST /api/auth/logout`: clear the session cookie.

**Properties and hunts**

- `GET /api/properties`: name, slug, and description for each active property.
- `GET /api/properties/{slug}`: one property including map center and default zoom.
- `GET /api/map-state?property={slug}`: protected stands/features for that property, its live count, statuses, safe occupant information, and checkout permission.
- `GET /api/live-count`: club-wide active hunter total across all properties.
- `POST /api/hunts`: accept host `stand_id` and up to two guest objects; derive identity exclusively from authentication; reject stands spanning more than one property.
- `POST /api/hunts/{host_hunt_id}/check-out`: atomically close the host hunt and every linked guest.
- Conflict responses expose a stable error code and affected stand ID/name without causing the frontend to clear entered values.

## Verification and Learning Checkpoints

- Preserve the 36-test backend baseline and expand it for properties, both sign-in methods, and database portability.
- Run the backend suite before each relevant commit.
- Explicitly verify concurrent check-ins, 3:00 AM/DST behavior, eight-hour overdue status, expired sessions, unauthorized access, and guest cascade checkout.
- Verify a check-in spanning two properties is refused, and that the club-wide count equals the sum of the per-property counts.
- Verify in the browser: guest stand filtering by available seats, conflict responses preserving entered values, signed-out and signed-in states, and checkout permitted only for the owning host or an admin.
- Confirm signed-out requests return no coordinates in the network response, not merely that the map is hidden.
- Verify both sign-in methods reach the same application, and that an email absent from the allowlist is refused by both.
- Test the full journey on a real phone and a second account: sign in, choose a property, check in with guests, polling, conflicts, checkout, return to the homepage, choose another property.
- Verify stand deep links open the correct stand on the correct property.
- Verify the production deployment with demo data before inviting club members.
- After every slice, cover the system/data flow, language structure, important syntax, architectural reasons, and one small comprehension exercise.

## Later: Learning React

Deferred indefinitely and off the critical path. Recorded so the reasoning is not relitigated.

- This application does not need React. Roughly twenty markers, fifteen to twenty users, and a complexity ceiling this document caps.
- Three pages do not change this. Served by FastAPI, they require no routing code: each page is independent and the session lives in a cookie. React's contribution would be client-side routing, replacing server-side routing that is free, and reintroducing the build step this plan excludes.
- A framework should not precede fluency in the underlying HTML, CSS, and JavaScript. Learning both at once makes every bug ambiguous.
- When the time comes, do not migrate this application. Roughly two-thirds of that work is wrapping Leaflet, which is an advanced React topic and teaches the framework's escape hatches rather than its core model.
- Instead: a throwaway project with fake data and no map, rebuilding only the check-in panel — guest rows, add/remove, the two-guest cap, and seat-availability filtering.
- Demonstration: overwrite the fake stand list on a timer, then type into a guest field. The text survives with no draft-capture code, which is what `drafts`, `getDraft`, `captureDraft`, and their eight call sites exist to do by hand.
- Revisit only if the application gains substantially more screens, other contributors, or a materially more complex check-in flow.

## Assumptions and Boundaries

- This plan authorizes implementation despite the project's earlier teaching-only guidance.
- The property page's live counter shows that property's hunters. The homepage counter is the club-wide total.
- All members see all three properties. No per-property permissions.
- Properties are seeded through data files and the CLI, not managed in the application.
- `is_admin` on the existing members table gates administrative checkout. No new roles.
- The first deployment uses demo properties, stands, and accounts; real identities, coordinates, features, and boundaries are added only before club invitations.
- The members-only beta targets approximately 15–20 users.
- Launch-ready means secure core behavior, responsive styling, understandable errors, phone verification, documentation, and deployment.
- Deferred: clustering, zoom-based detail, onboarding tooltip, custom logo, photos, property boundary, and richer homepage cards.
- Excluded: public signup, password-change UI, offline queue, notifications, reservations, wind data, admin dashboard, portfolio-demo mode, and any frontend framework or build step.
- Teaching pauses explain the structure without turning each slice into a prolonged syntax course; focused language drills happen after launch.
