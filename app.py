import os
import json
import requests
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException
from supabase import create_client, Client
from bs4 import BeautifulSoup

# — CONFIGURATION —
API_BASE_URL   = os.getenv("API_BASE_URL", "https://soccer-api-7ykx.onrender.com")
LEAGUES_URL    = f"{API_BASE_URL}/data/leagues.json"
LOOKAHEAD_DAYS = int(os.getenv("LOOKAHEAD_DAYS", "5"))
SUPABASE_URL   = os.getenv("SUPABASE_URL")
SUPABASE_KEY   = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
app = FastAPI(
    title="OndeAssistir Soccer API",
    version="1.3.0",
    description="Upcomings from /data, live-scores cached in Supabase, Flashscore fallback"
)

def safe_get(url: str):
    try:
        return requests.get(url, timeout=10)
    except requests.exceptions.ReadTimeout:
        return requests.get(url, timeout=10)

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/matches")
def get_upcoming_matches():
    now    = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=LOOKAHEAD_DAYS)
    upcoming = []

    # 1) Fetch league IDs
    resp = safe_get(LEAGUES_URL)
    if resp.status_code != 200:
        raise HTTPException(502, "Failed to fetch leagues.json")
    leagues = resp.json()
    # leagues.json is either a dict or list of strings/dicts
    if isinstance(leagues, dict):
        league_ids = list(leagues.keys())
    elif isinstance(leagues, list):
        league_ids = [
            item["id"] if isinstance(item, dict) and "id" in item else item
            for item in leagues
            if isinstance(item, (str, dict))
        ]
    else:
        league_ids = []

    # 2) Fetch each league’s matches
    for lid in league_ids:
        murl = f"{API_BASE_URL}/data/{lid}.json"
        mresp = safe_get(murl)
        if mresp.status_code != 200:
            continue
        matches = mresp.json()
        if not isinstance(matches, list):
            continue

        for m in matches:
            if not isinstance(m, dict):
                continue

            mid    = m.get("id") or m.get("match_id")
            slug   = m.get("slug")
            home   = m.get("home_team") or m.get("home")
            away   = m.get("away_team") or m.get("away")
            tstr   = m.get("utcDate") or m.get("kickoff") or m.get("start") or m.get("dateTime")
            if not (mid and slug and tstr):
                continue

            try:
                dt = datetime.fromisoformat(tstr.replace("Z", "+00:00"))
            except:
                continue

            if now <= dt <= cutoff:
                upcoming.append({
                    "match_id":  mid,
                    "slug":       slug,
                    "home_team":  home,
                    "away_team":  away,
                    "kickoff":    tstr,
                    "league":     lid
                })

    return upcoming

def resolve_match_id(identifier: str):
    """Numeric ID or case-insensitive slug lookup."""
    if identifier.isdigit():
        return int(identifier)

    # Fetch leagues.json again
    resp = safe_get(LEAGUES_URL)
    if resp.status_code != 200:
        return None
    leagues = resp.json()
    if isinstance(leagues, dict):
        league_ids = list(leagues.keys())
    elif isinstance(leagues, list):
        league_ids = [item["id"] if isinstance(item, dict) and "id" in item else item for item in leagues if isinstance(item, (str, dict))]
    else:
        league_ids = []

    for lid in league_ids:
        mresp = safe_get(f"{API_BASE_URL}/data/{lid}.json")
        if mresp.status_code != 200:
            continue
        matches = mresp.json()
        if not isinstance(matches, list):
            continue
        for m in matches:
            if isinstance(m, dict) and m.get("slug", "").lower() == identifier.lower():
                return m.get("id") or m.get("match_id")
    return None

@app.get("/score/{identifier}")
def get_live_score(identifier: str):
    # Resolve to numeric match_id
    match_id = resolve_match_id(identifier)
    if match_id is None:
        raise HTTPException(404, f"No match found for '{identifier}'")

    # 1) Try Supabase cache
    cache = (
        supabase
        .table("live_scores")
        .select("match_id, status, minute, score, updated_at")
        .eq("match_id", match_id)
        .single()
        .execute()
    )
    if getattr(cache, "error", None):
        raise HTTPException(500, cache.error.message)
    rec = cache.data
    if rec:
        try:
            rec["score"] = json.loads(rec.get("score", "{}"))
        except:
            rec["score"] = {}
        return rec

    # 2) Fallback: scrape Flashscore
    url = f"https://www.flashscore.ca/game/soccer/{identifier}/"
    page = requests.get(url, timeout=10)
    if page.status_code != 200:
        raise HTTPException(404, "Score not found on Flashscore")

    soup = BeautifulSoup(page.text, "html.parser")
    home_el   = soup.select_one(".home__score")
    away_el   = soup.select_one(".away__score")
    status_el = soup.select_one(".detailTime__status")
    minute_el = soup.select_one(".detailTime__minute")

    try:
        home = int(home_el.text.strip())
        away = int(away_el.text.strip())
    except:
        raise HTTPException(502, "Failed to parse scores")

    status = status_el.text.strip() if status_el else "UNKNOWN"
    minute = minute_el.text.strip() if minute_el else None
    now_str = datetime.now(timezone.utc).isoformat()
    score_json = {"home": home, "away": away}

    uprec = {
        "match_id":   match_id,
        "status":     status,
        "minute":     minute,
        "score":      json.dumps(score_json),
        "updated_at": now_str
    }
    up = supabase.table("live_scores").upsert(uprec, on_conflict=["match_id"]).execute()
    if getattr(up, "error", None):
        print("⚠️ Supabase upsert failed:", up.error.message)

    return {
        "match_id":   match_id,
        "status":     status,
        "minute":     minute,
        "score":      score_json,
        "updated_at": now_str
    }
