import json

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

def get_team_info(teams_data, team_id):
    return teams_data.get(team_id.upper(), {
        "name": team_id.upper(),
        "badge": "",
        "venue": ""
    })
