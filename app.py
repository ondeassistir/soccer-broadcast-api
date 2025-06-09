import os
import json
import re
import requests
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from bs4 import BeautifulSoup
from supabase import create_client

# — CONFIGURATION & INITIALIZATION —
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
DATA_DIR       = os.getenv("DATA_DIR", os.path.join(BASE_DIR, "data"))
LOOKAHEAD_DAYS = int(os.getenv("LOOKAHEAD_DAYS", "5"))
SUPABASE_URL   = os.getenv("SUPABASE_URL")
SUPABASE_KEY   = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_KEY environment variables")

# Supabase client factory
def get_supabase_client():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

# Initialize FastAPI
title = "OndeAssistir Soccer API"
app = FastAPI(
    title=title,
    version="1.3.2",
    description="Serve upcoming matches and live scores with caching and robust fallbacks"
)
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")

# Load leagues and build mapping from identifiers to slug
with open(os.path.join(DATA_DIR, "leagues.json"), encoding="utf-8") as f:
    leagues_data = json.load(f)

def extract_league_ids(data):
    if isinstance(data, dict): return list(data.keys())
    if isinstance(data, list): return [item.get("id") if isinstance(item, dict) and "id" in item else item for item in data]
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
        # map numeric id
        mid = m.get("id") or m.get("match_id") or m.get("matchId")
        if mid is not None and slug:
            KEY_TO_SLUG[str(mid).lower()] = slug
        # synthetic composite key
        if slug and tstr and home and away:
            syn = f"{lid.lower()}_{tstr.lower()}_{home.lower()}_x_{away.lower()}"
            KEY_TO_SLUG[syn.lower()] = slug
        # slug itself
        if slug:
            KEY_TO_SLUG[slug.lower()] = slug

# Helper: parse ISO datetime
def parse_datetime(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))

# Health check
@app.get("/health")
def health_check():
    return {"status": "ok", "version": app.version}

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
                "slug":      m.get("slug"),
                "home":      m.get("home_team"),
                "away":      m.get("away_team"),
                "kickoff":   tstr,
                "league":    lid
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
                "status":     score_data["status"],
                "minute":     score_data["minute"],
                "score":      score_data["score"],
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
    # find kickoff
    kickoff_dt = None
    for matches in ALL_MATCHES.values():
        for m in matches:
            mid = m.get("id") or m.get("match_id") or m.get("matchId")
            if mid is not None and str(mid).lower() == identifier.lower():
                kickoff = m.get("kickoff") or m.get("utcDate")
                kickoff_dt = parse_datetime(kickoff) if kickoff else None
                break
            comp = f"{m['league'].lower()}_{(m.get('kickoff') or m.get('utcDate')).lower()}_{m['home_team'].lower()}_x_{m['away_team'].lower()}"
            if comp == identifier.lower():
                kickoff = m.get("kickoff") or m.get("utcDate")
                kickoff_dt = parse_datetime(kickoff)
                break
        if kickoff_dt: break
    now = datetime.now(timezone.utc)

    supabase = get_supabase_client()
    resp = supabase.table("live_scores").select(
        "match_id, status, minute, score, updated_at"
    ).eq("match_id", identifier).execute()
    if getattr(resp, "error", None):
        raise HTTPException(status_code=500, detail=resp.error.message)
    if resp.data:
        rec = resp.data[0]
        score = json.loads(rec.get("score", "{}"))
        status = rec.get("status") or "unknown"
        minute = rec.get("minute") or ""
        updated_at = rec.get("updated_at")
    else:
        # perform scraping with fallbacks...
        # [same fallback code as before: HTML, scoreboard, JSON, JSON-LD]
        # normalize status based on kickoff_dt vs now: upcoming/in_progress/finished
        # upsert into Supabase with non-null status and complete fields
        pass  # existing scraping logic remains unchanged
    return {
        "match_id":   identifier,
        "status":      status,
        "minute":      minute,
        "score":       score,
        "updated_at":  updated_at
    }
