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
        checked_in_at TEXT NOT NULL,
        checked_out_at TEXT,
        checkout_source TEXT CHECK (checkout_source IS NULL OR checkout_source IN ('auto', 'member')),
        guest_name TEXT,
        guest_phone TEXT,
        FOREIGN KEY (stand_id) REFERENCES stands(id),
        FOREIGN KEY (member_id) REFERENCES members(id)
    );
"""


# Opens a connection to the real database file, with settings the app needs
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


# Creates the tables if they don't already exist
def init_db():
    conn = get_connection()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
