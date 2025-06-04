import json
import os

def load_teams():
    with open("data/teams.json", "r", encoding="utf-8") as f:
        return json.load(f)

def load_leagues():
    with open("data/leagues.json", "r", encoding="utf-8") as f:
        return json.load(f)

def load_matches_from_league(league_code):
    try:
        with open(f"data/{league_code}.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def load_matches_from_all_leagues():
    matches = []
    for file_name in os.listdir("data"):
        if file_name.endswith(".json") and file_name not in ["teams.json", "leagues.json", "channels.json"]:
            league_code = file_name.replace(".json", "")
            league_matches = load_matches_from_league(league_code)
            matches.extend(league_matches)
    return matches

def get_team_info(teams_data, team_id):
    return teams_data.get(team_id.upper(), {
        "name": team_id.upper(),
        "badge": "",
        "venue": ""
    })
