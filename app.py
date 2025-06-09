import os
import json
import requests
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from bs4 import BeautifulSoup
from supabase import create_client, Client

# — CONFIGURATION —
DATA_DIR       = os.getenv("DATA_DIR", "./data")        # your mounted data folder
LOOKAHEAD_DAYS = int(os.getenv("LOOKAHEAD_DAYS", "5"))  # upcoming window
SUPABASE_URL   = os.getenv("SUPABASE_URL")
SUPABASE_KEY   = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI(
    title="OndeAssistir Soccer API",
    version="1.1.0",
    description="Serves static data and live scores (with Flashscore fallback)"
)

# static data under /data/*.json
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/matches")
def get_upcoming_matches():
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=LOOKAHEAD_DAYS)
    out = []
    for fn in os.listdir(DATA_DIR):
        if not fn.endswith(".json"):
            continue
        league = fn[:-5]
        matches = json.load(open(os.path.join(DATA_DIR, fn), encoding="utf-8"))
        for m in matches:
            t = m.get("utcDate") or m.get("kickoff") or m.get("start")
            if not t:
                continue
            try:
                dt = datetime.fromisoformat(t.replace("Z","+00:00"))
            except:
                continue
            if now <= dt <= cutoff:
                info = m.copy()
                info["league"] = league
                out.append(info)
    return out

def find_match_id(slug: str):
    """Look up numeric match_id by slug in your static JSON."""
    for fn in os.listdir(DATA_DIR):
        if not fn.endswith(".json"):
            continue
        for m in json.load(open(os.path.join(DATA_DIR, fn), encoding="utf-8")):
            if m.get("slug") == slug:
                return m.get("id")
    return None

@app.get("/score/{identifier}")
def get_live_score(identifier: str):
    # 1) resolve numeric match_id
    if identifier.isdigit():
        match_id = int(identifier)
    else:
        match_id = find_match_id(identifier)
        if match_id is None:
            raise HTTPException(status_code=404, detail=f"Unknown slug '{identifier}'")

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
        raise HTTPException(status_code=500, detail=resp.error.message)
    data = resp.data
    if data:
        # parse JSON string back into dict
        try:
            data["score"] = json.loads(data.get("score", "{}"))
        except:
            data["score"] = {}
        return data

    # 3) fallback to Flashscore scrape
    flash_url = f"https://www.flashscore.ca/game/soccer/{identifier}/"
    page = requests.get(flash_url, timeout=10)
    if page.status_code != 200:
        raise HTTPException(status_code=404, detail="Score not found on Flashscore")
    soup = BeautifulSoup(page.text, "html.parser")

    # — YOU MUST ADJUST THESE SELECTORS TO MATCH FLASHSCORE’S CURRENT HTML —
    home_el   = soup.select_one(".home__score")
    away_el   = soup.select_one(".away__score")
    status_el = soup.select_one(".detailTime__status")
    minute_el = soup.select_one(".detailTime__minute")

    try:
        home_score = int(home_el.text.strip())
        away_score = int(away_el.text.strip())
    except:
        raise HTTPException(status_code=502, detail="Could not parse score from page")

    status = status_el.text.strip() if status_el else "UNKNOWN"
    minute = minute_el.text.strip() if minute_el else None

    # upsert into Supabase for caching
    now_str = datetime.now(timezone.utc).isoformat()
    record = {
        "match_id":   match_id,
        "status":     status,
        "minute":     minute,
        "score":      json.dumps({"home": home_score, "away": away_score}),
        "updated_at": now_str
    }
    up = supabase.table("live_scores").upsert(record, on_conflict=["match_id"]).execute()
    if getattr(up, "error", None):
        print("⚠️ Supabase upsert failed:", up.error.message)

    return {
        "match_id":   match_id,
        "status":     status,
        "minute":     minute,
        "score":      {"home": home_score, "away": away_score},
        "updated_at": now_str
    }
