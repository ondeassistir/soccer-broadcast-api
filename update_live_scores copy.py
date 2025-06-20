#!/usr/bin/env python3
import os
import sys
import time
import json
import logging
from datetime import datetime, timezone

import requests
from requests.adapters import HTTPAdapter, Retry
from supabase import create_client, Client

# ... your imports, supabase init, etc.

def get_fs_slug(match_id: str) -> str | None:
    res = supabase.table("match_slug_map") \
                 .select("slug") \
                 .eq("match_id", match_id) \
                 .single() \
                 .execute()
    return (res.data or {}).get("slug")

def scrape_and_update():
    matches = get_all_matches()  # however you fetch match_id list
    for match in matches:
        mid = match["match_id"]
        fs_slug = get_fs_slug(mid)
        if not fs_slug:
            print("⚠️ no slug for", mid)
            continue

        url = f"https://www.flashscore.com/match/{fs_slug}/#match-summary"
        print("🔍 scraping", url)
        try:
            live = scrape_match_page(url)   # your existing BS4 logic
        except Exception as e:
            print("❌ [scraper] failed for", mid, ":", e)
            continue

        upsert_live_score(mid, live.status, live.minute, live.score)
        print("✅ upserted", mid, live.status, live.score)

# rest of your script...


# ─── CONFIG ────────────────────────────────────────────────────────────────────

API_BASE = os.getenv("API_BASE", "https://soccer-api-7ykx.onrender.com")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")  # use your Supabase service-role key

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ Missing Supabase credentials (SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY).", file=sys.stderr)
    sys.exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ─── LOGGER ────────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("updater")

# ─── HELPERS ───────────────────────────────────────────────────────────────────

def get_session():
    s = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    return s


def load_leagues_from_api(session):
    url = f"{API_BASE}/data/leagues.json"
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    # unwrap if needed
    if isinstance(data, dict) and "leagues" in data:
        data = data["leagues"]
    return list(data.keys())


def fetch_matches_for_league(session, league_code):
    url = f"{API_BASE}/matches?league={league_code}"
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def upsert_live_score(match_id, status, minute, score):
    """Insert or update row in live_scores by match_id."""
    record = {
        "match_id": match_id,
        "status": status or "unknown",
        "minute": minute or "",
        "score": json.dumps(score or {"home": None, "away": None})
    }
    res = supabase.table("live_scores").upsert(record, on_conflict="match_id").execute()
    # supabase-py returns a response with status_code
    code = getattr(res, 'status_code', None)
    if code is not None and code >= 300:
        log.error(f"❌ supabase upsert failed for {match_id}: HTTP {code}")
    else:
        log.info(f"✅ upserted {match_id}")

# ─── MAIN LOOP ─────────────────────────────────────────────────────────────────

def scrape_and_update():
    session = get_session()
    leagues = load_leagues_from_api(session)
    log.info(f"🏁 Updating live scores for leagues: {', '.join(leagues)}")

    for league in leagues:
        try:
            matches = fetch_matches_for_league(session, league)
        except Exception as e:
            log.error(f"❌ failed to fetch matches for {league}: {e}")
            continue

        for m in matches:
            mid = m.get("match_id")
            if not mid:
                continue

            # TODO: replace with your real scraper call
            # from scraper import get_live_score
            # live = get_live_score(mid)
            live = {"status": None, "minute": None, "score": {}}

            upsert_live_score(mid, live.get("status"), live.get("minute"), live.get("score"))
            time.sleep(0.2)


def main():
    now = datetime.now(timezone.utc).isoformat()
    log.info(f"🚀 Starting live score update at {now}")
    try:
        scrape_and_update()
    except Exception:
        log.exception("💥 Unexpected error during live‐score update")
        sys.exit(1)

if __name__ == "__main__":
    main()
