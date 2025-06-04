from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from typing import List, Optional
import json
import os
from datetime import datetime
from helpers import (
    load_teams,
    load_leagues,
    load_matches_from_all_leagues,
    get_live_score,
)

app = FastAPI()

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Change this to your frontend URL for security
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve /data folder
app.mount("/data", StaticFiles(directory="data"), name="data")

# Load static dictionaries
teams = load_teams()
leagues = load_leagues()

@app.get("/matches")
async def get_matches(
    league: Optional[str] = None,
    team: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    channel: Optional[str] = None,
    page: int = 1,
    per_page: int = 10
):
    all_matches = load_matches_from_all_leagues(leagues, teams)

    # Filter by league
    if league:
        all_matches = [m for m in all_matches if m.get("league") == league]

    # Filter by team
    if team:
        team = team.upper()
        all_matches = [m for m in all_matches if m.get("home_team") == team or m.get("away_team") == team]

    # Filter by date range
    if start_date or end_date:
        def within_range(kickoff_str):
            try:
                dt = datetime.strptime(kickoff_str, "%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                return False
            if start_date:
                start = datetime.strptime(start_date, "%Y-%m-%d")
                if dt < start:
                    return False
            if end_date:
                end = datetime.strptime(end_date, "%Y-%m-%d")
                if dt > end:
                    return False
            return True
        all_matches = [m for m in all_matches if within_range(m.get("kickoff", ""))]

    # Filter by broadcast channel
    if channel:
        channel = channel.lower()
        all_matches = [
            m for m in all_matches
            if any(channel in [c.lower() for c in chans] for chans in m.get("broadcasts", {}).values())
        ]

    # Sort by kickoff date
    def sort_key(m):
        kickoff = m.get("kickoff", "No time yet")
        return kickoff if kickoff != "No time yet" else "9999-99-99T99:99:99Z"

    all_matches.sort(key=sort_key, reverse=True)

    # Pagination
    start = (page - 1) * per_page
    end = start + per_page
    paginated_matches = all_matches[start:end]

    # Add live score data (optional)
    for match in paginated_matches:
        match_id = match.get("match_id")
        if not match_id:
            continue
        live = get_live_score(match_id)
        if live:
            match.update(live)

    return paginated_matches

@app.get("/debug/data-files")
async def list_data_files():
    return {
        "root_files": os.listdir(),
        "data_files": os.listdir("data")
    }
