import os
import json
import re
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
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
MAX_DURATION = 600  # максимум 10 минут в секундах


def get_db():
    return psycopg2.connect(DATABASE_URL)

@app.get("/test-youtube")
def test_youtube():
    result = get_video_info("dQw4w9WgXcQ")
    return {"result": result}


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


class VideoRequest(BaseModel):
    video_id: str
    requested_by: str
    tokens: int = 0


def extract_video_id(url: str):
    patterns = [
        r'(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/)([a-zA-Z0-9_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    if re.match(r'^[a-zA-Z0-9_-]{11}$', url):
        return url
    return None


def parse_duration(duration: str):
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration)
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def get_video_info(video_id: str):
    try:
        url = "https://www.googleapis.com/youtube/v3/videos"
        params = {
            "part": "snippet,contentDetails,status",
            "id": video_id,
            "key": YOUTUBE_API_KEY
        }
        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        items = data.get("items", [])
        if not items:
            return None, "Video not found"

        item = items[0]
        status = item.get("status", {})
        duration_str = item["contentDetails"]["duration"]
        duration = parse_duration(duration_str)
        title = item["snippet"]["title"]

        if not status.get("embeddable"):
            return None, "This video cannot be embedded"

        if duration > MAX_DURATION:
            mins = MAX_DURATION // 60
            return None, f"Video is too long! Maximum {mins} minutes allowed"

        return {"video_id": video_id, "title": title, "duration": duration}, None

    except Exception as e:
        return None, str(e)


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


@app.get("/overlay")
def serve_overlay():
    file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "music_overlay.html")
    return FileResponse(file_path)


@app.post("/request")
def request_video(req: VideoRequest):
    video, error = get_video_info(req.video_id)
    if error:
        return {"error": error}
    add_to_queue(video["video_id"], video["title"], req.requested_by, req.tokens)
    return {"success": True, "title": video["title"]}


@app.get("/queue")
def get_queue():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, video_id, title, requested_by, tokens
        FROM queue
        WHERE played = FALSE
        ORDER BY tokens DESC, created_at ASC
    """)
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


@app.post("/skip")
def skip_current():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE queue SET played = TRUE
        WHERE id = (
            SELECT id FROM queue WHERE played = FALSE
            ORDER BY tokens DESC, created_at ASC LIMIT 1
        )
    """)
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
            url = item.get("url", "")
            user = item.get("user", "unknown")
            tokens = item.get("tokens", 0)

            if url:
                video_id = extract_video_id(url)
                if video_id:
                    video, error = get_video_info(video_id)
                    if video:
                        cur.execute(
                            "SELECT id FROM queue WHERE video_id = %s AND played = FALSE",
                            (video_id,)
                        )
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
