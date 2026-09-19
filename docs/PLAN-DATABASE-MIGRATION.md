# Database Migration

## Result

The backend now uses PostgreSQL on Neon instead of SQLite.

The application, command-line tools, and backend tests all use SQLAlchemy 2.0
Core with the Psycopg PostgreSQL driver. SQLite support was removed rather than
maintaining two database implementations.

## What Changed

### Database layer

- Replaced the SQLite schema string and migration helpers with SQLAlchemy table
  definitions.
- Kept the existing properties, stands, map features, members, and hunts data
  model.
- Preserved foreign keys, unique values, defaults, nullable authentication
  fields, and the allowed checkout-source constraint.
- Kept unrestricted string fields as PostgreSQL `TEXT`, matching the old
  SQLite schema.
- Added one shared SQLAlchemy engine with connection pooling and connection
  health checks.
- Prefer `DATABASE_URL_POOLED` when present, then fall back to `DATABASE_URL`.
- Convert Neon's `postgresql://` URLs to the Psycopg driver format expected by
  SQLAlchemy.
- Create missing tables with `metadata.create_all()`.

### Queries and transactions

- Converted SQLite `?` parameters to named SQLAlchemy parameters.
- Converted database results to mapping rows where the application reads
  columns by name.
- Replaced SQLite `lastrowid` with PostgreSQL `RETURNING id`.
- Preserved atomic host-and-guest check-in and checkout transactions.
- Replaced SQLite's database-wide write lock with PostgreSQL row locks.
- Check-in locks requested stands with `SELECT ... FOR UPDATE` in sorted order.
  The stable order prevents deadlocks when two check-ins request the same
  stands.
- Checkout locks its hunt row before checking and updating it.

### Authentication

- Converted member and Google-account queries to SQLAlchemy.
- Kept the allowlist, password, Google subject ID, session, and admin rules
  unchanged.
- Moved synchronous database work in the async Google callback to a worker
  thread. A slow database request can no longer pause other async requests.

### Management and seed tools

- Converted `manage.py` account creation and password reset to SQLAlchemy.
- Converted `seed.py` to named SQLAlchemy queries and PostgreSQL upserts.
- `seed.py` now loads `.env`, creates missing tables, and then inserts or
  updates properties, stands, and map features.
- Seeding does not copy local SQLite hunt history.

### Tests

- Ported backend fixtures and queries from SQLite to PostgreSQL.
- Added `TEST_DATABASE_URL` for a separate Neon test database.
- Tests refuse to start if the test URL is missing or points to the app
  database.
- The safety check treats Neon's direct and pooled URLs as aliases for the same
  database.
- Normal tests use transactions and savepoints so their changes roll back.
- Concurrency and command-line tests use committed rows, then delete their
  hunts, stands, and members.
- The test database has the schema it needs but was not demo-seeded.

## Environment Variables

```dotenv
DATABASE_URL=postgresql://...
DATABASE_URL_POOLED=postgresql://...
TEST_DATABASE_URL=postgresql://...
```

`TEST_DATABASE_URL` must point to a separate Neon database or branch. Real
connection strings stay in `.env`, which is ignored by Git.

## Verification

- The full backend suite passed against the separate Neon test database: 193
  tests.
- After the final Google callback change, all 13 Google authentication tests
  passed.
- The test database safety guard rejects direct and pooled URLs for the same
  Neon database.
- Python compilation, JavaScript syntax, and Git whitespace checks passed.

Real Google OAuth, browser and phone use, and Vercel deployment were not part of
this migration verification.
