from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import json
import os

app = FastAPI()

# Enable CORS for all origins (adjust as needed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files (like matches.json and teams.json)
app.mount("/data", StaticFiles(directory="data"), name="data")


# Utility to load teams
def load_teams():
    with open("data/teams.json", "r", encoding="utf-8") as f:
        return json.load(f)


# Utility to load and enrich matches
def load_enriched_matches():
    with open("data/matches.json", "r", encoding="utf-8") as f:
        matches = json.load(f)

    teams = load_teams()
    enriched_matches = []

    for match in matches:
        home_code = match["home_team"].upper()
        away_code = match["away_team"].upper()

        home_team = teams.get(home_code, {})
        away_team = teams.get(away_code, {})

        match_id = f"{match['league'].lower()}_{match['kickoff'].lower()}_{home_code.lower()}_x_{away_code.lower()}"

        enriched_matches.append({
            "match_id": match_id,
            "league": match["league"],
            "league_week_number": match.get("league_week_number"),
            "kickoff": match["kickoff"],
            "broadcasts": match.get("broadcasts", {}),
            "home_team": {
                "id": home_code.lower(),
                "name": home_team.get("name", h_
