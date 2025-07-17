import os
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any

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
FIREBASE_JSON = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY_JSON")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_KEY environment variables")
if not FIREBASE_JSON:
    raise RuntimeError("Missing FIREBASE_SERVICE_ACCOUNT_KEY_JSON environment variable")

# -----------------------
# Logging
# -----------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ondeassistir")

# -----------------------
# Initialize Firebase
# -----------------------
sa_info = json.loads(FIREBASE_JSON)
cred = credentials.Certificate(sa_info)
firebase_admin.initialize_app(cred)
logger.info("Firebase Admin initialized.")

# -----------------------
# Supabase Client
# -----------------------
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# -----------------------
# FastAPI Setup
# -----------------------
app = FastAPI(
    title="OndeAssistir Soccer API",
    version="1.5.0",
    description="Serve upcoming matches, broadcasts, live scores, and notifications"
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

# Static files mount
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")

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
    apiFootballMatchId: Optional[int]
    eventTeamId: Optional[int]
    class Config:
        extra = "allow"

# -----------------------
# Health Check
# -----------------------
@app.get("/health")
async def health():
    return {"status": "ok"}

# -----------------------
# Matches Endpoint
# -----------------------
@app.get("/matches")
async def get_matches() -> List[Dict[str, Any]]:
    rows, count, error = supabase.table("matches") \
        .select(
            "match_id, api_football_id, league_id, home_id, away_id, home_team, away_team, league_week_number, broadcasts, kickoff"
        ) \
        .execute()
    if error:
        raise HTTPException(status_code=500, detail=error.message)
    result = []
    for m in rows:
        result.append({
            "match_id":           m["match_id"],
            "home_team":          m.get("home_team"),
            "away_team":          m.get("away_team"),
            "api_football_id":    m.get("api_football_id"),
            "kickoff":            m.get("kickoff"),
            "league_id":          m.get("league_id"),
            "home_id":            m.get("home_id"),
            "away_id":            m.get("away_id"),
            "league_week_number": m.get("league_week_number"),
            "broadcasts":         m.get("broadcasts") or {}
        })
    return result

# -----------------------
# Single Match Details
# -----------------------
@app.get("/matches/{match_id}")
async def get_match_details(match_id: str) -> Dict[str, Any]:
    rows, count, error = supabase.table("matches") \
        .select("*") \
        .eq("match_id", match_id) \
        .single() \
        .execute()
    if error:
        raise HTTPException(status_code=500, detail=error.message)
    if not rows:
        raise HTTPException(status_code=404, detail="Match not found")
    m = rows
    return {
        "match_id": m["match_id"],
        "home_team": m.get("home_team"),
        "away_team": m.get("away_team"),
        "api_football_id": m.get("api_football_id"),
        "kickoff": m.get("kickoff"),
        "league_id": m.get("league_id"),
        "home_id": m.get("home_id"),
        "away_id": m.get("away_id"),
        "league_week_number": m.get("league_week_number"),
        "broadcasts": m.get("broadcasts") or {},
        "match_status": m.get("match_status"),
        "live_home_score": m.get("live_home_score"),
        "live_away_score": m.get("live_away_score"),
        "live_minutes_elapsed": m.get("live_minutes_elapsed"),
        "live_events": m.get("live_events")
    }

# -----------------------
# Live Score Endpoint
# -----------------------
@app.get("/score/{match_id}")
async def get_live_score(match_id: str) -> Dict[str, Any]:
    rows, count, error = supabase.table("matches") \
        .select("live_home_score, live_away_score, match_status, live_minutes_elapsed") \
        .eq("match_id", match_id) \
        .single() \
        .execute()
    if error:
        raise HTTPException(status_code=500, detail=error.message)
    m = rows
    return {
        "home": m.get("live_home_score", 0),
        "away": m.get("live_away_score", 0),
        "status": m.get("match_status"),
        "minute": m.get("live_minutes_elapsed", 0)
    }

# -----------------------
# Register FCM Token
# -----------------------
@app.post("/register-fcm-token", status_code=201)
async def register_fcm_token(payload: RegisterFCMToken) -> Dict[str, str]:
    data, count, error = supabase.table("user_fcm_tokens").upsert({
        "user_id": payload.user_id,
        "fcm_token": payload.fcm_token,
        "device_type": payload.device_type,
        "created_at": datetime.now(timezone.utc).isoformat()
    }, on_conflict=["fcm_token"]).execute()
    if error:
        raise HTTPException(status_code=500, detail=error.message)
    return {"message": "Token saved"}

# -----------------------
# Trigger Notification
# -----------------------
@app.post("/trigger-notification")
async def trigger_notification(event: NotificationEvent) -> Dict[str, str]:
    ev = event.dict()
    logger.info("Notification event: %s", ev)
    # Implement FCM logic here using firebase_admin.messaging
    return {"message": "Notifications triggered"}
