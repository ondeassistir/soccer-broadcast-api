from fastapi import FastAPI, Query, HTTPException
from typing import Optional
import json
import os
from cachetools import cached, TTLCache

app = FastAPI()

# Cache: 100 items, 10-minute TTL
cache = TTLCache(maxsize=100, ttl=600)

# Data location
MATCHES_FILE = "data/matches.json"

# Channel logo base URL
LOGO_BASE_URL = "https://ondeassistir.tv/images-scr/channels"

# Load all matches
@cached(cache)
def load_all_matches():
    if not os.path.exists(MATCHES_FILE):
        raise HTTPException(status_code=500, detail="matches.json not found")
    with open(MATCHES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

# GET /matches
@app.get("/matches")
def get_matches(
    country: str = Query(..., description="Country ID (e.g., 'brazil')"),
    league: Optional[str] = Query(None, description="League ID (e.g., 'serie-a')"),
    date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format")
):
    try:
        all_matches = load_all_matches()
    except HTTPException as e:
        raise e

    # Filter by country, league, date
    filtered = [
        match for match in all_matches
        if match.get("country_id") == country
        and (not league or match.get("league_id") == league)
        and (not date or match.get("date") == date)
    ]

    # Add full logo URL to each channel
    for match in filtered:
        match["channels"] = [
            {
                "id": ch,
                "logo": f"{LOGO_BASE_URL}/{ch}.png"
            }
            for ch in match.get("channel_ids", [])
        ]

    return {
        "country": country,
        "league": league,
        "date": date,
        "matches": filtered
    }
