import os
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import firebase_admin
from firebase_admin import credentials, messaging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel
from supabase import create_client

# -----------------------
# Logging Configuration
# -----------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ondeassistir")

# -----------------------
# Environment Variables
# -----------------------
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
FIREBASE_CREDENTIALS_JSON = os.getenv("FIREBASE_CREDENTIALS_JSON")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.getenv("DATA_DIR", os.path.join(BASE_DIR, "data"))
LOOKAHEAD_DAYS = int(os.getenv("LOOKAHEAD_DAYS", "5"))

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_KEY environment variables")
if not FIREBASE_CREDENTIALS_JSON:
    raise RuntimeError("Missing FIREBASE_CREDENTIALS_JSON environment variable")

# -----------------------
# Initialize Firebase
# -----------------------
try:
    sa_info = json.loads(FIREBASE_CREDENTIALS_JSON)
    cred = credentials.Certificate(sa_info)
    firebase_admin.initialize_app(cred)
    logger.info("Firebase Admin initialized successfully.")
except Exception as e:
    logger.error("Firebase initialization error: %s", e)
    raise RuntimeError(f"Firebase initialization error: {e}")

# -----------------------
# Supabase Client
# -----------------------
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# -----------------------
# Load Static Data
# -----------------------
channels_path = os.path.join(DATA_DIR, "channels.json")
if os.path.isfile(channels_path):
    with open(channels_path, encoding="utf-8") as f:
        CHANNELS = json.load(f)
else:
    CHANNELS = {}

with open(os.path.join(DATA_DIR, "leagues.json"), encoding="utf-8") as f:
    leagues_data = json.load(f)

# -----------------------
# Helper Functions
# -----------------------
def extract_league_ids(data):
    if isinstance(data, dict):
        return list(data.keys())
    if isinstance(data, list):
        return [item.get("id") if isinstance(item, dict) and "id" in item else item for item in data]
    return []

def parse_datetime(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))

# -----------------------
# Load per‑league match files
# -----------------------
LEAGUE_IDS = extract_league_ids(leagues_data)
ALL_MATCHES = {}
for lid in LEAGUE_IDS:
    path = os.path.join(DATA_DIR, f"{lid}.json")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            ALL_MATCHES[lid] = json.load(f)

# -----------------------
# FastAPI Application
# -----------------------
app = FastAPI(
    title="OndeAssistir Soccer API",
    version="1.5.0",
    description="Serve upcoming matches, broadcasts, live scores, and league calendars"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
# API Endpoints
# -----------------------
@app.get("/health")
def health_check():
    return {"status": "ok", "version": app.version}

@app.get("/matches")
def get_upcoming_matches():
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=4)
    end = now + timedelta(days=LOOKAHEAD_DAYS)

    resp = supabase.table("matches").select("*").execute()
    if resp.error:
        raise HTTPException(status_code=500, detail=resp.error.message)

    out = []
    for m in resp.data:
        kickoff_str = m.get("kickoff")
        if not kickoff_str:
            continue
        try:
            dt = parse_datetime(kickoff_str)
        except ValueError:
            continue
        if not (start <= dt <= end):
            continue

        match_id = m.get("match_id") or (
            f"{m['league'].lower()}_{kickoff_str.lower()}_"
            f"{m['home_team'].lower()}_x_{m['away_team'].lower()}"
        )

        enriched_broadcasts = {}
        for country, ch_ids in (m.get("broadcasts") or {}).items():
            enriched_broadcasts[country] = [CHANNELS[c] for c in ch_ids if c in CHANNELS]

        out.append({
            "match_id":            match_id,
            "home_team":           m.get("home_team"),
            "home_id":             m.get("home_id"),
            "away_team":           m.get("away_team"),
            "away_id":             m.get("away_id"),
            "league":              m.get("league"),
            "league_id":           m.get("league_id"),
            "league_week_number":  m.get("league_week_number"),
            "api_football_id":     m.get("api_football_id"),
            "kickoff":             kickoff_str,
            "broadcasts":          enriched_broadcasts
        })
    return out

@app.get("/matches/{identifier}")
def get_match(identifier: str):
    for m in get_upcoming_matches():
        if m["match_id"].lower() == identifier.lower():
            score = get_live_score(m["match_id"])
            m.update(score)
            return m
    raise HTTPException(status_code=404, detail="Match not found")

@app.get("/score/{identifier}")
def get_live_score(identifier: str):
    now = datetime.now(timezone.utc)
    resp = supabase.table("live_scores").select("match_id,status,minute,score,updated_at").eq("match_id", identifier).execute()
    if resp.error:
        raise HTTPException(status_code=500, detail=resp.error.message)
    if resp.data:
        rec = resp.data[0]
        return {
            "match_id":   rec["match_id"],
            "status":     rec.get("status", "unknown"),
            "minute":     rec.get("minute", ""),
            "score":      json.loads(rec.get("score", "{}")),
            "updated_at": rec.get("updated_at")
        }
    initial = {"home": 0, "away": 0}
    supabase.table("live_scores").upsert({
        "match_id":    identifier,
        "status":      "scheduled",
        "minute":      "",
        "score":       json.dumps(initial),
        "updated_at":  now.isoformat()
    }, on_conflict=["match_id"]).execute()
    return {"match_id": identifier, "status": "scheduled", "minute": "", "score": initial, "updated_at": now.isoformat()}

@app.post("/register-fcm-token", status_code=201)
async def register_fcm_token(payload: RegisterFCMToken):
    logger.info("Register FCM token: %s", payload.user_id)
    result = supabase.table("user_fcm_tokens").upsert({
        "user_id":     payload.user_id,
        "fcm_token":   payload.fcm_token,
        "device_type": payload.device_type,
        "created_at":  datetime.now(timezone.utc).isoformat()
    }, on_conflict=["fcm_token"]).execute()
    if result.error:
        raise HTTPException(status_code=500, detail=result.error.message)
    return {"message": "Token saved"}

@app.post("/trigger-notification")
async def	trigger_notification(event: NotificationEvent):
    ev = event.dict()
    logger.info("Notification event: %s", ev)

    team_ids = [ev["eventTeamId"]] if ev.get("eventTeamId") is not None else []

    try:
        fav_query = supabase.table("user_favorite_teams").select("user_id")
        if team_ids:
            fav_query = fav_query.in_("team_id", team_ids)
        fav_resp = fav_query.execute()
    except Exception as e:
        logger.error("Error fetching favorite teams: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch favorite teams")
    if getattr(fav_resp, "error", None):
        raise HTTPException(status_code=500, detail=fav_resp.error.message)

    user_ids = [r["user_id"] for r in fav_resp.data]
    if not user_ids:
        return {"message": "No subscribers for these teams"}

    token_resp = supabase.table("user_fcm_tokens").select("user_id,fcm_token").execute()
    if getattr(token_resp, "error", None):
        raise HTTPException(status_code=500, detail=token_resp.error.message)
    tokens = [r["fcm_token"] for r in token_resp.data if r["user_id"] in user_ids]

    title = f"{ev.get('homeTeamAbbr')} vs {ev.get('awayTeamAbbr')}"
    body  = f"{ev.get('eventDetail')} — {ev.get('score')}"
    failures = []
    for i in range(0, len(tokens), 500):
        chunk = tokens[i:i+500]
        message = messaging.MulticastMessage(
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in ev.items()},
            tokens=chunk,
        )
        batch = messaging.send_multicast(message)
        for idx, resp_item in enumerate(batch.responses):
            if not resp_item.success:
                bad = chunk[idx]
                failures.append({"token": bad, "error": str(resp_item.error)})
