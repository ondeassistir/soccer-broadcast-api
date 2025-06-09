import os
import json
import requests
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from bs4 import BeautifulSoup
from supabase import create_client, Client

# — CONFIGURATION & INITIALIZATION —
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
DATA_DIR       = os.getenv("DATA_DIR", os.path.join(BASE_DIR, "data"))
LOOKAHEAD_DAYS = int(os.getenv("LOOKAHEAD_DAYS", "5"))
SUPABASE_URL   = os.getenv("SUPABASE_URL")
SUPABASE_KEY   = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_KEY environment variables")

# Initialize Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Initialize FastAPI
app = FastAPI(
    title="OndeAssistir Soccer API",
    version="1.0.2",
    description="Serve upcoming matches and live scores with caching and Flashscore fallback"
)

# Mount static JSON directory
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")

# — STARTUP DATA LOADING & SANITY CHECKS —
print(f"🔍 BASE_DIR = {BASE_DIR}")
print(f"🔍 DATA_DIR contents: {os.listdir(DATA_DIR) if os.path.isdir(DATA_DIR) else 'Directory not found'}")

# Load leagues.json
leagues_path = os.path.join(DATA_DIR, "leagues.json")
if not os.path.isfile(leagues_path):
    raise RuntimeError(f"Missing leagues.json in {DATA_DIR}")
with open(leagues_path, encoding="utf-8") as f:
    leagues_data = json.load(f)

# Extract league IDs
def extract_league_ids(data):
    if isinstance(data, dict):
        return list(data.keys())
    if isinstance(data, list):
        return [item.get("id") if isinstance(item, dict) and "id" in item else item
                for item in data if isinstance(item, (dict, str))]
    return []

LEAGUE_IDS = extract_league_ids(leagues_data)

# Load all matches and build maps
ALL_MATCHES = {}           # league_id -> list of match dicts
KEY_TO_SLUG = {}           # identifier key -> slug

for lid in LEAGUE_IDS:
    path = os.path.join(DATA_DIR, f"{lid}.json")
    if not os.path.isfile(path):
        continue
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, list)
    except Exception as e:
        print(f"⚠️ Skipping {lid}.json: {e}")
        continue

    ALL_MATCHES[lid] = data
    for m in data:
        if not isinstance(m, dict):
            continue
        # numeric ID
        mid_num = m.get("id") or m.get("match_id") or m.get("matchId")
        slug = m.get("slug")
        tstr = m.get("utcDate") or m.get("kickoff") or m.get("start") or m.get("dateTime")
        home = m.get("home_team") or m.get("home")
        away = m.get("away_team") or m.get("away")
        # numeric key
        if mid_num is not None:
            KEY_TO_SLUG[str(mid_num)] = slug
        # synthetic key
        if mid_num is None and slug and all(isinstance(x, str) for x in (tstr, home, away)):
            syn = f"{lid.lower()}_{tstr.lower()}_{home.lower()}_x_{away.lower()}"
            KEY_TO_SLUG[syn] = slug
        # slug itself
        if slug:
            KEY_TO_SLUG[slug.lower()] = slug

print(f"✅ Loaded keys: {len(KEY_TO_SLUG)} entries")

# Helper: parse ISO datetime

def parse_datetime(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))

# — ENDPOINTS —
@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/matches")
def get_upcoming_matches():
    """
    Return matches from 4 days in the past up to LOOKAHEAD_DAYS in the future.
    """
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=4)
    end   = now + timedelta(days=LOOKAHEAD_DAYS)
    results = []

    for lid, matches in ALL_MATCHES.items():
        for m in matches:
            tstr = m.get("utcDate") or m.get("kickoff") or m.get("start") or m.get("dateTime")
            if not isinstance(tstr, str):
                continue
            try:
                dt = parse_datetime(tstr)
            except:
                continue
            if not (start <= dt <= end):
                continue

            mid_num = m.get("id") or m.get("match_id") or m.get("matchId")
            slug   = m.get("slug")
            home   = m.get("home_team") or m.get("home")
            away   = m.get("away_team") or m.get("away")

            if mid_num is not None:
                key = str(mid_num)
            else:
                key = f"{lid.lower()}_{tstr.lower()}_{home.lower()}_x_{away.lower()}"

            results.append({
                "match_id": key,
                "slug":      slug,
                "has_slug":  bool(slug),
                "home":      home,
                "away":      away,
                "kickoff":   tstr,
                "league":    lid
            })
    return results

@app.get("/score/{identifier}")
def get_live_score(identifier: str):
    slug = KEY_TO_SLUG.get(identifier.lower())
    if not slug:
        raise HTTPException(status_code=404, detail=f"Unknown match '{identifier}'")

    # try cache
    resp = (
        supabase
        .table("live_scores")
        .select("match_id, status, minute, score, updated_at")
        .eq("match_id", identifier)
        .single()
        .execute()
    )
    if getattr(resp, "error", None):
        raise HTTPException(status_code=500, detail=resp.error.message)
    record = resp.data
    if record:
        try:
            record["score"] = json.loads(record.get("score", "{}"))
        except:
            record["score"] = {}
        return record

    # fallback scrape
    url = f"https://www.flashscore.ca/game/soccer/{slug}/"
    page = requests.get(url, timeout=10)
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
    except:
        raise HTTPException(status_code=502, detail="Failed to parse scores from page")

    status = status_el.text.strip() if status_el else "UNKNOWN"
    minute = minute_el.text.strip() if minute_el else None
    now_str = datetime.now(timezone.utc).isoformat()
    score_json = {"home": home, "away": away}

    upsert_data = {
        "match_id":   identifier,
        "status":     status,
        "minute":     minute,
        "score":      json.dumps(score_json),
        "updated_at": now_str
    }
    up = supabase.table("live_scores").upsert(upsert_data, on_conflict=["match_id"]).execute()
    if getattr(up, "error", None):
        print("⚠️ Supabase upsert failed:", up.error.message)

    return {
        "match_id":   identifier,
        "status":     status,
        "minute":     minute,
        "score":      score_json,
        "updated_at": now_str
    }
