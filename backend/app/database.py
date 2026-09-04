import sqlite3
from pathlib import Path

DB_PATH = (
    Path(__file__).parent.parent / "blackcreek.db"
)  # Points at backend/blackcreek.db no matter where you run

# Table structure for the whole app
SCHEMA = """
    CREATE TABLE IF NOT EXISTS stands (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        type TEXT NOT NULL,
        lat REAL NOT NULL,
        lng REAL NOT NULL,
        capacity INTEGER NOT NULL DEFAULT 1,
        preferred_winds TEXT,
        is_retired INTEGER NOT NULL DEFAULT 0 CHECK (is_retired IN (0, 1))
    );

    CREATE TABLE IF NOT EXISTS map_features (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        type TEXT NOT NULL,
        lat REAL NOT NULL,
        lng REAL NOT NULL
    );

    CREATE TABLE IF NOT EXISTS members (
        id TEXT PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        is_admin INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0, 1)),
        first_name TEXT NOT NULL,
        last_name TEXT NOT NULL,
        created_at TEXT NOT NULL
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


def ensure_current_schema(conn):
    """Apply the small local migration needed by the current development schema."""
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

    if stand_columns or hunt_columns:
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
