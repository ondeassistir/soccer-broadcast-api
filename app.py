# app.py

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from helpers import load_teams, load_leagues, load_matches_from_all_leagues, get_live_score
from typing import Optional
from datetime import datetime

app = FastAPI()

# Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static data folder
app.mount("/data", StaticFiles(directory="data"), name="data")

# Load once at startup
teams_dict = load_teams()
leagues_dict = load_leagues()


def sort_key(m):
    kickoff = m.get("kickoff", "No time yet")
    return kickoff if kickoff != "No time yet" else "9999-99-99T99:99:99Z"


@app.get("/matches")
async def get_matches(
    league: Optional[str] = None,
    team: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    channel: Optional[str] = None,
    page: int = 1,
    page_size: int = 10
):
    all_matches = load_matches_from_all_leagues(leagues_dict, teams_dict)

    # Filters
    if league:
        all_matches = [m for m in all_matches if m["league"].lower() == league.lower()]

    if team:
        team = team.lower()
        all_matches = [m for m in all_matches if m["home_team"]["id"] == team or m["away_team"]["id"] == team]

    if date_from:
        try:
            date_from_dt = datetime.fromisoformat(date_from)
            all_matches = [m for m in all_matches if m.get("kickoff") and datetime.fromisoformat(m["kickoff"]) >= date_from_dt]
        except:
            pass

    if date_to:
        try:
            date_to_dt = datetime.fromisoformat(date_to)
            all_matches = [m for m in all_matches if m.get("kickoff") and datetime.fromisoformat(m["kickoff"]) <= date_to_dt]
        except:
            pass

    if channel:
        all_matches = [m for m in all_matches if channel.lower() in [c.lower() for c in m.get("broadcasts", {}).get("br", [])]]

    # Sort matches (most recent first)
    all_matches.sort(key=sort_key, reverse=True)

    # Pagination
    start = (page - 1) * page_size
    end = start + page_size
    return all_matches[start:end]


@app.get("/matches/{match_id}")
async def get_match_detail(match_id: str):
    all_matches = load_matches_from_all_leagues(leagues_dict, teams_dict)

    for match in all_matches:
        if match["match_id"] == match_id:
            # Try to add live score info
            try:
                live = get_live_score(match_id)
                match["status"] = live["status"]
                match["minute"] = live["minute"]
                match["score"] = live["score"]
            except:
                pass
            return match

    raise HTTPException(status_code=404, detail="Match not found")


@app.get("/debug/data-files")
async def debug_data_files():
    import os
    return {"files_in_data_folder": os.listdir("data")}
