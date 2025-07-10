import os
import json
import re
import requests
import traceback
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
import logging

# — CONFIGURATION & INITIALIZATION —
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.getenv("DATA_DIR", os.path.join(BASE_DIR, "data"))
LOOKAHEAD_DAYS = int(os.getenv("LOOKAHEAD_DAYS", "5"))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_KEY environment variables")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ondeassistir")

# Supabase client factory
def get_supabase_client():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

# Load broadcast channel definitions
channels_path = os.path.join(DATA_DIR, "channels.json")
if os.path.isfile(channels_path):
    with open(channels_path, encoding="utf-8") as f:
        CHANNELS = json.load(f)
else:
    CHANNELS = {}

# Initialize FastAPI
title = "OndeAssistir Soccer API"
app = FastAPI(
    title=title,
    version="1.5.0",
    description="Serve upcoming matches, broadcasts, live scores, and league calendars"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ======================== CORE FUNCTIONALITY ======================== #

# Load leagues
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

# ======================== CALENDAR UPDATE LOGIC ======================== #

def get_season_from_date(dt: datetime) -> str:
    """Determine season format (YYYY/YYYY) from a date"""
    year = dt.year
    return f"{year-1}/{year}" if dt.month < 7 else f"{year}/{year+1}"

def update_league_calendar_from_json():
    """Update league_calendar table from JSON files"""
    try:
        supabase = get_supabase_client()
        logger.info("⏳ Starting league calendar update from JSON files")
        
        for league_id in LEAGUE_IDS:
            file_path = os.path.join(DATA_DIR, f"{league_id}.json")
            if not os.path.exists(file_path):
                logger.info(f"ℹ️ Skipping {league_id} - file not found")
                continue
                
            try:
                with open(file_path, encoding="utf-8") as f:
                    matches = json.load(f)
                
                logger.info(f"🔁 Processing {league_id} with {len(matches)} matches")
                update_count = 0
                error_count = 0
                
                for idx, match in enumerate(matches, 1):
                    try:
                        # Extract API football ID
                        api_id = match.get("id") or match.get("match_id") or match.get("api_football_id")
                        if not api_id:
                            logger.warning(f"⚠️ Match {idx} missing ID: {match.get('home_team')} vs {match.get('away_team')}")
                            error_count += 1
                            continue
                        
                        # Convert to integer if possible
                        try:
                            api_id = int(api_id)
                        except (TypeError, ValueError):
                            pass
                        
                        # Get kickoff time
                        kickoff_str = match.get("utcDate") or match.get("kickoff")
                        if not kickoff_str:
                            logger.warning(f"⚠️ Match {api_id} missing kickoff time")
                            error_count += 1
                            continue
                        
                        try:
                            kickoff_dt = parse_datetime(kickoff_str)
                            season = get_season_from_date(kickoff_dt)
                        except Exception as e:
                            logger.warning(f"⚠️ Error parsing date for match {api_id}: {str(e)}")
                            error_count += 1
                            continue
                        
                        # Prepare data
                        match_data = {
                            "api_football_id": api_id,
                            "match_id": match.get("match_id") or f"{league_id}_{kickoff_str}_{match.get('home_team')}_x_{match.get('away_team')}",
                            "league": league_id,
                            "home": match.get("home_team") or match.get("home"),
                            "away": match.get("away_team") or match.get("away"),
                            "kickoff": kickoff_str,
                            "round": match.get("matchday") or match.get("round"),
                            "season": season,
                            "status": "scheduled"
                        }
                        
                        # Debug log first 5 matches
                        if idx <= 5:
                            logger.info(f"📝 Sample match data ({idx}): {json.dumps(match_data, indent=2)}")
                        
                        # Upsert without overwriting live data
                        response = supabase.table("league_calendar").upsert(
                            match_data,
                            on_conflict="api_football_id"
                        ).execute()
                        
                        if getattr(response, "error", None):
                            logger.error(f"❌ Upsert error for match {api_id}: {response.error}")
                            error_count += 1
                        else:
                            update_count += 1
                            # Log progress every 50 matches
                            if update_count % 50 == 0:
                                logger.info(f"✳️ Updated {update_count} matches so far...")
                    
                    except Exception as e:
                        logger.error(f"❌ Error processing match: {str(e)}")
                        error_count += 1
                        continue
                
                logger.info(f"✅ Updated {update_count}/{len(matches)} matches for {league_id}")
                if error_count:
                    logger.warning(f"⚠️ Failed to update {error_count} matches")
            
            except Exception as e:
                logger.error(f"❌ Error updating {league_id}: {str(e)}")
                logger.error(traceback.format_exc())
        
        logger.info("🎉 League calendar update completed")
    
    except Exception as e:
        logger.error(f"🔥 Critical error in calendar update: {str(e)}")
        logger.error(traceback.format_exc())

# Initialize scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(
    update_league_calendar_from_json,
    'interval',
    hours=6,
    next_run_time=datetime.now()
)
scheduler.start()

# Shut down the scheduler when exiting the app
atexit.register(lambda: scheduler.shutdown())

# ======================== API ENDPOINTS ======================== #

# Health check
@app.get("/health")
def health_check():
    return {"status": "ok", "version": app.version}

# Broadcast enrichment helper
def enrich_broadcasts(raw: dict) -> dict:
    enriched = {}
    for country, ch_ids in (raw or {}).items():
        enriched[country] = [CHANNELS.get(ch) for ch in ch_ids if ch in CHANNELS]
    return enriched

# Upcoming matches
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

# Single match details
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

# Live score endpoint
@app.get("/score/{identifier}")
def get_live_score(identifier: str):
    slug = KEY_TO_SLUG.get(identifier.lower())
    if not slug:
        raise HTTPException(status_code=404, detail=f"Unknown match '{identifier}'")
    now = datetime.now(timezone.utc)
    kickoff_dt = None
    for matches in ALL_MATCHES.values():
        for m in matches:
            mid = m.get("id") or m.get("match_id") or m.get("matchId")
            comp = f"{m['league'].lower()}_{(m.get('kickoff') or m.get('utcDate')).lower()}_{m['home_team'].lower()}_x_{m['away_team'].lower()}"
            if (mid is not None and str(mid).lower() == identifier.lower()) or comp == identifier.lower():
                kt = m.get("kickoff") or m.get("utcDate")
                kickoff_dt = parse_datetime(kt) if kt else None
                break
        if kickoff_dt:
            break

    supabase = get_supabase_client()
    resp = supabase.table("live_scores").select("match_id,status,minute,score,updated_at").eq("match_id", identifier).execute()
    if getattr(resp, "error", None):
        raise HTTPException(status_code=500, detail=resp.error.message)
    if resp.data:
        rec = resp.data[0]
        score = json.loads(rec.get("score", "{}"))
        status = rec.get("status") or "unknown"
        minute = rec.get("minute") or ""
        updated_at = rec.get("updated_at")
    else:
        # Simplified fallback logic
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

# FCM Token Registration
class RegisterFCMToken(BaseModel):
    user_id: str
    fcm_token: str
    device_type: str

@app.post("/register-fcm-token", status_code=201)
async def register_fcm_token(request: Request):
    try:
        payload = await request.json()
        user_id = payload.get("user_id")
        fcm_token = payload.get("fcm_token")
        device_type = payload.get("device_type")
        
        if not all([user_id, fcm_token, device_type]):
            raise HTTPException(status_code=422, detail="Missing required fields")
        
        supabase = get_supabase_client()
        result = supabase.table("user_fcm_tokens").upsert({
            "user_id": user_id,
            "fcm_token": fcm_token,
            "device_type": device_type,
            "created_at": datetime.utcnow().isoformat()
        }, on_conflict="fcm_token").execute()
        
        if getattr(result, "error", None):
            raise HTTPException(status_code=500, detail=result.error.message)
            
        return {"message": "Token saved"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ======================== CALENDAR ENDPOINTS ======================== #

@app.get("/team-calendar/{team_name}")
def get_team_calendar(team_name: str, limit: int = 100):
    supabase = get_supabase_client()
    try:
        response = supabase.table("league_calendar") \
            .select("*") \
            .or_(f"home.ilike.%{team_name}%", f"away.ilike.%{team_name}%") \
            .order("kickoff") \
            .limit(limit) \
            .execute()
        
        if not response.data:
            raise HTTPException(status_code=404, detail="No matches found")
        return response.data
    except Exception as e:
        logger.error(f"Team calendar error: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/league-calendar/{league_id}")
def get_league_calendar(
    league_id: str,
    season: str = Query(default="2023/2024"),
    include_finished: bool = True
):
    supabase = get_supabase_client()
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
        logger.error(f"League calendar error: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

# ======================== ADMIN ENDPOINTS ======================== #

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
    
    # Trigger immediate calendar update when league files change
    if filename.endswith(".json") and filename != "channels.json":
        update_league_calendar_from_json()
    
    return {"status": "ok"}

# ======================== STATIC FILES ======================== #
# Mount static files LAST to avoid route conflicts
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")
app.mount("/admin", StaticFiles(directory=os.path.join(BASE_DIR, "admin")), name="admin")

# Initial calendar update on startup
@app.on_event("startup")
def startup_event():
    logger.info("🚀 Starting application...")
    update_league_calendar_from_json()