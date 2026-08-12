import json
from pathlib import Path

from app.database import get_connection

DATA = Path(__file__).parent.parent / "data" / "stands.json"

with open(DATA) as f:
    data = json.load(f)

stands = data["stands"]

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

conn.commit()
conn.close()
