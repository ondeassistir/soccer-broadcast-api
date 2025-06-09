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
    version="1.0.1",
    description="Serve upcoming matches and live scores with caching and Flashscore fallback"
)

# Mount static JSON directory
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")

# — STARTUP DATA LOADING & SANITY CHECKS —
print(f"🔍 BASE_DIR = {BASE_DIR}")
print(f"🔍 DATA_DIR = {DATA_DIR}")
try:
    files = os.listdir(DATA_DIR)
    print(f"🔍 DATA_DIR contents: {files}")
except Exception as e:
    print(f"⚠️ Could not list DATA_DIR: {e}")

# Load leagues.json
leagues_path = os.path.join(DATA_DIR, "leagues.json")
if not os.path.isfile(leagues_path):
    raise RuntimeError(f"Missing leagues.json in {DATA_DIR}")
with open(leagues_path, encoding="utf-8") as f:
    leagues_data = json.load(f)

# Extract league IDs
if isinstance(leagues_data, dict):
    LEAGUE_IDS = list(leagues_data.keys())
elif isinstance(leagues_data, list):
    LEAGUE_IDS = [item.get("id") if isinstance(item, dict) and "id" in item else item
                  for item in leagues_data if isinstance(item, (dict, str))]
else:
    LEAGUE_IDS = []

# Load all matches into memory and build slug/id maps
ALL_MATCHES = {}   # league_id -> list of match dicts
SLUG_MAP    = {}   # slug_lower -> match_id
ID_MAP      = {}   # match_id -> slug

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
    for match in data:
        if not isinstance(match, dict):
            continue
        # Support various ID key names
        mid = (match.get("id") or match.get("match_id") or match.get("matchId"))
        slug = match.get("slug")
        if mid is None:
            # debug missing ID
            print(f"⚠️ Missing ID for slug {slug} in league {lid}")
            continue
        # map slug to id
        if isinstance(slug, str):
            slug_key = slug.lower()
            SLUG_MAP[slug_key] = mid
            if mid not in ID_MAP:
                ID_MAP[mid] = slug

print(f"✅ Loaded matches for leagues: {list(ALL_MATCHES.keys())}")

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
    Return upcoming matches within LOOKAHEAD_DAYS,
    including matches that lack slugs (flagged via has_slug).
    """
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=LOOKAHEAD_DAYS)
    results = []

    for lid, matches in ALL_MATCHES.items():
        for m in matches:
            # pick kickoff or fallback
            tstr = (m.get("utcDate") or m.get("kickoff") or m.get("start") or m.get("dateTime"))
            if not isinstance(tstr, str):
                continue
            try:
                dt = parse_datetime(tstr)
            except Exception:
                continue
            if not (now <= dt <= cutoff):
                continue

            # extract fields
            mid = (m.get("id") or m.get("match_id") or m.get("matchId"))
            slug = m.get("slug")
            home = m.get("home_team") or m.get("home")
            away = m.get("away_team") or m.get("away")

            results.append({
                "match_id": mid,
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
    """
    Fetch live score by numeric match_id or Flashscore slug.
    Uses Supabase caching, then Flashscore fallback.
    """
    # 1) resolve match_id and slug
    if identifier.isdigit():
        match_id = int(identifier)
        slug = ID_MAP.get(match_id)
    else:
        slug_key = identifier.lower()
        match_id = SLUG_MAP.get(slug_key)
        slug = identifier

    if match_id is None:
        raise HTTPException(status_code=404, detail=f"Unknown match '{identifier}'")

    # 2) try Supabase cache
    cache = (
        supabase
        .table("live_scores")
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

    # 3) fallback scraping requires slug
    if not slug:
        raise HTTPException(status_code=404, detail="This match has no slug for live scraping yet.")
    flash_url = f"https://www.flashscore.ca/game/soccer/{slug}/"
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
        raise HTTPException(status_code=502, detail="Failed to parse scores from page")

    status = status_el.text.strip() if status_el else "UNKNOWN"
    minute = minute_el.text.strip() if minute_el else None
    now_str = datetime.now(timezone.utc).isoformat()
    score_json = {"home": home, "away": away}

    # cache in Supabase
    upsert_data = {
        "match_id":   match_id,
        "status":     status,
        "minute":     minute,
        "score":      json.dumps(score_json),
        "updated_at": now_str
    }
    up = supabase.table("live_scores").upsert(upsert_data, on_conflict=["match_id"]).execute()
    if getattr(up, "error", None):
        print("⚠️ Supabase upsert failed:", up.error.message)

    return {
        "match_id":   match_id,
        "status":     status,
        "minute":     minute,
        "score":      score_json,
        "updated_at": now_str
    }
