from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import json
import os

app = FastAPI()

# Add CORS middleware to allow requests from your frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Replace with your frontend domain in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files from /data
app.mount("/data", StaticFiles(directory="data"), name="data")

# Load teams data once at startup
with open("data/teams.json", "r", encoding="utf-8") as f:
    teams = json.load(f)

# Enriched match data endpoint
@app.get("/matches")
async def get_matches():
    with open("data/matches.json", "r", encoding="utf-8") as f:
        raw_matches = json.load(f)

    enriched_matches = []
    for match in raw_matches:
        home_team_code = match["home_team"]
        away_team_code = match["away_team"]

        home_team = teams.get(home_team_code.upper(), {})
        away_team = teams.get(away_team_code.upper(), {})

        enriched_matches.append({
            "match_id": f"{match['league']}_{match['kickoff']}_{home_team_code}_x_{away_team_code}",
            "league": match["league"],
            "league_week_number": match.get("league_week_number"),
            "kickoff": match["kickoff"],
            "broadcasts": match.get("broadcasts", {}),
            "home_team": {
                "id": home_team_code,
                "name": home_team.get("name", home_team_code),
                "badge": home_team.get("badge", ""),
                "venue": home_team.get("venue", ""),
            },
            "away_team": {
                "id": away_team_code,
                "name": away_team.get("name", away_team_code),
                "badge": away_team.get("badge", ""),
                "venue": away_team.get("venue", ""),
            }
        })

    return enriched_matches

# Debug endpoint to view files in /data folder
@app.get("/debug/data-files")
async def list_data_files():
    files = os.listdir("data")
    return {"files_in_data_folder": files}
