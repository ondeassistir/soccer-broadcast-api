# helpers.py

import os
import json
from datetime import datetime
from bs4 import BeautifulSoup
import requests


def load_teams():
    with open("data/teams.json", "r", encoding="utf-8") as f:
        return json.load(f)


def load_leagues():
    with open("data/leagues.json", "r", encoding="utf-8") as f:
        return json.load(f)


def load_matches_from_all_leagues(leagues_dict, teams_dict):
    all_matches = []

    for league_code in leagues_dict:
        file_path = f"data/{league_code}.json"
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                try:
                    matches = json.load(f)
                except json.JSONDecodeError:
                    matches = []

for match in matches:
    if not isinstance(match, dict):
        continue  # Skip invalid entries

    home_code = match.get("home_team", "").upper()
    away_code = match.get("away_team", "").upper()

    home_team = teams_dict.get(home_code, {})
    away_team = teams_dict.get(away_code, {})

    enriched_match = {
        "match_id": f"{match['league'].lower()}_{match['kickoff'].lower()}_{home_code.lower()}_x_{away_code.lower()}",
        "league": match.get("league"),
        "league_week_number": match.get("league_week_number"),
        "kickoff": match.get("kickoff"),
        "broadcasts": match.get("broadcasts", {}),
        "home_team": {
            "id": home_code.lower(),
            "name": home_team.get("name", home_code),
            "badge": home_team.get("badge", ""),
            "venue": home_team.get("venue", "")
        },
        "away_team": {
            "id": away_code.lower(),
            "name": away_team.get("name", away_code),
            "badge": away_team.get("badge", ""),
            "venue": away_team.get("venue", "")
        },
        "score": {
            "home": None,
            "away": None
        },
        "status": "upcoming",
        "minute": None
    }

    all_matches.append(enriched_match)



def get_live_score(match_id):
    # Example dummy function (implement scraping logic here)
    return {
        "status": "live",  # or "upcoming", "finished"
        "minute": 45,
        "score": {
            "home": 1,
            "away": 2
        }
    }
