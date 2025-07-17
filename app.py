import os
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import firebase_admin
from firebase_admin import credentials, messaging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel
from supabase import create_client

# -----------------------
# Environment & Config
# -----------------------
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
DATA_DIR      = os.getenv("DATA_DIR", os.path.join(BASE_DIR, "data"))
LOOKAHEAD_DAYS = int(os.getenv("LOOKAHEAD_DAYS", "5"))
SUPABASE_URL  = os.getenv("SUPABASE_URL")
SUPABASE_KEY  = os.getenv("SUPABASE_KEY")
FIREBASE_CREDENTIALS_JSON = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY_JSON")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_KEY environment variables")
if not FIREBASE_CREDENTIALS_JSON:
    raise RuntimeError("Missing FIREBASE_SERVICE_ACCOUNT_KEY_JSON environment variable")

# -----------------------
# Logging
# -----------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ondeassistir")

# -----------------------
# Initialize Firebase
# -----------------------
sa_info = json.loads(FIREBASE_CREDENTIALS_JSON)
cred = credentials.Certificate(sa_info)
firebase_admin.initialize_app(cred)
logger.info("Firebase Admin initialized successfully.")

# -----------------------
# Supabase Client
# -----------------------
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# -----------------------
# Load Static JSON
# -----------------------
channels_path = os.path.join(DATA_DIR, "channels.json")
if os.path.isfile(channels_path):
    with open(channels_path, encoding="utf-8") as f:
        CHANNELS = json.load(f)
else:
    CHANNELS = {}

leagues_path = os.path.join(DATA_DIR, "leagues.json")
if os.path.isfile(leagues_path):
    with open(leagues_path, encoding="utf-8") as f:
        LEAGUES = json.load(f)
else:
    LEAGUES = {}

# Load per-league match files
ALL_MATCHES = {}
for league_id in (LEAGUES if isinstance(LEAGUES, list) else LEAGUES.keys()):
    league_file = os.path.join(DATA_DIR, f"{league_id}.json")
    if os.path.isfile(league_file):
        with open(league_file, encoding="utf-8") as f:
            ALL_MATCHES[league_id] = json.load(f)

# -----------------------
# FastAPI Initialization
# -----------------------
app = FastAPI(
    title="OndeAssistir Soccer API",
    version="1.5.0",
    description="Serve upcoming matches, broadcasts, live scores, and league calendars"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

# Serve static JSON files under /data
app.mount(
    "/data",
    StaticFiles(directory=DATA_DIR),
    name="data"
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
# Helper Functions
# -----------------------
def parse_datetime(dt_str: str) -> datetime:
    # Handles ISO strings with Z
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))

# -----------------------
# API Endpoints
# -----------------------
@app.get("/health")
def health_check():
    return {"status": "ok", "version": app.version}

@app.get("/matches")
def get_upcoming_matches():
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=1)
    end = now + timedelta(days=LOOKAHEAD_DAYS)

    resp = supabase.table("matches").select("*").execute()
    if resp.error:
        raise HTTPException(status_code=500, detail=resp.error.message)

    out = []
    for m in resp.data:
        kof = m.get("kickoff")
        if not kof:
            continue
        try:
            dt = parse_datetime(kof)
        except ValueError:
            continue
        if dt < start or dt > end:
            continue

        enriched = {}
        for country, ids in (m.get("broadcasts") or {}).items():
            enriched[country] = [CHANNELS.get(i) for i in ids]

        out.append({
            "match_id": m.get("match_id"),
            "home_team": m.get("home_team"),
            "away_team": m.get("away_team"),
            "league": m.get("league"),
            "api_football_id": m.get("api_football_id"),
            "kickoff": kof,
            "broadcasts": enriched
        })
    return out

@app.get("/matches/{identifier}")
def get_match(identifier: str):
    matches = get_upcoming_matches()
    for m in matches:
        if m["match_id"].lower() == identifier.lower():
            score = get_live_score(m["match_id"])
            m.update(score)
            return m
    raise HTTPException(status_code=404, detail="Match not found")

@app.get("/score/{identifier}")
def get_live_score(identifier: str):
    now = datetime.now(timezone.utc)
    resp = supabase.table("matches").select(
        "live_home_score,live_away_score,match_status,live_minutes_elapsed"
    ).eq("match_id", identifier).execute()
    if resp.error:
        raise HTTPException(status_code=500, detail=resp.error.message)

    if resp.data:
        rec = resp.data[0]
        return {
            "home": rec.get("live_home_score", 0),
            "away": rec.get("live_away_score", 0),
            "status": rec.get("match_status"),
            "minute": rec.get("live_minutes_elapsed", 0)
        }
    # If no record, return defaults
    return {"home": 0, "away": 0, "status": "scheduled", "minute": 0}

@app.post("/register-fcm-token", status_code=201)
async def register_fcm_token(payload: RegisterFCMToken):
    data, count, error = supabase.table("user_fcm_tokens").upsert({
        "user_id": payload.user_id,
        "fcm_token": payload.fcm_token,
        "device_type": payload.device_type,
        "created_at": datetime.now(timezone.utc).isoformat()
    }, on_conflict=["fcm_token"]).execute()
    if error:
        raise HTTPException(status_code=500, detail=error.message)
    return {"message": "Token saved"}

@app.post("/trigger-notification")
async def trigger_notification(event: NotificationEvent):
    ev = event.dict()
    logger.info("Notification event: %s", ev)
    # existing logic to send FCM messages...
    return {"message": "Notifications triggered"}
