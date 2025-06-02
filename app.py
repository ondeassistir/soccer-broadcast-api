from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import json
import os

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files from /data
app.mount("/data", StaticFiles(directory="data"), name="data")


@app.get("/matches")
async def get_enriched_matches():
    with open("data/matches.json", "r", encoding="utf-8") as f:
        matches = json.load(f)

    with open("data/teams.json", "r", encoding="utf-8") as f:
        teams = json.load(f)

    enriched_matches = []
    for match in matches:
	home_team_code = match["home_team"].upper()
	away_team_code = match["away_team"].upper()

	home_team = teams.get(home_team_code, {})
	away_team = teams.get(away_team_code, {})

        enriched_matches.append({
            "match_id": f"{match['league'].lower()}_{match['kickoff'].lower()}_{home_team_code.lower()}_x_{away_team_code.lower()}",
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


@app.get("/debug/data-files")
async def list_data_files():
    files = os.listdir("data")
    return {"files_in_data_folder": files}
