from app.database import get_connection
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()


origins = ["http://localhost:5500", "http://127.0.0.1:5500"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # List of allowed origins
    allow_credentials=True,  # Allow cookies and authentication headers
    allow_methods=["*"],  # Allow all standard HTTP methods (GET, POST, etc.)
    allow_headers=["*"],  # Allow all browser headers
)


@app.get("/api/stands")
def list_stands():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM stands").fetchall()
    conn.close()
    return rows


@app.get("/api/map-features")
def list_map_features():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM map_features").fetchall()
    conn.close()
    return rows
