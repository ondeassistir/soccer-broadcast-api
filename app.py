import os
import json
import re
import requests
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from bs4 import BeautifulSoup
from supabase import create_client
from pydantic import BaseModel
from typing import List, Optional

# — CONFIGURATION & INITIALIZATION —
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.getenv("DATA_DIR", os.path.join(BASE_DIR, "data"))
LOOKAHEAD_DAYS = int(os.getenv("LOOKAHEAD_DAYS", "5"))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_KEY environment variables")

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

# Mount static files
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")
app.mount("/admin", StaticFiles(directory=os.path.join(BASE_DIR, "admin")), name="admin")

# OPTIONS for CORS
@app.options("/matches")
def options_matches():
    return {}

@app.options("/matches/{identifier}")
def options_match(identifier: str):
    return {}

@app.options("/score/{identifier}")
def options_score(identifier: str):
    return {}

# Admin save endpoint
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
    if not os.path.isfile(path): continue
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
            if not tstr: continue
            try:
                dt = parse_datetime(tstr)
            except:
                continue
            if not (start <= dt <= end): continue
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
        # Scrape and normalize logic remains the same as previous version
        # ...
        status = status or "unknown"
        minute = minute or ""
        score = {"home": home or 0, "away": away or 0}
        updated_at = now.isoformat()
        get_supabase_client().table("live_scores").upsert({
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

# ─── FCM TOKEN REGISTRATION ────────────────────────────────────────────────
class RegisterFCMToken(BaseModel):
    user_id: str
    fcm_token: str
    device_type: str

@app.post("/register-fcm-token", status_code=201)
async def register_fcm_token(request: Request):
    """
    Save or update the FCM token for this user into user_fcm_tokens.
    Includes debug logging for request body and validation errors.
    """
    raw_body = await request.body()
    print(f"📥 Raw request body: {raw_body}")
    try:
        payload = RegisterFCMToken.parse_raw(raw_body)
    except Exception as e:
        # Return Pydantic validation errors
        error_details = getattr(e, 'errors', lambda: str(e))()
        print(f"❌ Validation error: {error_details}")
        raise HTTPException(status_code=422, detail=error_details)

    supabase = get_supabase_client()
    now_iso = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
    result = (
        supabase
        .table("user_fcm_tokens")
        .upsert({
            "user_id":     payload.user_id,
            "fcm_token":   payload.fcm_token,
            "device_type": payload.device_type,
            "created_at":  now_iso
        }, on_conflict="fcm_token")
        .execute()
    )
    print(f"🗄️ Supabase response: {result}")
    if getattr(result, "error", None):
        print(f"❌ Supabase error: {result.error}")
        raise HTTPException(status_code=500, detail=result.error.message)
    return {"message": "Token saved"}

# ========== NEW LEAGUE CALENDAR ENDPOINTS ========== #

# Get all matches for a specific team
@app.get("/team-calendar/{team_name}")
def get_team_calendar(team_name: str, limit: int = 100):
    supabase = get_supabase_client()
    try:
        # Query using case-insensitive matching
        response = supabase.table("league_calendar") \
            .select("*") \
            .or_(f"home.ilike.{team_name}", f"away.ilike.{team_name}") \
            .order("kickoff", desc=False) \
            .limit(limit) \
            .execute()
        
        if response.data:
            return response.data
        else:
            raise HTTPException(status_code=404, detail="No matches found for this team")
            
    except Exception as e:
        print(f"Error fetching team calendar: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

# Get full calendar for a specific league
@app.get("/league-calendar/{league_id}")
def get_league_calendar(
    league_id: str,
    season: str = Query(default="2023/2024", description="Season in YYYY/YYYY format"),
    include_finished: bool = Query(default=True, description="Include finished matches")
):
    supabase = get_supabase_client()
    try:
        query = supabase.table("league_calendar") \
            .select("*") \
            .eq("league", league_id) \
            .eq("season", season) \
            .order("kickoff", desc=False)
        
        if not include_finished:
            query = query.neq("match_status", "finished")
            
        response = query.execute()
        
        if response.data:
            return response.data
        else:
            raise HTTPException(status_code=404, detail="No matches found for this league")
            
    except Exception as e:
        print(f"Error fetching league calendar: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

# Admin endpoint to load league calendar data
@app.post("/admin/load-league-calendar")
async def load_league_calendar(request: Request):
    try:
        data = await request.json()
        league_id = data.get("league_id")
        season = data.get("season")
        matches = data.get("matches")
        
        if not league_id or not season or not matches:
            raise HTTPException(status_code=400, detail="Missing required parameters")
        
        supabase = get_supabase_client()
        results = []
        
        for match in matches:
            # Extract required fields with fallbacks
            match_data = {
                "api_football_id": match.get("api_football_id"),
                "match_id": match.get("id") or match.get("match_id"),
                "league": league_id,
                "home": match.get("home_team") or match.get("home"),
                "away": match.get("away_team") or match.get("away"),
                "kickoff": match.get("utcDate") or match.get("kickoff"),
                "round": match.get("matchday") or match.get("round"),
                "season": season,
                "status": "scheduled"
            }
            
            # Insert or update
            response = supabase.table("league_calendar") \
                .upsert(match_data, on_conflict="api_football_id") \
                .execute()
                
            results.append(response.data)
        
        return {"status": "success", "count": len(results)}
        
    except Exception as e:
        print(f"Error loading league calendar: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))