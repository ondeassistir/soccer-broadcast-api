from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import json
import os
from datetime import datetime

app = FastAPI()

# CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/data", StaticFiles(directory="data"), name="data")


@app.get("/matches")
@app.get("/matches/{match_id}")
async def get_match_detail(match_id: str):
    from urllib.parse import unquote
    match_id = unquote(match_id)  # Ensure encoded characters are handled

    with open("data/matches.json", "r", encoding="utf-8") as f:
        matches = json.load(f)

    with open("data/teams.json", "r", encoding="utf-8") as f:
        teams = json.load(f)

    for match in matches:
        home_code = match["home_team"].upper()
        away_code = match["away_team"].upper()
        current_id = f"{match['league'].lower()}_{match['kickoff'].lower()}_{home_code.lower()}_x_{away_code.lower()}"

        if current_id == match_id:
            home = teams.get(home_code, {})
            away = teams.get(away_code, {})
            return {
                "match_id": current_id,
                "league": match["league"],
                "league_week_number": match.get("league_week_number"),
                "kickoff": match["kickoff"],
                "broadcasts": match.get("broadcasts", {}),
                "home_team": {
                    "id": home_code.lower(),
                    "name": home.get("name", home_code),
                    "badge": home.get("badge", ""),
                    "venue": home.get("venue", "")
                },
                "away_team": {
                    "id": away_code.lower(),
                    "name": away.get("name", away_code),
                    "badge": away.get("badge", ""),
                    "venue": away.get("venue", "")
                }
            }

    return {"error": "Match not found"}

async def get_matches(
    league: str = Query(None),
    country: str = Query(None),
    date: str = Query(None),  # Format: YYYY-MM-DD
):
    with open("data/matches.json", "r", encoding="utf-8") as f:
        matches = json.load(f)

    with open("data/teams.json", "r", encoding="utf-8") as f:
        teams = json.load(f)

    enriched_matches = []
    for match in matches:
        home_code = match["home_team"].upper()
        away_code = match["away_team"].upper()
        home = teams.get(home_code, {})
        away = teams.get(away_code, {})

        # Country-level filtering (based on broadcasts)
        if country:
            broadcasts = match.get("broadcasts", {})
            if country.lower() not in broadcasts:
                continue

        # Date filtering
        if date and match["kickoff"] != "No time yet":
            try:
                match_date = datetime.fromisoformat(match["kickoff"].replace("Z", "+00:00")).date()
                if match_date.isoformat() != date:
                    continue
            except:
                continue

        enriched_matches.append({
            "match_id": f"{match['league'].lower()}_{match['kickoff'].lower()}_{home_code.lower()}_x_{away_code.lower()}",
            "league": match["league"],
            "league_week_number": match.get("league_week_number"),
            "kickoff": match["kickoff"],
            "broadcasts": match.get("broadcasts", {}),
            "home_team": {
                "id": home_code.lower(),
                "name": home.get("name", home_code),
                "badge": home.get("badge", ""),
                "venue": home.get("venue", "")
            },
            "away_team": {
                "id": away_code.lower(),
                "name": away.get("name", away_code),
                "badge": away.get("badge", ""),
                "venue": away.get("venue", "")
            }
        })

    # League filtering after enrichment
    if league:
        enriched_matches = [m for m in enriched_matches if m["league"] == league]

    return enriched_matches
