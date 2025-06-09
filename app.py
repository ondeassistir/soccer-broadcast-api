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
    version="1.1.2",
    description="Serve upcoming matches and live scores with caching and Flashscore fallback"
)
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")

# Load leagues and build mapping from identifiers to slug
with open(os.path.join(DATA_DIR, "leagues.json"), encoding="utf-8") as f:
    leagues_data = json.load(f)

def extract_league_ids(data):
    if isinstance(data, dict):
        return list(data.keys())
    if isinstance(data, list):
        return [item.get("id") if isinstance(item, dict) and "id" in item else item
                for item in data if isinstance(item, (dict, str))]
    return []

LEAGUE_IDS = extract_league_ids(leagues_data)
ALL_MATCHES = {}
KEY_TO_SLUG = {}

for lid in LEAGUE_IDS:
    path = os.path.join(DATA_DIR, f"{lid}.json")
    if not os.path.isfile(path):
        continue
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    ALL_MATCHES[lid] = data
    for m in data:
        slug = m.get("slug")
        tstr = m.get("utcDate") or m.get("kickoff") or m.get("start") or m.get("dateTime")
        home = m.get("home_team") or m.get("home")
        away = m.get("away_team") or m.get("away")
        if slug and tstr and home and away:
            syn = f"{lid.lower()}_{tstr.lower()}_{home.lower()}_x_{away.lower()}"
            KEY_TO_SLUG[syn] = slug
            KEY_TO_SLUG[slug.lower()] = slug

# Helper: parse ISO datetime
def parse_datetime(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))

# Health check endpoint
@app.get("/health")
def health_check():
    return {"status": "ok", "version": app.version}

# Upcoming matches endpoint
@app.get("/matches")
def get_upcoming_matches():
    now   = datetime.now(timezone.utc)
    start = now - timedelta(days=4)
    end   = now + timedelta(days=LOOKAHEAD_DAYS)
    out   = []
    for lid, matches in ALL_MATCHES.items():
        for m in matches:
            tstr = m.get("utcDate") or m.get("kickoff") or m.get("start") or m.get("dateTime")
            if not tstr:
                continue
            try:
                dt = parse_datetime(tstr)
            except:
                continue
            if not (start <= dt <= end):
                continue
            mid = m.get("id") or m.get("match_id") or m.get("matchId")
            key = str(mid) if mid is not None else f"{lid.lower()}_{tstr.lower()}_{m['home_team'].lower()}_x_{m['away_team'].lower()}"
            out.append({
                "match_id": key,
                "slug":      m.get("slug"),
                "has_slug":  bool(m.get("slug")),
                "home":      m.get("home_team"),
                "away":      m.get("away_team"),
                "kickoff":   tstr,
                "league":    lid
            })
    return out

# Live score endpoint
@app.get("/score/{identifier}")
def get_live_score(identifier: str):
    slug = KEY_TO_SLUG.get(identifier.lower())
    if not slug:
        raise HTTPException(status_code=404, detail=f"Unknown match '{identifier}'")

    supabase = get_supabase_client()
    # 1) Try cache lookup by match_id
    resp = (
        supabase
        .table("live_scores")
        .select("match_id, status, minute, score, updated_at")
        .eq("match_id", identifier)
        .execute()
    )
    if getattr(resp, "error", None):
        raise HTTPException(status_code=500, detail=resp.error.message)

    if resp.data:
        rec = resp.data[0]
        try:
            rec["score"] = json.loads(rec.get("score", "{}"))
        except:
            rec["score"] = {}
        return rec

    # 2) Cache miss → scrape Flashscore
    url = f"https://www.flashscore.ca/game/soccer/{slug}/"
    page = requests.get(url, timeout=10)
    if page.status_code != 200:
        raise HTTPException(status_code=404, detail="Score not found on Flashscore")
    soup = BeautifulSoup(page.text, "html.parser")

    # First try live-score selectors
    home_el = soup.select_one(".home__score")
    away_el = soup.select_one(".away__score")
    home = int(home_el.text.strip()) if home_el and home_el.text.strip().isdigit() else None
    away = int(away_el.text.strip()) if away_el and away_el.text.strip().isdigit() else None

    # Fallback for finished matches
    if home is None or away is None:
        score_el = soup.select_one(".detailScore__score")
        if score_el:
            nums = re.findall(r"\d+", score_el.text)
            if len(nums) >= 2:
                home, away = int(nums[0]), int(nums[1])

    status_el = soup.select_one(".detailTime__status")
    minute_el = soup.select_one(".detailTime__minute")
    status = status_el.text.strip() if status_el else None
    minute = minute_el.text.strip() if minute_el else None
    now_str = datetime.now(timezone.utc).isoformat()
    score_json = {"home": home, "away": away}

    # Upsert into Supabase under match_id
    upsert_data = {
        "match_id":   identifier,
        "status":     status,
        "minute":     minute,
        "score":      json.dumps(score_json),
        "updated_at": now_str
    }
    supabase.table("live_scores").upsert(upsert_data, on_conflict=["match_id"]).execute()

    return {
        "match_id":   identifier,
        "status":      status,
        "minute":      minute,
        "score":       score_json,
        "updated_at":  now_str
    }
