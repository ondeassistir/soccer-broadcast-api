import os
import json
import requests
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from bs4 import BeautifulSoup
from supabase import create_client, Client

# — DETERMINE DATA_DIR RELATIVE TO THIS FILE —
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.getenv("DATA_DIR", os.path.join(BASE_DIR, "data"))

# — CONFIGURATION —
LOOKAHEAD_DAYS = int(os.getenv("LOOKAHEAD_DAYS", "5"))
SUPABASE_URL   = os.getenv("SUPABASE_URL")
SUPABASE_KEY   = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI(
    title="OndeAssistir Soccer API",
    version="1.4.1",
    description="Serve /data locally, /matches from that, /score by ID or slug"
)

# serve static files at /data
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")

# DEBUG: log at startup what’s on disk
print("🔍 BASE_DIR =", BASE_DIR)
try:
    print("🔍 DATA_DIR contents:", os.listdir(DATA_DIR))
except Exception as e:
    print("⚠️ Could not list DATA_DIR:", e)

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/matches")
def get_upcoming_matches():
    """
    List upcoming matches in the next LOOKAHEAD_DAYS,
    reading leagues.json + each league's JSON from disk.
    """
    now    = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=LOOKAHEAD_DAYS)
    out = []

    leagues_path = os.path.join(DATA_DIR, "leagues.json")
    if not os.path.isfile(leagues_path):
        raise HTTPException(500, detail=f"leagues.json not found in {DATA_DIR}")
    try:
        leagues = json.load(open(leagues_path, encoding="utf-8"))
    except Exception as e:
        raise HTTPException(500, detail=f"Error parsing leagues.json: {e}")

    # build league id list
    if isinstance(leagues, dict):
        league_ids = list(leagues.keys())
    elif isinstance(leagues, list):
        league_ids = [
            item["id"] if isinstance(item, dict) and "id" in item else item
            for item in leagues
            if isinstance(item, (dict, str))
        ]
    else:
        league_ids = []

    # iterate each league file
    for lid in league_ids:
        fn   = f"{lid}.json"
        path = os.path.join(DATA_DIR, fn)
        if not os.path.isfile(path):
            continue
        try:
            matches = json.load(open(path, encoding="utf-8"))
        except:
            continue
        if not isinstance(matches, list):
            continue

        for m in matches:
            if not isinstance(m, dict):
                continue

            mid  = m.get("id") or m.get("match_id")
            slug = m.get("slug")
            home = m.get("home_team") or m.get("home")
            away = m.get("away_team") or m.get("away")
            tstr = (
                m.get("utcDate")
                or m.get("kickoff")
                or m.get("start")
                or m.get("dateTime")
            )
            if not (mid and slug and tstr):
                continue

            try:
                dt = datetime.fromisoformat(tstr.replace("Z", "+00:00"))
            except:
                continue

            if now <= dt <= cutoff:
                out.append({
                    "match_id":  mid,
                    "slug":       slug,
                    "home_team":  home,
                    "away_team":  away,
                    "kickoff":    tstr,
                    "league":     lid
                })

    return out

def resolve_match_id(identifier: str):
    # direct numeric?
    if identifier.isdigit():
        return int(identifier)
    # else scan all league files
    for fn in os.listdir(DATA_DIR):
        if fn in ("leagues.json",) or not fn.endswith(".json"):
            continue
        path = os.path.join(DATA_DIR, fn)
        try:
            data = json.load(open(path, encoding="utf-8"))
        except:
            continue
        if not isinstance(data, list):
            continue
        for m in data:
            if isinstance(m, dict) and m.get("slug", "").lower() == identifier.lower():
                return m.get("id") or m.get("match_id")
    return None

@app.get("/score/{identifier}")
def get_live_score(identifier: str):
    match_id = resolve_match_id(identifier)
    if match_id is None:
        raise HTTPException(404, detail=f"No match found for '{identifier}'")

    # 1) Try cached in Supabase
    resp = (
        supabase
        .table("live_scores")
        .select("match_id, status, minute, score, updated_at")
        .eq("match_id", match_id)
        .single()
        .execute()
    )
    if getattr(resp, "error", None):
        raise HTTPException(500, detail=resp.error.message)
    rec = resp.data
    if rec:
        try:
            rec["score"] = json.loads(rec.get("score", "{}"))
        except:
            rec["score"] = {}
        return rec

    # 2) Fallback to Flashscore scrape
    url  = f"https://www.flashscore.ca/game/soccer/{identifier}/"
    page = requests.get(url, timeout=10)
    if page.status_code != 200:
        raise HTTPException(404, detail="Score not found on Flashscore")

    soup      = BeautifulSoup(page.text, "html.parser")
    home_el   = soup.select_one(".home__score")
    away_el   = soup.select_one(".away__score")
    status_el = soup.select_one(".detailTime__status")
    minute_el = soup.select_one(".detailTime__minute")

    try:
        home = int(home_el.text.strip())
        away = int(away_el.text.strip())
    except:
        raise HTTPException(502, detail="Failed to parse scores")

    status = status_el.text.strip() if status_el else "UNKNOWN"
    minute = minute_el.text.strip() if minute_el else None
    now_str = datetime.now(timezone.utc).isoformat()
    score_json = {"home": home, "away": away}

    up = {
        "match_id":   match_id,
        "status":     status,
        "minute":     minute,
        "score":      json.dumps(score_json),
        "updated_at": now_str
    }
    res = supabase.table("live_scores").upsert(up, on_conflict=["match_id"]).execute()
    if getattr(res, "error", None):
        print("⚠️ Supabase upsert failed:", res.error.message)

    return {
        "match_id":   matc_
