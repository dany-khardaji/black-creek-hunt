import json
from pathlib import Path

from app.database import get_connection

STANDS = Path(__file__).parent.parent / "data" / "stands.json"
FEATURES = Path(__file__).parent.parent / "data" / "map-features.json"

with open(STANDS) as f:
    stands_data = json.load(f)
with open(FEATURES) as f:
    features_data = json.load(f)

stands = stands_data["stands"]
features = features_data["features"]

conn = get_connection()
for stand in stands:
    conn.execute(
        """
        INSERT OR REPLACE INTO stands (id, name, type, lat, lng, capacity, preferred_winds)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            stand["id"],
            stand["name"],
            stand["type"],
            stand["lat"],
            stand["lng"],
            stand["capacity"],
            json.dumps(stand["preferred_winds"]),
        ),
    )

for feature in features:
    conn.execute(
        """
        INSERT OR REPLACE INTO map_features (id, name, type, lat, lng)
        VALUES (?, ?, ?, ?, ?)
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
