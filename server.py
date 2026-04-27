import os
import json
import requests
import psycopg2
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.getenv("DATABASE_URL")
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY")


def get_db():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS queue (
            id SERIAL PRIMARY KEY,
            audio_url TEXT NOT NULL,
            title TEXT NOT NULL,
            requested_by TEXT NOT NULL,
            tokens INTEGER DEFAULT 0,
            played BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    conn.commit()
    cur.close()
    conn.close()


init_db()


class SongRequest(BaseModel):
    query: str
    requested_by: str
    tokens: int = 0


def search_pixabay(query: str):
    url = "https://pixabay.com/api/music/"
    params = {
        "key": PIXABAY_API_KEY,
        "q": query,
        "per_page": 5
    }
    response = requests.get(url, params=params)
    data = response.json()

    if data.get("hits"):
        item = data["hits"][0]
        return {
            "audio_url": item["audio"]["mp3"],
            "title": item["title"]
        }
    return None


def add_to_queue(audio_url, title, requested_by, tokens):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO queue (audio_url, title, requested_by, tokens) VALUES (%s, %s, %s, %s)",
        (audio_url, title, requested_by, tokens)
    )
    conn.commit()
    cur.close()
    conn.close()


@app.get("/overlay")
def serve_overlay():
    file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "music_overlay.html")
    return FileResponse(file_path)


@app.post("/request")
def request_song(req: SongRequest):
    track = search_pixabay(req.query)
    if not track:
        return {"error": "Track not found"}
    add_to_queue(track["audio_url"], track["title"], req.requested_by, req.tokens)
    return {"success": True, "title": track["title"]}


@app.get("/queue")
def get_queue():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, audio_url, title, requested_by, tokens
        FROM queue
        WHERE played = FALSE
        ORDER BY tokens DESC, created_at ASC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{"id": r[0], "audio_url": r[1], "title": r[2], "requested_by": r[3], "tokens": r[4]} for r in rows]


@app.post("/played/{song_id}")
def mark_played(song_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE queue SET played = TRUE WHERE id = %s", (song_id,))
    conn.commit()
    cur.close()
    conn.close()
    return {"success": True}


@app.delete("/clear")
def clear_queue():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM queue WHERE played = FALSE")
    conn.commit()
    cur.close()
    conn.close()
    return {"success": True}


@app.post("/sync")
async def sync_queue(request: Request):
    data = await request.json()
    queue_str = data.get("queue", "[]")

    try:
        queue = json.loads(queue_str)
        conn = get_db()
        cur = conn.cursor()

        for item in queue:
            query = item.get("query", "")
            user = item.get("user", "unknown")
            tokens = item.get("tokens", 0)

            if query:
                track = search_pixabay(query)
                if track:
                    cur.execute(
                        "SELECT id FROM queue WHERE title = %s AND played = FALSE",
                        (track["title"],)
                    )
                    exists = cur.fetchone()
                    if not exists:
                        cur.execute(
                            "INSERT INTO queue (audio_url, title, requested_by, tokens) VALUES (%s, %s, %s, %s)",
                            (track["audio_url"], track["title"], user, tokens)
                        )

        conn.commit()
        cur.close()
        conn.close()

    except Exception as e:
        return {"error": str(e)}

    return {"status": "ok"}
