# app.py

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from typing import Optional
from helpers import load_teams, load_leagues, load_matches_from_all_leagues, get_live_score

app = FastAPI()

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files (e.g., JSON data)
app.mount("/data", StaticFiles(directory="data"), name="data")

@app.get("/matches")
async def get_matches(
    country: Optional[str] = Query(None),
    league: Optional[str] = Query(None),
    team: Optional[str] = Query(None),
    channel: Optional[str] = Query(None),
    date: Optional[str] = Query(None)
):
    leagues_dict = load_leagues()
    teams_dict = load_teams()
    all_matches = load_matches_from_all_leagues(leagues_dict, teams_dict)

    # Apply filters
    if country:
        leagues_in_country = [code for code, l in leagues_dict.items() if l.get("country", "").lower() == country.lower()]
        all_matches = [m for m in all_matches if m.get("league") in leagues_in_country]

    if league:
        all_matches = [m for m in all_matches if m.get("league") == league]

    if team:
        team_upper = team.upper()
        all_matches = [m for m in all_matches if m.get("home_team", {}).get("id") == team_upper or m.get("away_team", {}).get("id") == team_upper]

    if channel:
        all_matches = [m for m in all_matches if any(channel.lower() in [c.lower() for c in m.get("broadcasts", {}).get(loc, [])] for loc in m.get("broadcasts", {}))]

    if date:
        all_matches = [m for m in all_matches if m.get("kickoff", "").startswith(date)]

    def sort_key(m):
        kickoff = m.get("kickoff", "No time yet")
        return kickoff if kickoff != "No time yet" else "9999-99-99T99:99:99Z"

    sorted_matches = sorted(all_matches, key=sort_key, reverse=True)
    return sorted_matches

@app.get("/matches/{match_id}")
async def get_match_detail(match_id: str):
    leagues_dict = load_leagues()
    teams_dict = load_teams()
    all_matches = load_matches_from_all_leagues(leagues_dict, teams_dict)

    for match in all_matches:
        if match.get("match_id") == match_id:
            return match

    raise HTTPException(status_code=404, detail="Match not found")

@app.get("/teams/{team_id}")
async def get_team_details(team_id: str):
    team_id = team_id.upper()
    teams_dict = load_teams()
    leagues_dict = load_leagues()

    team_data = teams_dict.get(team_id)
    if not team_data:
        raise HTTPException(status_code=404, detail="Team not found")

    all_matches = load_matches_from_all_leagues(leagues_dict, teams_dict)

    team_matches = [
        match for match in all_matches
        if match.get("home_team", {}).get("id") == team_id or match.get("away_team", {}).get("id") == team_id
    ]

    return {
        "team": team_data,
        "matches": team_matches
    }
