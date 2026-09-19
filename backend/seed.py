import json
from pathlib import Path

# Importing settings loads the local .env before the database module reads its
# connection URL. Deployments still use their existing environment variables.
from app import config
from app.database import PRIMARY_PROPERTY_ID, get_connection, init_db
from sqlalchemy import text

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

# Makes the tables first, so seeding a fresh database works.
init_db()
conn = get_connection()

# Properties must exist before stands and features can reference them.
for prop in properties:
    conn.execute(
        text(
            """
            INSERT INTO properties (
                id, slug, name, description, center_lat, center_lng, default_zoom
            )
            VALUES (
                :id, :slug, :name, :description, :center_lat, :center_lng,
                :default_zoom
            )
            ON CONFLICT(id) DO UPDATE SET
                slug = excluded.slug,
                name = excluded.name,
                description = excluded.description,
                center_lat = excluded.center_lat,
                center_lng = excluded.center_lng,
                default_zoom = excluded.default_zoom
            """
        ),
        {
            "id": prop["id"],
            "slug": prop["slug"],
            "name": prop["name"],
            "description": prop["description"],
            "center_lat": prop["center_lat"],
            "center_lng": prop["center_lng"],
            "default_zoom": prop["default_zoom"],
        },
    )

# Insert or update every stand from the json into the database
for stand in stands:
    conn.execute(
        text(
            """
            INSERT INTO stands (
                id, name, type, lat, lng, capacity, preferred_winds, property_id
            )
            VALUES (
                :id, :name, :type, :lat, :lng, :capacity, :preferred_winds,
                :property_id
            )
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                type = excluded.type,
                lat = excluded.lat,
                lng = excluded.lng,
                capacity = excluded.capacity,
                preferred_winds = excluded.preferred_winds,
                property_id = excluded.property_id
            """
        ),
        {
            "id": stand["id"],
            "name": stand["name"],
            "type": stand["type"],
            "lat": stand["lat"],
            "lng": stand["lng"],
            "capacity": stand["capacity"],
            # store the list as a json string
            "preferred_winds": json.dumps(stand["preferred_winds"]),
            "property_id": stand.get("property_id", PRIMARY_PROPERTY_ID),
        },
    )

# Insert or update every map feature from the json into the database
for feature in features:
    conn.execute(
        text(
            """
            INSERT INTO map_features (id, name, type, lat, lng, property_id)
            VALUES (:id, :name, :type, :lat, :lng, :property_id)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                type = excluded.type,
                lat = excluded.lat,
                lng = excluded.lng,
                property_id = excluded.property_id
            """
        ),
        {
            "id": feature["id"],
            "name": feature["name"],
            "type": feature["type"],
            "lat": feature["lat"],
            "lng": feature["lng"],
            "property_id": feature.get("property_id", PRIMARY_PROPERTY_ID),
        },
    )
conn.commit()
conn.close()
