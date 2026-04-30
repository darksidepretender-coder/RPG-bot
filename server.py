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

LEVELS = [
    {"min": 0,    "title": "Newcomer",  "emoji": "🧙"},
    {"min": 100,  "title": "Warrior",   "emoji": "⚔️"},
    {"min": 500,  "title": "Knight",    "emoji": "🏰"},
    {"min": 1000, "title": "King",      "emoji": "👑"},
    {"min": 5000, "title": "Legend",    "emoji": "🐉"},
]


def get_db():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS players (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            tokens INTEGER DEFAULT 0,
            level INTEGER DEFAULT 0,
            title TEXT DEFAULT 'Newcomer',
            emoji TEXT DEFAULT '🧙',
            tips_count INTEGER DEFAULT 0,
            largest_tip INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS achievements (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL,
            achievement TEXT NOT NULL,
            earned_at TIMESTAMP DEFAULT NOW()
        )
    """)
    conn.commit()
    cur.close()
    conn.close()


init_db()


def get_level(tokens: int):
    level = LEVELS[0]
    for i, l in enumerate(LEVELS):
        if tokens >= l["min"]:
            level = {**l, "level": i}
    return level


def check_achievements(username: str, tokens: int, tip_amount: int, tips_count: int):
    conn = get_db()
    cur = conn.cursor()
    new_achievements = []

    achievements_to_check = [
        ("First Tip", tips_count == 1),
        ("Big Spender", tip_amount >= 100),
        ("Whale", tip_amount >= 500),
        ("Loyal Fan", tips_count >= 10),
        ("Warrior Rank", tokens >= 100),
        ("Knight Rank", tokens >= 500),
        ("King Rank", tokens >= 1000),
        ("Legend Rank", tokens >= 5000),
    ]

    for achievement, condition in achievements_to_check:
        if condition:
            cur.execute(
                "SELECT id FROM achievements WHERE username = %s AND achievement = %s",
                (username, achievement)
            )
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO achievements (username, achievement) VALUES (%s, %s)",
                    (username, achievement)
                )
                new_achievements.append(achievement)

    conn.commit()
    cur.close()
    conn.close()
    return new_achievements


class TipEvent(BaseModel):
    username: str
    amount: int


class PlayerQuery(BaseModel):
    username: str


@app.post("/tip")
def process_tip(tip: TipEvent):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT tokens, tips_count, largest_tip FROM players WHERE username = %s", (tip.username,))
    player = cur.fetchone()

    if player:
        new_tokens = player[0] + tip.amount
        new_tips_count = player[1] + 1
        new_largest_tip = max(player[2], tip.amount)
        level_info = get_level(new_tokens)
        cur.execute("""
            UPDATE players SET
                tokens = %s,
                level = %s,
                title = %s,
                emoji = %s,
                tips_count = %s,
                largest_tip = %s,
                updated_at = NOW()
            WHERE username = %s
        """, (new_tokens, level_info["level"], level_info["title"], level_info["emoji"],
              new_tips_count, new_largest_tip, tip.username))
    else:
        new_tokens = tip.amount
        new_tips_count = 1
        new_largest_tip = tip.amount
        level_info = get_level(new_tokens)
        cur.execute("""
            INSERT INTO players (username, tokens, level, title, emoji, tips_count, largest_tip)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (tip.username, new_tokens, level_info["level"], level_info["title"],
              level_info["emoji"], new_tips_count, new_largest_tip))

    conn.commit()
    cur.close()
    conn.close()

    new_achievements = check_achievements(tip.username, new_tokens, tip.amount, new_tips_count)

    return {
        "success": True,
        "username": tip.username,
        "tokens": new_tokens,
        "level": level_info["level"],
        "title": level_info["title"],
        "emoji": level_info["emoji"],
        "new_achievements": new_achievements,
        "leveled_up": player and get_level(player[0])["level"] < level_info["level"]
    }


@app.get("/player/{username}")
def get_player(username: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT username, tokens, level, title, emoji, tips_count, largest_tip FROM players WHERE username = %s", (username,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return {"username": username, "tokens": 0, "level": 0, "title": "Newcomer", "emoji": "🧙", "tips_count": 0, "largest_tip": 0}

    return {"username": row[0], "tokens": row[1], "level": row[2], "title": row[3], "emoji": row[4], "tips_count": row[5], "largest_tip": row[6]}


@app.get("/leaderboard")
def get_leaderboard():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT username, tokens, title, emoji FROM players ORDER BY tokens DESC LIMIT 10")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{"username": r[0], "tokens": r[1], "title": r[2], "emoji": r[3]} for r in rows]


@app.get("/achievements/{username}")
def get_achievements(username: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT achievement, earned_at FROM achievements WHERE username = %s ORDER BY earned_at DESC", (username,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{"achievement": r[0], "earned_at": str(r[1])} for r in rows]


@app.get("/overlay")
def serve_overlay():
    file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rpg_overlay.html")
    return FileResponse(file_path)


@app.get("/events")
def get_events(since: float = 0):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT username, achievement, earned_at
        FROM achievements
        WHERE EXTRACT(EPOCH FROM earned_at) * 1000 > %s
        ORDER BY earned_at DESC
        LIMIT 10
    """, (since,))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    events = []
    for row in rows:
        events.append({
            "type": "achievement",
            "username": row[0],
            "achievement": row[1]
        })
    return events
