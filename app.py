import os
import json
import requests
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from bs4 import BeautifulSoup
from supabase import create_client, Client

# — CONFIGURATION —
DATA_DIR       = os.getenv("DATA_DIR", "./data")
LOOKAHEAD_DAYS = int(os.getenv("LOOKAHEAD_DAYS", "5"))
SUPABASE_URL   = os.getenv("SUPABASE_URL")
SUPABASE_KEY   = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_KEY environment variables")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI(
    title="OndeAssistir Soccer API",
    version="1.4.0",
    description="Static data under /data, upcoming matches under /matches, and live scores under /score/{id_or_slug}"
)

# Serve your JSON folder
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")

# DEBUG STARTUP: log what’s actually on disk
print("🔍 DATA_DIR is", DATA_DIR)
try:
    print("🔍 DATA_DIR contents:", os.listdir(DATA_DIR))
except Exception as e:
    print("⚠️ Failed to list DATA_DIR:", e)
# DEBUG END

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/matches")
def get_upcoming_matches():
    now    = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=LOOKAHEAD_DAYS)
    upcoming = []

    leagues_path = os.path.join(DATA_DIR, "leagues.json")
    try:
        with open(leagues_path, "r", encoding="utf-8") as f:
            leagues = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not read leagues.json: {e}")

    # leagues.json can be list or dict
    if isinstance(leagues, dict):
        league_ids = list(leagues.keys())
    elif isinstance(leagues, list):
        league_ids = [
            item["id"] if isinstance(item, dict) and "id" in item else item
            for item in leagues if isinstance(item, (dict, str))
        ]
    else:
        league_ids = []

    for lid in league_ids:
        fn = f"{lid}.json"
        path = os.path.join(DATA_DIR, fn)
        if not os.path.isfile(path):
            # missing file is normal
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                matches = json.load(f)
        except Exception:
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
    if identifier.isdigit():
        return int(identifier)
    # scan every league file
    for fn in os.listdir(DATA_DIR):
        if fn == "leagues.json" or not fn.endswith(".json"):
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
        raise HTTPException(status_code=404, detail=f"No match found for '{identifier}'")

    # 1) try cache
    resp = (
        supabase.table("live_scores")
                 .select("match_id, status, minute, score, updated_at")
                 .eq("match_id", match_id)
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

    # 2) fallback to Flashscore
    url = f"https://www.flashscore.ca/game/soccer/{identifier}/"
    page = requests.get(url, timeout=10)
    if page.status_code != 200:
        raise HTTPException(status_code=404, detail="Score not found on Flashscore")
    soup      = BeautifulSoup(page.text, "html.parser")
    home_el   = soup.select_one(".home__score")
    away_el   = soup.select_one(".away__score")
    status_el = soup.select_one(".detailTime__status")
    minute_el = soup.select_one(".detailTime__minute")

    try:
        home = int(home_el.text.strip())
        away = int(away_el.text.strip())
    except:
        raise HTTPException(status_code=502, detail="Failed to parse scores")

    status = status_el.text.strip() if status_el else "UNKNOWN"
    minute = minute_el.text.strip() if minute_el else None
    now_str = datetime.now(timezone.utc).isoformat()
    score_json = {"home": home, "away": away}

    upsert = {
        "match_id":   match_id,
        "status":     status,
        "minute":     minute,
        "score":      json.dumps(score_json),
        "updated_at": now_str
    }
    result = supabase.table("live_scores").upsert(upsert, on_conflict=["match_id"]).execute()
    if getattr(result, "error", None):
        print("⚠️ Supabase upsert failed:", result.error.message)

    return {
        "match_id":   match_id,
        "status":     status,
        "minute":     minute,
        "score":      score_json,
        "updated_at": now_str
    }
