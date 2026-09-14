import json
from pathlib import Path

from app.database import PRIMARY_PROPERTY_ID, get_connection

PROPERTIES = Path(__file__).parent.parent / "data" / "properties.json"
STANDS = Path(__file__).parent.parent / "data" / "stands.json"
FEATURES = Path(__file__).parent.parent / "data" / "map-features.json"

# load the raw json files off disk
with open(PROPERTIES) as f:
    properties_data = json.load(f)
with open(STANDS) as f:
    stands_data = json.load(f)
with open(FEATURES) as f:
    features_data = json.load(f)

properties = properties_data["properties"]
stands = stands_data["stands"]
features = features_data["features"]

conn = get_connection()

# Properties must exist before stands and features can reference them.
for prop in properties:
    conn.execute(
        """
        INSERT INTO properties (
            id, slug, name, description, center_lat, center_lng, default_zoom
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            slug = excluded.slug,
            name = excluded.name,
            description = excluded.description,
            center_lat = excluded.center_lat,
            center_lng = excluded.center_lng,
            default_zoom = excluded.default_zoom
        """,
        (
            prop["id"],
            prop["slug"],
            prop["name"],
            prop["description"],
            prop["center_lat"],
            prop["center_lng"],
            prop["default_zoom"],
        ),
    )

# Insert or update every stand from the json into the database
for stand in stands:
    conn.execute(
        """
        INSERT INTO stands (
            id, name, type, lat, lng, capacity, preferred_winds, property_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            type = excluded.type,
            lat = excluded.lat,
            lng = excluded.lng,
            capacity = excluded.capacity,
            preferred_winds = excluded.preferred_winds,
            property_id = excluded.property_id
        """,
        (
            stand["id"],
            stand["name"],
            stand["type"],
            stand["lat"],
            stand["lng"],
            stand["capacity"],
            json.dumps(stand["preferred_winds"]),  # store the list as a json string
            stand.get("property_id", PRIMARY_PROPERTY_ID),
        ),
    )

# Insert or update every map feature from the json into the database
for feature in features:
    conn.execute(
        """
        INSERT INTO map_features (id, name, type, lat, lng, property_id)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            type = excluded.type,
            lat = excluded.lat,
            lng = excluded.lng,
            property_id = excluded.property_id
        """,
        (
            feature["id"],
            feature["name"],
            feature["type"],
            feature["lat"],
            feature["lng"],
            feature.get("property_id", PRIMARY_PROPERTY_ID),
        ),
    )
conn.commit()
conn.close()
