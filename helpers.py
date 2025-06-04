import json
import os
from datetime import datetime
from typing import List, Dict
import requests

# Load teams.json and leagues.json once
with open("data/teams.json", "r", encoding="utf-8") as f:
    teams = json.load(f)

with open("data/leagues.json", "r", encoding="utf-8") as f:
    leagues = json.load(f)

# Function to get match status and score from live scraper
def fetch_live_data(match_id: str) -> Dict:
    try:
        response = requests.get(f"https://ondeassistir-livescore.onrender.com/live/{match_id}")
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"Live data fetch failed for {match_id}: {e}")
    return {}

# Merge all match files into one list
def get_all_matches_with_enrichment() -> List[Dict]:
    matches = []
    for league_info in leagues:
        league_id = league_info["id"]
        path = f"data/{league_id}.json"
        if not os.path.exists(path):
            continue

        with open(path, "r", encoding="utf-8") as f:
            league_matches = json.load(f)

        for match in league_matches:
            home_id = match.get("home_team", "").upper()
            away_id = match.get("away_team", "").upper()

            home_team = teams.get(home_id, {})
            away_team = teams.get(away_id, {})

            match_id = f"{match['league'].lower()}_{match['kickoff'].lower()}_{home_id.lower()}_x_{away_id.lower()}"

            # Try to fetch live data
            live = fetch_live_data(match_id)

            matches.append({
                "match_id": match_id,
                "league": match["league"],
                "league_week_number": match.get("league_week_number"),
                "kickoff": match["kickoff"],
                "broadcasts": match.get("broadcasts", {}),
                "home_team": {
                    "id": home_id.lower(),
                    "name": home_team.get("name", home_id),
                    "badge": home_team.get("badge", ""),
                    "venue": home_team.get("venue", "")
                },
                "away_team": {
                    "id": away_id.lower(),
                    "name": away_team.get("name", away_id),
                    "badge": away_team.get("badge", ""),
                    "venue": away_team.get("venue", "")
                },
                "status": live.get("status", "TBD"),
                "minute": live.get("minute", None),
                "score": live.get("score", None)
            })

    return matches
