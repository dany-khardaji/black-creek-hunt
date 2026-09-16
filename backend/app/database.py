import sqlite3
from pathlib import Path

DB_PATH = (
    Path(__file__).parent.parent / "blackcreek.db"
)  # Points at backend/blackcreek.db no matter where you run

# Stands and features with no property named are treated as this one.
PRIMARY_PROPERTY_ID = "black-creek"

# Table structure for the whole app
SCHEMA = f"""
    CREATE TABLE IF NOT EXISTS properties (
        id TEXT PRIMARY KEY,
        slug TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        description TEXT,
        center_lat REAL NOT NULL,
        center_lng REAL NOT NULL,
        default_zoom INTEGER NOT NULL DEFAULT 15,
        is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
    );

    CREATE TABLE IF NOT EXISTS stands (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        type TEXT NOT NULL,
        lat REAL NOT NULL,
        lng REAL NOT NULL,
        capacity INTEGER NOT NULL DEFAULT 1,
        preferred_winds TEXT,
        is_retired INTEGER NOT NULL DEFAULT 0 CHECK (is_retired IN (0, 1)),
        property_id TEXT NOT NULL DEFAULT '{PRIMARY_PROPERTY_ID}'
            REFERENCES properties(id)
    );

    CREATE TABLE IF NOT EXISTS map_features (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        type TEXT NOT NULL,
        lat REAL NOT NULL,
        lng REAL NOT NULL,
        property_id TEXT NOT NULL DEFAULT '{PRIMARY_PROPERTY_ID}'
            REFERENCES properties(id)
    );

    CREATE TABLE IF NOT EXISTS members (
        id TEXT PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        -- Null for members who only sign in with Google. A member may hold
        -- both a password and a Google identity.
        password_hash TEXT,
        -- Google's stable subject claim. Never match members on email alone
        -- after first sign-in: a Google account's email can change, the
        -- subject cannot.
        google_sub TEXT UNIQUE,
        is_admin INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0, 1)),
        first_name TEXT NOT NULL,
        last_name TEXT NOT NULL,
        created_at TEXT NOT NULL,
        last_login_at TEXT
    );

    CREATE TABLE IF NOT EXISTS hunts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stand_id TEXT NOT NULL,
        member_id TEXT NOT NULL,
        host_hunt_id INTEGER,
        checked_in_at TEXT NOT NULL,
        checked_out_at TEXT,
        checkout_source TEXT CHECK (checkout_source IS NULL OR checkout_source IN ('auto', 'member')),
        guest_name TEXT,
        guest_phone TEXT,
        FOREIGN KEY (stand_id) REFERENCES stands(id),
        FOREIGN KEY (member_id) REFERENCES members(id),
        FOREIGN KEY (host_hunt_id) REFERENCES hunts(id)
    );
"""


# Lets members exist without a password, for Google sign-in. SQLite cannot
# change that rule on an existing table, so the table is rebuilt instead.
def migrate_members_for_auth(conn):
    columns = conn.execute("PRAGMA table_info(members)").fetchall()
    if not columns:
        return  # Fresh database: SCHEMA already has the current shape.

    password_hash_required = any(
        column["name"] == "password_hash" and column["notnull"] for column in columns
    )
    names = {column["name"] for column in columns}
    if not password_hash_required and "google_sub" in names:
        if "last_login_at" not in names:
            conn.execute("ALTER TABLE members ADD COLUMN last_login_at TEXT")
            conn.commit()
        return

    # Hunt rows point at members, so the link is switched off while the table is
    # swapped or the old hunts would be deleted along with it.
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN")
        conn.execute(
            """
            CREATE TABLE members_migrated (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT,
                google_sub TEXT UNIQUE,
                is_admin INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0, 1)),
                first_name TEXT NOT NULL,
                last_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_login_at TEXT
            )
            """
        )
        # Emails are lowercased as they move over, so capital letters never
        # stop a member matching their account.
        conn.execute(
            """
            INSERT INTO members_migrated (
                id, email, password_hash, is_admin,
                first_name, last_name, created_at
            )
            SELECT id, LOWER(email), password_hash, is_admin,
                   first_name, last_name, created_at
            FROM members
            """
        )
        conn.execute("DROP TABLE members")
        conn.execute("ALTER TABLE members_migrated RENAME TO members")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


# Brings an older database file up to the shape the app expects.
def ensure_current_schema(conn):
    migrate_members_for_auth(conn)

    stand_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(stands)").fetchall()
    }
    hunt_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(hunts)").fetchall()
    }

    if stand_columns and "capacity" not in stand_columns:
        conn.execute(
            "ALTER TABLE stands ADD COLUMN capacity INTEGER NOT NULL DEFAULT 1"
        )

    if hunt_columns and "host_hunt_id" not in hunt_columns:
        conn.execute(
            "ALTER TABLE hunts ADD COLUMN host_hunt_id INTEGER REFERENCES hunts(id)"
        )
        conn.execute(
            """
            UPDATE hunts AS guest
            SET host_hunt_id = (
                SELECT host.id
                FROM hunts AS host
                WHERE host.member_id = guest.member_id
                AND host.checked_in_at = guest.checked_in_at
                AND host.guest_name IS NULL
                ORDER BY host.id
                LIMIT 1
            )
            WHERE guest.guest_name IS NOT NULL
            AND guest.host_hunt_id IS NULL
            """
        )

    if hunt_columns:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hunts_host_hunt_id ON hunts(host_hunt_id)"
        )

    feature_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(map_features)").fetchall()
    }

    # Older stands and features are all assigned to the main property. The link
    # back to that table is left off here because SQLite will not add one to an
    # existing table.
    if stand_columns and "property_id" not in stand_columns:
        conn.execute(
            "ALTER TABLE stands ADD COLUMN property_id TEXT NOT NULL "
            f"DEFAULT '{PRIMARY_PROPERTY_ID}'"
        )

    if feature_columns and "property_id" not in feature_columns:
        conn.execute(
            "ALTER TABLE map_features ADD COLUMN property_id TEXT NOT NULL "
            f"DEFAULT '{PRIMARY_PROPERTY_ID}'"
        )

    if stand_columns or hunt_columns or feature_columns:
        conn.commit()


# Opens a connection to the real database file, with settings the app needs
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    ensure_current_schema(conn)
    return conn


# Creates the tables if they don't already exist
def init_db():
    conn = get_connection()
    conn.executescript(SCHEMA)
    ensure_current_schema(conn)
    conn.commit()
    conn.close()
