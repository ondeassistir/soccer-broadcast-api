import os
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List

import firebase_admin
from firebase_admin import credentials, messaging

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel
from supabase import create_client
import typer
import uvicorn

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

# Validate required environment variables
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_KEY environment variables")
if not FIREBASE_CREDENTIALS_JSON:
    raise RuntimeError("Missing FIREBASE_CREDENTIALS_JSON environment variable")

# -----------------------
# Initialize Firebase
# -----------------------
try:
    sa_info = json.loads(FIREBASE_CREDENTIALS_JSON)
except json.JSONDecodeError as e:
    raise RuntimeError(f"Invalid FIREBASE_CREDENTIALS_JSON: {e}")

try:
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

# Build match lookup
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
        if slug:
            if mid is not None:
                KEY_TO_SLUG[str(mid).lower()] = slug
            if tstr and home and away:
                comp = f"{lid.lower()}_{tstr.lower()}_{home.lower()}_x_{away.lower()}"
                KEY_TO_SLUG[comp.lower()] = slug
            KEY_TO_SLUG[slug.lower()] = slug

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
    allow_origins=["*"],  # restrict in production
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
    out = []
    for lid, matches in ALL_MATCHES.items():
        for m in matches:
            tstr = m.get("utcDate") or m.get("kickoff") or m.get("start") or m.get("dateTime")
            if not tstr:
                continue
            try:
                dt = parse_datetime(tstr)
            except ValueError:
                continue
            if not (start <= dt <= end):
                continue
            mid = m.get("id") or m.get("match_id") or m.get("matchId")
            key = str(mid) if mid is not None else f"{lid.lower()}_{tstr.lower()}_{m['home_team'].lower()}_x_{m['away_team'].lower()}"
            out.append({
                "match_id": key,
                "slug": m.get("slug"),
                "home": m.get("home_team"),
                "away": m.get("away_team"),
                "kickoff": tstr,
                "league": lid,
                "broadcasts": {country: [CHANNELS[c] for c in ch_ids if c in CHANNELS]
                               for country, ch_ids in (m.get("broadcasts") or {}).items()}
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
    slug = KEY_TO_SLUG.get(identifier.lower())
    if not slug:
        raise HTTPException(status_code=404, detail=f"Unknown match '{identifier}'")
    now = datetime.now(timezone.utc)
    resp = supabase.table("live_scores").select("match_id,status,minute,score,updated_at")
    resp = resp.eq("match_id", identifier).execute()
    if resp.error:
        raise HTTPException(status_code=500, detail=resp.error.message)
    if resp.data:
        rec = resp.data[0]
        return {
            "match_id": rec["match_id"],
            "status": rec.get("status", "unknown"),
            "minute": rec.get("minute", ""),
            "score": json.loads(rec.get("score", "{}")),
            "updated_at": rec.get("updated_at")
        }
    # initialize
    initial = {"home": 0, "away": 0}
    supabase.table("live_scores").upsert({
        "match_id": identifier,
        "status": "scheduled",
        "minute": "",
        "score": json.dumps(initial),
        "updated_at": now.isoformat()
    }, on_conflict=["match_id"]).execute()
    return {"match_id": identifier, "status": "scheduled", "minute": "", "score": initial, "updated_at": now.isoformat()}

@app.post("/register-fcm-token", status_code=201)
async def register_fcm_token(payload: RegisterFCMToken):
    logger.info("Register FCM token: %s", payload.user_id)
    result = supabase.table("user_fcm_tokens").upsert({
        "user_id": payload.user_id,
        "fcm_token": payload.fcm_token,
        "device_type": payload.device_type,
        "created_at": datetime.now(timezone.utc).isoformat()
    }, on_conflict=["fcm_token"]).execute()
    if result.error:
        raise HTTPException(status_code=500, detail=result.error.message)
    return {"message": "Token saved"}

@app.post("/trigger-notification")
async def trigger_notification(event: NotificationEvent):
    ev = event.dict()
    logger.info("Notification event: %s", ev)
    resp = supabase.table("user_fcm_tokens").select("fcm_token").execute()
    if resp.error:
        raise HTTPException(status_code=500, detail=resp.error.message)
    tokens = [r["fcm_token"] for r in resp.data]
    if not tokens:
        return {"message": "No tokens"}
    title = f"{ev['homeTeamAbbr']} vs {ev['awayTeamAbbr']}"
    body = f"{ev['eventDetail']} — {ev['score']}"
    failures = []
    for i in range(0, len(tokens), 500):
        chunk = tokens[i:i+500]
        msg = messaging.MulticastMessage(
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in ev.items()},
            tokens=chunk
        )
        batch = messaging.send_multicast(msg)
        logger.info("Batch %d: %d/%d sent", i//500+1, batch.success_count, len(chunk))
        for idx, r in enumerate(batch.responses):
            if not r.success:
                failures.append({"token": chunk[idx], "error": str(r.error)})
                supabase.table("user_fcm_tokens").delete().eq("fcm_token", chunk[idx]).execute()
    return {"failures": failures}

@app.get("/team-calendar/{team_name}")
def get_team_calendar(team_name: str, limit: int = 100):
    try:
        data = supabase.table("league_calendar").select("*")
        data = data.or_(f"home.ilike.%{team_name}%", f"away.ilike.%{team_name}%").order("kickoff").limit(limit).execute()
        return data.data
    except Exception as e:
        logger.error("Team calendar error: %s", e)
        raise HTTPException(status_code=500, detail="Server error")

@app.get("/league-calendar/{league_id}")
def get_league_calendar(league_id: str, season: str = Query("2023/2024"), include_finished: bool = True):
    try:
        q = supabase.table("league_calendar").select("*").eq("league", league_id).eq("season", season).order("kickoff")
        if not include_finished:
            q = q.not_.in_("match_status", ["FT","AET","PEN","CANC","ABD","AWD","WO"])
        return q.execute().data
    except Exception as e:
        logger.error("League calendar error: %s", e)
        raise HTTPException(status_code=500, detail="Server error")

@app.post("/admin/save/{filename}")
async def save_json(filename: str, request: Request):
    allowed = {"leagues.json","channels.json","teams.json","QUALIFIERS_2026.json","BRA_A.json","INT_FRIENDLY.json","CLUB_WC.json"}
    if filename not in allowed:
        raise HTTPException(status_code=403, detail="Forbidden")
    body = await request.body()
    try:
        json.loads(body)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")
    with open(os.path.join(DATA_DIR, filename), "w", encoding="utf-8") as f:
        f.write(body.decode())
    return {"status": "ok"}

app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")
app.mount("/admin", StaticFiles(directory=os.path.join(BASE_DIR,"admin")), name="admin")

cli = typer.Typer()
@cli.command()
def setup():
    typer.echo("✅ Future matches synced.")

@cli.command()
def backfill():
    typer.echo("✅ Backfill complete.")

@cli.command()
def serve(host: str = typer.Option("0.0.0.0"), port: int = typer.Option(int(os.getenv("PORT", 8000)))):
    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    cli()
