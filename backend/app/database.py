import os

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    create_engine,
)

# Stands and features with no property named are treated as this one.
PRIMARY_PROPERTY_ID = "black-creek"

metadata = MetaData()

properties = Table(
    "properties",
    metadata,
    Column("id", Text, primary_key=True),
    Column("slug", Text, unique=True, nullable=False),
    Column("name", Text, nullable=False),
    Column("description", Text),
    Column("center_lat", Float, nullable=False),
    Column("center_lng", Float, nullable=False),
    Column("default_zoom", Integer, nullable=False, server_default="15"),
    Column("is_active", Boolean, nullable=False, server_default="true"),
)

stands = Table(
    "stands",
    metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("type", Text, nullable=False),
    Column("lat", Float, nullable=False),
    Column("lng", Float, nullable=False),
    Column("capacity", Integer, nullable=False, server_default="1"),
    Column("preferred_winds", Text),
    Column("is_retired", Boolean, nullable=False, server_default="false"),
    Column(
        "property_id",
        Text,
        ForeignKey("properties.id"),
        nullable=False,
        server_default=PRIMARY_PROPERTY_ID,
    ),
)

map_features = Table(
    "map_features",
    metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("type", Text, nullable=False),
    Column("lat", Float, nullable=False),
    Column("lng", Float, nullable=False),
    Column(
        "property_id",
        Text,
        ForeignKey("properties.id"),
        nullable=False,
        server_default=PRIMARY_PROPERTY_ID,
    ),
)

members = Table(
    "members",
    metadata,
    Column("id", Text, primary_key=True),
    Column("email", Text, unique=True, nullable=False),
    # null for members who only sign in with google; one person may hold both
    Column("password_hash", Text),
    # google's stable subject claim. never match on email alone after the first
    # sign-in: a google account's email can change, the subject cannot
    Column("google_sub", Text, unique=True),
    Column("is_admin", Boolean, nullable=False, server_default="false"),
    Column("first_name", Text, nullable=False),
    Column("last_name", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("last_login_at", Text),
)

hunts = Table(
    "hunts",
    metadata,
    # postgres assigns this itself, so inserts read it back with RETURNING
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("stand_id", Text, ForeignKey("stands.id"), nullable=False),
    Column("member_id", Text, ForeignKey("members.id"), nullable=False),
    Column("host_hunt_id", Integer, ForeignKey("hunts.id")),
    Column("checked_in_at", Text, nullable=False),
    Column("checked_out_at", Text),
    Column("checkout_source", Text),
    Column("guest_name", Text),
    Column("guest_phone", Text),
    CheckConstraint(
        "checkout_source IS NULL OR checkout_source IN ('auto', 'member')",
        name="ck_hunts_checkout_source",
    ),
)


# Neon hands out plain postgresql:// urls; sqlalchemy needs the driver named.
def normalize_database_url(url):
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def database_url():
    url = os.environ.get("DATABASE_URL_POOLED") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy the connection string from the Neon "
            "dashboard into .env."
        )
    return normalize_database_url(url)


_engine = None


# One engine for the whole process. It holds the connection pool, so building a
# second one per request would open connections Neon then refuses.
def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(database_url(), pool_pre_ping=True, future=True)
    return _engine


# Callers close what they open, matching how the routes are already written.
def get_connection():
    return get_engine().connect()


def init_db():
    metadata.create_all(get_engine())
