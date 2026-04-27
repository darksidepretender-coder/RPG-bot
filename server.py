import os
import requests
import psycopg2
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
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
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

# ── База данных ──
def get_db():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS queue (
            id SERIAL PRIMARY KEY,
            video_id TEXT NOT NULL,
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

# ── Модели ──
class SongRequest(BaseModel):
    query: str
    requested_by: str
    tokens: int = 0

# ── YouTube поиск ──
def search_youtube(query: str):
    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": 1,
        "key": YOUTUBE_API_KEY
    }
    response = requests.get(url, params=params)
    data = response.json()
    if data.get("items"):
        item = data["items"][0]
        return {
            "video_id": item["id"]["videoId"],
            "title": item["snippet"]["title"]
        }
    return None

def add_to_queue(video_id, title, requested_by, tokens):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO queue (video_id, title, requested_by, tokens) VALUES (%s, %s, %s, %s)",
        (video_id, title, requested_by, tokens)
    )
    conn.commit()
    cur.close()
    conn.close()

# ── Эндпоинты ──
@app.post("/request")
def request_song(req: SongRequest):
    video = search_youtube(req.query)
    if not video:
        return {"error": "Track not found"}
    add_to_queue(video["video_id"], video["title"], req.requested_by, req.tokens)
    return {"success": True, "title": video["title"], "video_id": video["video_id"]}

@app.get("/queue")
def get_queue():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, video_id, title, requested_by, tokens FROM queue WHERE played = FALSE ORDER BY tokens DESC, created_at ASC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{"id": r[0], "video_id": r[1], "title": r[2], "requested_by": r[3], "tokens": r[4]} for r in rows]

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

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    event_type = data.get("type")

    if event_type == "chatMessage":
        message = data.get("object", {})
        text = message.get("message", "").strip()
        user = message.get("user", {}).get("username", "unknown")

        if text.lower().startswith("!song "):
            query = text[6:].strip()
            video = search_youtube(query)
            if video:
                add_to_queue(video["video_id"], video["title"], user, 0)

    if event_type == "tip":
        tip = data.get("object", {})
        user = tip.get("from_user", "unknown")
        tokens = int(tip.get("amount", 0))
        note = tip.get("message", "").strip()

        if note.lower().startswith("!song "):
            query = note[6:].strip()
            video = search_youtube(query)
            if video:
                add_to_queue(video["video_id"], video["title"], user, tokens)

    @app.post("/sync")
async def sync_queue(request: Request):
    data = await request.json()
    queue_str = data.get("queue", "[]")
    
    try:
        import json
        queue = json.loads(queue_str)
        
        conn = get_db()
        cur = conn.cursor()
        
        for item in queue:
            query = item.get("query", "")
            user = item.get("user", "unknown")
            tokens = item.get("tokens", 0)
            
            if query:
                video = search_youtube(query)
                if video:
                    # Проверяем нет ли уже в очереди
                    cur.execute("SELECT id FROM queue WHERE title = %s AND played = FALSE", (video["title"],))
                    exists = cur.fetchone()
                    if not exists:
                        cur.execute(
                            "INSERT INTO queue (video_id, title, requested_by, tokens) VALUES (%s, %s, %s, %s)",
                            (video["video_id"], video["title"], user, tokens)
                        )
        
        conn.commit()
        cur.close()
        conn.close()
        
    except Exception as e:
        return {"error": str(e)}
    
    return {"status": "ok"}

    return {"status": "ok"}
