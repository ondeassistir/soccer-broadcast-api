import os
import json
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
    version="1.1.1",
    description="Static data under /data, upcoming matches under /matches, and live scores under /score/{identifier}"
)

# Mount your static JSON folder
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/matches")
def get_upcoming_matches():
    now    = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=LOOKAHEAD_DAYS)
    upcoming = []

    for fn in os.listdir(DATA_DIR):
        # — SKIP THE LEAGUE INDEX — 
        if fn == "leagues.json":
            continue
        if not fn.endswith(".json"):
            continue

        league_code = os.path.splitext(fn)[0]
        filepath    = os.path.join(DATA_DIR, fn)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                matches = json.load(f)
        except Exception:
            continue

        for m in matches:
            time_str = m.get("utcDate") or m.get("kickoff") or m.get("start")
            if not time_str:
                continue
            try:
                dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            except:
                continue
            if now <= dt <= cutoff:
                info = m.copy()
                info["league"] = league_code
                upcoming.append(info)

    return upcoming

def find_match_id(slug: str):
    """Lookup numeric match_id for a Flashscore slug in your static files."""
    for fn in os.listdir(DATA_DIR):
        if fn == "leagues.json" or not fn.endswith(".json"):
            continue
        data = json.load(open(os.path.join(DATA_DIR, fn), encoding="utf-8"))
        for m in data:
            if m.get("slug") == slug:
                return m.get("id")
    return None

@app.get("/score/{identifier}")
def get_live_score(identifier: str):
    # 1) resolve match_id
    if identifier.isdigit():
        match_id = int(identifier)
    else:
        match_id = find_match_id(identifier)
        if match_id is None:
            raise HTTPException(404, detail=f"No match found for slug '{identifier}'")

    # 2) try Supabase cache
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
    record = resp.data
    if record:
        try:
            record["score"] = json.loads(record.get("score", "{}"))
        except:
            record["score"] = {}
        return record

    # 3) fallback to Flashscore scrape
    flash_url = f"https://www.flashscore.ca/game/soccer/{identifier}/"
    page = requests.get(flash_url, timeout=10)
    if page.status_code != 200:
        raise HTTPException(404, detail="Score not found on Flashscore")
    soup = BeautifulSoup(page.text, "html.parser")

    # — adjust these selectors as needed —
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
    record     = {
        "match_id":   match_id,
        "status":     status,
        "minute":     minute,
        "score":      json.dumps(score_json),
        "updated_at": now_str
    }
    up = supabase.table("live_scores").upsert(record, on_conflict=["match_id"]).execute()
    if getattr(up, "error", None):
        print("⚠️ Supabase upsert failed:", up.error.message)

    return {
        "match_id":   match_id,
        "status":     status,
        "minute":     minute,
        "score":      score_json,
        "updated_at": now_str
    }
