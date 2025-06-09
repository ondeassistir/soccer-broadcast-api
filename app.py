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
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_KEY environment variables")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
app = FastAPI(
    title="OndeAssistir Soccer API",
    version="1.2.1",
    description="Fetches upcoming matches and live scores with Flashscore fallback."
)

# Helper to GET with simple retry

def safe_get(url: str, timeout: int = 10):
    try:
        return requests.get(url, timeout=timeout)
    except requests.exceptions.ReadTimeout:
        return requests.get(url, timeout=timeout)

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/matches")
def get_upcoming_matches():
    """
    Fetch league IDs and their match lists from the remote API, filter to next LOOKAHEAD_DAYS.
    """
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=LOOKAHEAD_DAYS)
    upcoming = []

    # 1) fetch leagues index
    resp = safe_get(LEAGUES_URL)
    try:
        resp.raise_for_status()
    except Exception:
        raise HTTPException(status_code=502, detail="Could not fetch leagues index")
    data = resp.json()
    # parse league IDs
    if isinstance(data, dict):
        league_ids = list(data.keys())
    elif isinstance(data, list):
        league_ids = [item["id"] if isinstance(item, dict) and "id" in item else item
                      for item in data if isinstance(item, (dict, str))]
    else:
        league_ids = []

    # 2) fetch each league's matches
    for lid in league_ids:
        murl = f"{API_BASE_URL}/data/{lid}.json"
        r = safe_get(murl)
        if r.status_code == 404:
            continue
        try:
            r.raise_for_status()
        except Exception:
            continue
        matches = r.json()
        if not isinstance(matches, list):
            continue
        for m in matches:
            if not isinstance(m, dict):
                continue
            match_id = m.get("id") or m.get("match_id")
            slug     = m.get("slug")
            home     = m.get("home_team") or m.get("home")
            away     = m.get("away_team") or m.get("away")
            time_str = m.get("utcDate") or m.get("kickoff") or m.get("start") or m.get("dateTime")
            if not (match_id and slug and time_str):
                continue
            try:
                dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            except Exception:
                continue
            if now <= dt <= cutoff:
                upcoming.append({
                    "match_id": match_id,
                    "slug":      slug,
                    "home_team": home,
                    "away_team": away,
                    "kickoff":   time_str,
                    "league":    lid
                })
    return upcoming

@app.get("/score/{identifier}")
def get_live_score(identifier: str):
    """
    Resolve identifier (match_id or slug) to numeric match_id, then return cached score or scrape Flashscore.
    """
    # resolve numeric ID
    if identifier.isdigit():
        match_id = int(identifier)
    else:
        # search remote JSON for slug
        match_id = None
        # reuse league list
        resp = safe_get(LEAGUES_URL)
        if resp.status_code == 200:
            entries = resp.json()
            league_ids = entries.keys() if isinstance(entries, dict) else [e.get("id") for e in entries if isinstance(e, dict)]
            for lid in league_ids:
                r = safe_get(f"{API_BASE_URL}/data/{lid}.json")
                if r.status_code != 200:
                    continue
                for m in r.json():
                    if isinstance(m, dict) and m.get("slug", "").lower() == identifier.lower():
                        match_id = m.get("id") or m.get("match_id")
                        break
                if match_id:
                    break
        if not match_id:
            raise HTTPException(status_code=404, detail=f"No match found for slug '{identifier}'")

    # 1) try Supabase cache
    cache = (
        supabase.table("live_scores")
        .select("match_id, status, minute, score, updated_at")
        .eq("match_id", match_id)
        .single()
        .execute()
    )
    if getattr(cache, "error", None):
        raise HTTPException(status_code=500, detail=cache.error.message)
    record = cache.data
    if record:
        try:
            record["score"] = json.loads(record.get("score", "{}"))
        except:
            record["score"] = {}
        return record

    # 2) fallback: scrape Flashscore page
    flash_url = f"https://www.flashscore.ca/game/soccer/{identifier}/"
    page = requests.get(flash_url, timeout=10)
    if page.status_code != 200:
        raise HTTPException(status_code=404, detail="Score not found on Flashscore")
    soup = BeautifulSoup(page.text, "html.parser")

    home_el   = soup.select_one(".home__score")
    away_el   = soup.select_one(".away__score")
    status_el = soup.select_one(".detailTime__status")
    minute_el = soup.select_one(".detailTime__minute")

    try:
        home = int(home_el.text.strip())
        away = int(away_el.text.strip())
    except Exception:
        raise HTTPException(status_code=502, detail="Failed to parse scores")

    status = status_el.text.strip() if status_el else "UNKNOWN"
    minute = minute_el.text.strip() if minute_el else None
    now_str = datetime.now(timezone.utc).isoformat()

    score_json = {"home": home, "away": away}
    upsert_record = {
        "match_id":   match_id,
        "status":     status,
        "minute":     minute,
        "score":      json.dumps(score_json),
        "updated_at": now_str
    }
    up = supabase.table("live_scores").upsert(upsert_record, on_conflict=["match_id"]).execute()
    if getattr(up, "error", None):
        print("⚠️ Supabase upsert failed:", up.error.message)

    return {
        "match_id":   match_id,
        "status":     status,
        "minute":     minute,
        "score":      score_json,
        "updated_at": now_str
    }
