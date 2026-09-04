import json
from pathlib import Path

from app.database import get_connection

STANDS = Path(__file__).parent.parent / "data" / "stands.json"
FEATURES = Path(__file__).parent.parent / "data" / "map-features.json"

# load the raw json files off disk
with open(STANDS) as f:
    stands_data = json.load(f)
with open(FEATURES) as f:
    features_data = json.load(f)

stands = stands_data["stands"]
features = features_data["features"]

conn = get_connection()

# Insert or update every stand from the json into the database
for stand in stands:
    conn.execute(
        """
        INSERT INTO stands (id, name, type, lat, lng, capacity, preferred_winds)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            type = excluded.type,
            lat = excluded.lat,
            lng = excluded.lng,
            capacity = excluded.capacity,
            preferred_winds = excluded.preferred_winds
        """,
        (
            stand["id"],
            stand["name"],
            stand["type"],
            stand["lat"],
            stand["lng"],
            stand["capacity"],
            json.dumps(stand["preferred_winds"]),  # store the list as a json string
        ),
    )

# Insert or update every map feature from the json into the database
for feature in features:
    conn.execute(
        """
        INSERT INTO map_features (id, name, type, lat, lng)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            type = excluded.type,
            lat = excluded.lat,
            lng = excluded.lng
        """,
        (
            feature["id"],
            feature["name"],
            feature["type"],
            feature["lat"],
            feature["lng"],
        ),
    )
conn.commit()
conn.close()
