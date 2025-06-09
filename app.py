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
    version="1.3.0",
    description="Serve upcoming matches and live scores with caching and multiple fallback strategies"
)
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")

# Load leagues and mapping
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
    if not os.path.isfile(path): continue
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    ALL_MATCHES[lid] = data
    for m in data:
        slug = m.get("slug")
        tstr = m.get("utcDate") or m.get("kickoff") or m.get("start") or m.get("dateTime")
        home = m.get("home_team") or m.get("home")
        away = m.get("away_team") or m.get("away")
        if slug and tstr and home and away:
            key = m.get("id") or m.get("match_id") or m.get("matchId") or slug
            KEY_TO_SLUG[str(key).lower()] = slug
            KEY_TO_SLUG[slug.lower()] = slug

def parse_datetime(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))

# Health
@app.get("/health")
def health_check():
    return {"status": "ok", "version": app.version}

# Upcoming matches
@app.get("/matches")
def get_upcoming_matches():
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=4)
    end = now + timedelta(days=LOOKAHEAD_DAYS)
    result = []
    for lid, matches in ALL_MATCHES.items():
        for m in matches:
            tstr = m.get("utcDate") or m.get("kickoff") or m.get("start") or m.get("dateTime")
            if not tstr: continue
            try:
                dt = parse_datetime(tstr)
            except:
                continue
            if not (start <= dt <= end): continue
            key = m.get("id") or m.get("match_id") or m.get("matchId")
            if not key:
                key = f"{lid.lower()}_{tstr.lower()}_{m['home_team'].lower()}_x_{m['away_team'].lower()}"
            result.append({
                "match_id": str(key),
                "slug": m.get("slug"),
                "home": m.get("home_team"),
                "away": m.get("away_team"),
                "kickoff": tstr,
                "league": lid
            })
    return result

# Single match
@app.get("/matches/{identifier}")
def get_match(identifier: str):
    ident_lc = identifier.lower()
    for m in get_upcoming_matches():
        if m["match_id"].lower() == ident_lc:
            score = get_live_score(m["match_id"])
            m.update({
                "status": score["status"],
                "minute": score["minute"],
                "score": score["score"],
                "updated_at": score["updated_at"]
            })
            return m
    raise HTTPException(status_code=404, detail="Match not found")

# Live score
@app.get("/score/{identifier}")
def get_live_score(identifier: str):
    slug = KEY_TO_SLUG.get(identifier.lower())
    if not slug:
        raise HTTPException(status_code=404, detail="Unknown match '{identifier}'")
    # find kickoff
    kickoff = None
    for matches in ALL_MATCHES.values():
        for m in matches:
            key = m.get("id") or m.get("match_id") or m.get("matchId") or m.get("slug")
            if str(key).lower() == identifier.lower():
                kickoff = m.get("kickoff") or m.get("utcDate")
                break
        if kickoff: break
    now = datetime.now(timezone.utc)
    kickoff_dt = parse_datetime(kickoff) if kickoff else None

    supabase = get_supabase_client()
    resp = supabase.table("live_scores").select("match_id,status,minute,score,updated_at").eq("match_id",identifier).execute()
    if resp.error:
        raise HTTPException(status_code=500, detail=resp.error.message)
    if resp.data:
        rec = resp.data[0]
        score = json.loads(rec.get("score","{}"))
        status = rec.get("status")
        minute = rec.get("minute")
    else:
        # scrape
        url = f"https://www.flashscore.ca/game/soccer/{slug}/"
        page = requests.get(url,timeout=10)
        if page.status_code!=200:
            raise HTTPException(status_code=404, detail="Score not found")
        soup=BeautifulSoup(page.text,'html.parser')
        home_el=soup.select_one('.home__score'); away_el=soup.select_one('.away__score')
        home = int(home_el.text.strip()) if home_el and home_el.text.strip().isdigit() else None
        away = int(away_el.text.strip()) if away_el and away_el.text.strip().isdigit() else None
        status_el=soup.select_one('.detailTime__status'); minute_el=soup.select_one('.detailTime__minute')
        status = status_el.text.strip() if status_el else None
        minute = minute_el.text.strip() if minute_el else None
        # fallback HTML
        if home is None or away is None:
            score_el=soup.select_one('.detailScore__score')
            if score_el:
                nums=re.findall(r'\d+',score_el.text); home=int(nums[0]); away=int(nums[1])
        # fallback JSON
        if home is None or away is None:
            script=soup.find('script',id='__NEXT_DATA__')
            if script and script.string:
                d=json.loads(script.string)
                evt=d.get('props',{}).get('pageProps',{}).get('initialState',{}).get('events',{}).get(slug)
                if evt:
                    home=evt.get('homeScore'); away=evt.get('awayScore'); status=evt.get('status'); minute=evt.get('minute')
        # fallback JSON-LD
        if home is None or away is None:
            ld=soup.find('script',type='application/ld+json')
            if ld and ld.string:
                ldj=json.loads(ld.string)
                if ldj.get('@type')=='SportsEvent':
                    home=ldj.get('homeScore'); away=ldj.get('awayScore'); status=ldj.get('eventStatus',status)
        # normalize
        if kickoff_dt and kickoff_dt > now:
            status='upcoming'; minute=''
        elif status=='FT':
            status='finished'; minute='90'
        else:
            status='in_progress'
        score={"home":home or 0,"away":away or 0}
        now_str=datetime.now(timezone.utc).isoformat()
        up={"match_id":identifier,"status":status,"minute":minute,"score":json.dumps(score),"updated_at":now_str}
        supabase.table('live_scores').upsert(up,on_conflict=['match_id']).execute()
    return {"match_id":identifier,"status":status,"minute":minute,"score":score,"updated_at":rec.get("updated_at") if resp.data else now_str}
