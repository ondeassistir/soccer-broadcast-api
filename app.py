from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from typing import Optional
from datetime import datetime
from helpers import load_teams, load_leagues, load_matches_from_all_leagues, get_live_score

app = FastAPI()

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static data folder
app.mount("/data", StaticFiles(directory="data"), name="data")

# Load data
teams = load_teams()
leagues = load_leagues()
matches = load_matches_from_all_leagues(leagues, teams)

@app.get("/matches")
async def get_matches(
    league: Optional[str] = Query(None),
    team: Optional[str] = Query(None),
    country: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    page: int = Query(1, ge=1)
):
    filtered = matches

    # Filter by league
    if league:
        filtered = [m for m in filtered if m["league"].lower() == league.lower()]

    # Filter by team
    if team:
        team = team.lower()
        filtered = [
            m for m in filtered
            if m["home_team"]["id"].lower() == team or m["away_team"]["id"].lower() == team
        ]

    # Filter by country (channel availability)
    if country:
        filtered = [
            m for m in filtered
            if country.lower() in m.get("broadcasts", {})
        ]

    # Filter by date range
    def parse_date(d):
        return datetime.strptime(d, "%Y-%m-%d")

    if date_from:
        filtered = [
            m for m in filtered
            if m["kickoff"] != "No time yet" and datetime.strptime(m["kickoff"], "%Y-%m-%dT%H:%M:%SZ") >= parse_date(date_from)
        ]
    if date_to:
        filtered = [
            m for m in filtered
            if m["kickoff"] != "No time yet" and datetime.strptime(m["kickoff"], "%Y-%m-%dT%H:%M:%SZ") <= parse_date(date_to)
        ]

    # Sort by kickoff, most recent first
    def sort_key(m):
        return m["kickoff"] if m["kickoff"] != "No time yet" else "9999-99-99T99:99:99Z"

    filtered.sort(key=sort_key, reverse=True)

    # Pagination (10 per page)
    start = (page - 1) * 10
    end = start + 10
    paginated = filtered[start:end]

    # Add live score info
    for match in paginated:
        live = get_live_score(match["match_id"])
        if live:
            match["status"] = live.get("status")
            match["minute"] = live.get("minute")
            match["score"] = live.get("score")

    return paginated
