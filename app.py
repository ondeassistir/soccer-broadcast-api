from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import json
import os

app = FastAPI()

# Enable CORS for all origins (adjust if needed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static folder
app.mount("/data", StaticFiles(directory="data"), name="data")

# Load team metadata (badge, venue)
def load_teams():
    with open("data/teams.json", "r", encoding="utf-8") as f:
        return json.load(f)

teams_data = load_teams()

# Generate match_id and enrich match with venue/badge
def enrich_match(match):
    home = match.get("home_team").upper()
    away = match.get("away_team").upper()
    kickoff = match.get("kickoff")
    league = match.get("league")
    
    match_id = f"{league}_{kickoff}_{home}_x_{away}".lower().replace(" ", "_")

    home_info = teams_data.get(home, {})
    away_info = teams_data.get(away, {})

    return {
        "match_id": match_id,
        **match,
        "home_team_venue": home_info.get("venue", "Unknown"),
        "home_team_badge": home_info.get("badge", ""),
        "away_team_venue": away_info.get("venue", "Unknown"),
        "away_team_badge": away_info.get("badge", "")
    }

@app.get("/matches")
async def get_matches():
    with open("data/matches.json", "r", encoding="utf-8") as f:
        raw_matches = json.load(f)

    enriched_matches = [enrich_match(m) for m in raw_matches]
    return enriched_matches

# Debug route
@app.get("/debug/data-files")
async def list_data_files():
    files = os.listdir("data")
    return {"files_in_data_folder": files}
