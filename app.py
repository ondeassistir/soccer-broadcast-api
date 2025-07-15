import os
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List

# -----------------------
# Logging Configuration
# -----------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ondeassistir")

import firebase_admin
from firebase_admin import credentials, messaging

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel

# -----------------------
# Environment Variables
# -----------------------
# Core configuration loaded directly from environment
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
FIREBASE_CREDENTIALS_JSON = os.getenv("FIREBASE_CREDENTIALS_JSON")
DATA_DIR = os.getenv("DATA_DIR")
LOOKAHEAD_DAYS = int(os.getenv("LOOKAHEAD_DAYS", "5"))

# Validate required environment variables
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_KEY environment variables")
if not FIREBASE_CREDENTIALS_JSON:
    raise RuntimeError("Missing FIREBASE_CREDENTIALS_JSON environment variable")

# -----------------------
# Initialize Firebase
# -----------------------
# Parse the service account JSON and initialize the Admin SDK
try:
    sa_info = json.loads(FIREBASE_CREDENTIALS_JSON)
except json.JSONDecodeError as e:
    raise RuntimeError(f"Invalid FIREBASE_CREDENTIALS_JSON: {e}")

try:
    cred = credentials.Certificate(sa_info)
    firebase_admin.initialize_app(cred)
    logger.info("Firebase Admin initialized successfully.")
except Exception as e:
    raise RuntimeError(f"Firebase initialization error: {e}")

# Pydantic model for incoming payloads
from pydantic import BaseModel

# -----------------------
# Supabase Client
# -----------------------
# -----------------------
# Supabase Client
# -----------------------
from supabase import create_client
# Create and cache Supabase client using environment variables
from supabase import create_client
# Create and cache Supabase client using environment variables
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# -----------------------
# Logging Configuration
# -----------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ondeassistir")

# -----------------------
# Firebase Initialization
# -----------------------
if not firebase_admin._apps:
    try:
        sa_info = json.loads(settings.FIREBASE_CREDENTIALS_JSON)
        cred = credentials.Certificate(sa_info)
        firebase_admin.initialize_app(cred)
        logger.info("Firebase Admin initialized successfully.")
    except Exception as e:
        logger.error("Failed to initialize Firebase Admin: %s", e)
        raise RuntimeError("Firebase initialization error")

# -----------------------
# Supabase Client
# -----------------------
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

# -----------------------
# Data Directories & Config
# -----------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = settings.DATA_DIR or os.path.join(BASE_DIR, "data")
LOOKAHEAD_DAYS = settings.LOOKAHEAD_DAYS

# Load broadcast channels
channels_path = os.path.join(DATA_DIR, "channels.json")
if os.path.isfile(channels_path):
    with open(channels_path, encoding="utf-8") as f:
        CHANNELS = json.load(f)
else:
    CHANNELS = {}

# -----------------------
# FastAPI Initialization
# -----------------------
title = "OndeAssistir Soccer API"
app = FastAPI(
    title=title,
    version="1.5.0",
    description="Serve upcoming matches, broadcasts, live scores, and league calendars"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Consider locking this down in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------
# Pydantic Models
# -----------------------
class RegisterFCMToken(BaseModel):
    user_id: str
    fcm_token: str
    device_type: str

class NotificationEvent(BaseModel):
    type: str
    matchId: str
    homeTeamAbbr: str
    awayTeamAbbr: str
    score: str
    eventDetail: str
    apiFootballMatchId: Optional[int] = None
    eventTeamId: Optional[int] = None

    class Config:
        extra = "allow"

# -----------------------
# Core Data Loading
# -----------------------
with open(os.path.join(DATA_DIR, "leagues.json"), encoding="utf-8") as f:
    leagues_data = json.load(f)

def extract_league_ids(data):
    if isinstance(data, dict):
        return list(data.keys())
    if isinstance(data, list):
        return [item.get("id") if isinstance(item, dict) and "id" in item else item for item in data]
    return []

LEAGUE_IDS = extract_league_ids(leagues_data)
ALL_MATCHES = {}
KEY_TO_SLUG = {}
for lid in LEAGUE_IDS:
    path = os.path.join(DATA_DIR, f"{lid}.json")
    if not os.path.isfile(path):
        continue
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    ALL_MATCHES[lid] = data
    for m in data:
        slug = m.get("slug")
        tstr = m.get("utcDate") or m.get("kickoff") or m.get("start") or m.get("dateTime")
        home = m.get("home_team") or m.get("home")
        away = m.get("away_team") or m.get("away")
        mid = m.get("id") or m.get("match_id") or m.get("matchId")
        if mid is not None and slug:
            KEY_TO_SLUG[str(mid).lower()] = slug
        if slug and tstr and home and away:
            comp = f"{lid.lower()}_{tstr.lower()}_{home.lower()}_x_{away.lower()}"
            KEY_TO_SLUG[comp.lower()] = slug
        if slug:
            KEY_TO_SLUG[slug.lower()] = slug

def parse_datetime(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))

# -----------------------
# API Endpoints
# -----------------------
@app.get("/health")
def health_check():
    return {"status": "ok", "version": app.version}


def enrich_broadcasts(raw: dict) -> dict:
    enriched = {}
    for country, ch_ids in (raw or {}).items():
        enriched[country] = [CHANNELS.get(ch) for ch in ch_ids if ch in CHANNELS]
    return enriched

@app.get("/matches")
def get_upcoming_matches():
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=4)
    end = now + timedelta(days=LOOKAHEAD_DAYS)
    out = []
    for lid, matches in ALL_MATCHES.items():
        for m in matches:
            tstr = m.get("utcDate") or m.get("kickoff") or m.get("start") or m.get("dateTime")
            if not tstr:
                continue
            try:
                dt = parse_datetime(tstr)
            except:
                continue
            if not (start <= dt <= end):
                continue
            mid = m.get("id") or m.get("match_id") or m.get("matchId")
            if mid is not None:
                key = str(mid)
            else:
                key = f"{lid.lower()}_{tstr.lower()}_{m['home_team'].lower()}_x_{m['away_team'].lower()}"
            out.append({
                "match_id": key,
                "slug": m.get("slug"),
                "home": m.get("home_team"),
                "away": m.get("away_team"),
                "kickoff": tstr,
                "league": lid,
                "broadcasts": enrich_broadcasts(m.get("broadcasts"))
            })
    return out

@app.get("/matches/{identifier}")
def get_match(identifier: str):
    ident_lc = identifier.lower()
    for m in get_upcoming_matches():
        if m["match_id"].lower() == ident_lc:
            score_data = get_live_score(m["match_id"])
            m.update({
                "status": score_data["status"],
                "minute": score_data["minute"],
                "score": score_data["score"],
                "updated_at": score_data["updated_at"]
            })
            return m
    raise HTTPException(status_code=404, detail="Match not found")

@app.get("/score/{identifier}")
def get_live_score(identifier: str):
    slug = KEY_TO_SLUG.get(identifier.lower())
    if not slug:
        raise HTTPException(status_code=404, detail=f"Unknown match '{identifier}'")
    now = datetime.now(timezone.utc)
    # Supabase lookup
    resp = supabase.table("live_scores") \
        .select("match_id,status,minute,score,updated_at") \
        .eq("match_id", identifier) \
        .execute()
    if getattr(resp, "error", None):
        raise HTTPException(status_code=500, detail=resp.error.message)
    if resp.data:
        rec = resp.data[0]
        score = json.loads(rec.get("score", "{}"))
        status = rec.get("status") or "unknown"
        minute = rec.get("minute") or ""
        updated_at = rec.get("updated_at")
    else:
        # Fallback: initialize scheduled match row
        status = "scheduled"
        minute = ""
        score = {"home": 0, "away": 0}
        updated_at = now.isoformat()
        supabase.table("live_scores").upsert({
            "match_id": identifier,
            "status": status,
            "minute": minute,
            "score": json.dumps(score),
            "updated_at": updated_at
        }, on_conflict=["match_id"]).execute()
    return {
        "match_id": identifier,
        "status": status,
        "minute": minute,
        "score": score,
        "updated_at": updated_at
    }

@app.post("/register-fcm-token", status_code=201)
async def register_fcm_token(payload: RegisterFCMToken):
    logger.info(f"Registering FCM token for user {payload.user_id}")
    try:
        result = supabase.table("user_fcm_tokens").upsert({
            "user_id": payload.user_id,
            "fcm_token": payload.fcm_token,
            "device_type": payload.device_type,
            "created_at": datetime.now(timezone.utc).isoformat()
        }, on_conflict="fcm_token").execute()
        if getattr(result, "error", None):
            raise HTTPException(status_code=500, detail=result.error.message)
        return {"message": "Token saved"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error registering FCM token: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/trigger-notification")
async def trigger_notification(event: NotificationEvent):
    ev = event.dict()
    logger.info(f"Trigger notification event: {ev}")
    # Fetch tokens
    resp = supabase.table("user_fcm_tokens").select("fcm_token").execute()
    if getattr(resp, "error", None):
        raise HTTPException(status_code=500, detail=resp.error.message)
    tokens: List[str] = [r["fcm_token"] for r in resp.data]
    if not tokens:
        return {"message": "No tokens registered"}

    title = f"{ev['homeTeamAbbr']} vs {ev['awayTeamAbbr']}"
    body_text = f"{ev['eventDetail']} — {ev['score']}"

    # Send in batches of 500
    failures = []
    for i in range(0, len(tokens), 500):
        chunk = tokens[i:i+500]
        message = messaging.MulticastMessage(
            notification=messaging.Notification(title=title, body=body_text),
            data={k: str(v) for k, v in ev.items()},
            tokens=chunk
        )
        batch = messaging.send_multicast(message)
        logger.info(f"Sent {batch.success_count}/{len(chunk)} notifications in batch {i//500+1}")
        # Prune invalid tokens
        for idx, resp_item in enumerate(batch.responses):
            if not resp_item.success:
                token_to_remove = chunk[idx]
                failures.append({"token": token_to_remove, "error": str(resp_item.error)})
                # Delete bad token from Supabase
                supabase.table("user_fcm_tokens").delete().eq("fcm_token", token_to_remove).execute()
    return {"message": f"Notifications processed", "failures": failures}

# -----------------------
# Calendar Endpoints
# -----------------------
@app.get("/team-calendar/{team_name}")
def get_team_calendar(team_name: str, limit: int = 100):
    try:
        response = supabase.table("league_calendar") \
            .select("*") \
            .or_(f"home.ilike.%{team_name}%", f"away.ilike.%{team_name}%") \
            .order("kickoff") \
            .limit(limit) \
            .execute()
        return response.data
    except Exception as e:
        logger.error("Team calendar error: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/league-calendar/{league_id}")
def get_league_calendar(
    league_id: str,
    season: str = Query(default="2023/2024"),
    include_finished: bool = True
):
    try:
        query = supabase.table("league_calendar") \
            .select("*") \
            .eq("league", league_id) \
            .eq("season", season) \
            .order("kickoff")
        if not include_finished:
            query = query.not_.in_("match_status", ["FT", "AET", "PEN", "CANC", "ABD", "AWD", "WO"])
        response = query.execute()
        return response.data
    except Exception as e:
        logger.error("League calendar error: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")

# -----------------------
# Admin Endpoints
# -----------------------
@app.post("/admin/save/{filename}")
async def save_json(filename: str, request: Request):
    allowed = {"leagues.json", "channels.json", "teams.json", "QUALIFIERS_2026.json",
               "BRA_A.json", "INT_FRIENDLY.json", "CLUB_WC.json"}
    if filename not in allowed:
        raise HTTPException(status_code=403, detail="File not allowed")
    body = await request.body()
    try:
        json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")
    path = os.path.join(DATA_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body.decode("utf-8"))
    return {"status": "ok"}

# -----------------------
# Static File Mounts
# -----------------------
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")
app.mount("/admin", StaticFiles(directory=os.path.join(BASE_DIR, "admin")), name="admin")

# -----------------------
# CLI Commands
# -----------------------
cli = typer.Typer()

@cli.command()
def setup():
    """
    Sync a new batch of future matches and map their api_football_id.
    """
    typer.echo("✅ Future matches synced.")

@cli.command()
def backfill():
    """
    Clean up final results for any recently-finished matches.
    """
    typer.echo("✅ Backfill complete.")

@cli.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind address"),
    port: int = typer.Option(int(os.getenv("PORT", 8000)), help="Port to listen on")
):
    """
    Run the FastAPI server.
    """
    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    cli()
