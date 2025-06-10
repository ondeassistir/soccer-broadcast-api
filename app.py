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
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.getenv("DATA_DIR", os.path.join(BASE_DIR, "data"))
LOOKAHEAD_DAYS = int(os.getenv("LOOKAHEAD_DAYS", "5"))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_KEY environment variables")

# Initialize Supabase client
def get_supabase_client():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

# Load broadcast channel definitions
channels_path = os.path.join(DATA_DIR, "channels.json")
if os.path.isfile(channels_path):
    with open(channels_path, encoding="utf-8") as f:
        CHANNELS = json.load(f)
else:
    CHANNELS = {}

# Initialize FastAPI
title = "OndeAssistir Soccer API"
app = FastAPI(
    title=title,
    version="1.4.0",
    description="Serve upcoming matches, broadcasts, and live scores with robust fallbacks"
)
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")

# Load leagues and match data
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
        mid = m.get("id") or m.get("match_id") or m.get("matchId")
        # map numeric ID
        if mid is not None and slug:
            KEY_TO_SLUG[str(mid).lower()] = slug
        # synthetic composite key
        if slug and tstr and home and away:
            comp = f"{lid.lower()}_{tstr.lower()}_{home.lower()}_x_{away.lower()}"
            KEY_TO_SLUG[comp.lower()] = slug
        # map slug
        if slug:
            KEY_TO_SLUG[slug.lower()] = slug

def parse_datetime(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))

# Health check
@app.get("/health")
def health_check():
    return {"status": "ok", "version": app.version}

# Helper to enrich broadcasts by mapping channel IDs to channel objects
def enrich_broadcasts(raw: dict) -> dict:
    enriched = {}
    for country, ch_ids in (raw or {}).items():
        enriched[country] = [CHANNELS.get(ch) for ch in ch_ids if ch in CHANNELS]
    return enriched

# Upcoming matches endpoint
@app.get("/matches")
def get_upcoming_matches():
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=4)
    end = now + timedelta(days=LOOKAHEAD_DAYS)
    out = []
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
            if mid is not None:
                key = str(mid)
            else:
                key = f"{lid.lower()}_{tstr.lower()}_{m['home_team'].lower()}_x_{m['away_team'].lower()}"
            out.append({
                "match_id": key,
                "slug": m.get("slug"),
                "home": m.get("home_team"),
                "away": m.get("away_team"),
                "kickoff": tstr,
                "league": lid,
                "broadcasts": enrich_broadcasts(m.get("broadcasts"))
            })
    return out

# Single match details endpoint
@app.get("/matches/{identifier}")
def get_match(identifier: str):
    ident_lc = identifier.lower()
    for m in get_upcoming_matches():
        if m["match_id"].lower() == ident_lc:
            score_data = get_live_score(m["match_id"])
            m.update({
                "status": score_data["status"],
                "minute": score_data["minute"],
                "score": score_data["score"],
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
    # determine kickoff_dt for status logic
    kickoff_dt = None
    for matches in ALL_MATCHES.values():
        for m in matches:
            mid = m.get("id") or m.get("match_id") or m.get("matchId")
            comp = f"{m['league'].lower()}_{(m.get('kickoff') or m.get('utcDate')).lower()}_{m['home_team'].lower()}_x_{m['away_team'].lower()}"
            if (mid is not None and str(mid).lower() == identifier.lower()) or comp == identifier.lower():
                kt = m.get("kickoff") or m.get("utcDate")
                kickoff_dt = parse_datetime(kt) if kt else None
                break
        if kickoff_dt:
            break
    now = datetime.now(timezone.utc)

    supabase = get_supabase_client()
    resp = supabase.table("live_scores").select("match_id,status,minute,score,updated_at").eq("match_id", identifier).execute()
    if getattr(resp, "error", None):
        raise HTTPException(status_code=500, detail=resp.error.message)
    if resp.data:
        rec = resp.data[0]
        score = json.loads(rec.get("score", "{}"))
        status = rec.get("status") or "unknown"
        minute = rec.get("minute") or ""
        updated_at = rec.get("updated_at")
    else:
        # Scrape Flashscore page with fallbacks
        url = f"https://www.flashscore.ca/game/soccer/{slug}/"
        page = requests.get(url, timeout=10)
        if page.status_code != 200:
            raise HTTPException(status_code=404, detail="Score not found on Flashscore")
        soup = BeautifulSoup(page.text, "html.parser")
        # live selectors
        home_el = soup.select_one(".home__score")
        away_el = soup.select_one(".away__score")
        home = int(home_el.text.strip()) if home_el and home_el.text.strip().isdigit() else None
        away = int(away_el.text.strip()) if away_el and away_el.text.strip().isdigit() else None
        status_el = soup.select_one(".detailTime__status")
        minute_el = soup.select_one(".detailTime__minute")
        status = status_el.text.strip() if status_el else None
        minute = minute_el.text.strip() if minute_el else None
        # HTML scoreboard fallback
        if home is None or away is None:
            score_el = soup.select_one(".detailScore__score")
            if score_el:
                nums = re.findall(r"\d+", score_el.text)
                if len(nums) >= 2:
                    home, away = int(nums[0]), int(nums[1])
        # Next.js JSON fallback
        if home is None or away is None:
            script = soup.find("script", id="__NEXT_DATA__")
            if script and script.string:
                try:
                    d = json.loads(script.string)
                    evt = d.get("props", {}).get("pageProps", {}).get("initialState", {}).get("events", {}).get(slug)
                    if evt:
                        home = evt.get("homeScore")
                        away = evt.get("awayScore")
                        status = evt.get("status")
                        minute = evt.get("minute")
                except:
                    pass
        # JSON-LD fallback
        if home is None or away is None:
            ld = soup.find("script", type="application/ld+json")
            if ld and ld.string:
                try:
                    ldj = json.loads(ld.string)
                    if ldj.get("@type") == "SportsEvent":
                        home = ldj.get("homeScore")
                        away = ldj.get("awayScore")
                        status = ldj.get("eventStatus") or status
                except:
                    pass
        # normalize status
        if kickoff_dt and kickoff_dt > now:
            status = "upcoming"
            minute = ""
        elif status == "FT":
            status = "finished"
            minute = minute or "90"
        else:
            status = status or "in_progress"
        home = home if home is not None else 0
        away = away if away is not None else 0
        score = {"home": home, "away": away}
        updated_at = datetime.now(timezone.utc).isoformat()
        # upsert
        get_supabase_client().table("live_scores").upsert({
            "match_id": identifier,
            "status": status,
            "minute": minute,
            "score": json.dumps(score),
            "updated_at": updated_at
        }, on_conflict=["match_id"]).execute()
    return {
        "match_id": identifier,
        "status": status,
        "minute": minute,
        "score": score,
        "updated_at": updated_at
    }
