import json
import os
from typing import Dict, List
import requests
from bs4 import BeautifulSoup


def load_teams() -> Dict:
    with open("data/teams.json", "r", encoding="utf-8") as f:
        return json.load(f)


def load_leagues() -> Dict:
    with open("data/leagues.json", "r", encoding="utf-8") as f:
        return json.load(f)


def load_matches_from_all_leagues(leagues_dict: Dict, teams_dict: Dict) -> List[Dict]:
    all_matches = []

    for league_code in leagues_dict:
        file_path = f"data/{league_code}.json"
        if not os.path.exists(file_path):
            continue

        with open(file_path, "r", encoding="utf-8") as f:
            try:
                matches = json.load(f)
            except json.JSONDecodeError:
                continue

        for match in matches:
            home_code = match.get("home_team", "").upper()
            away_code = match.get("away_team", "").upper()

            home_team = teams_dict.get(home_code, {})
            away_team = teams_dict.get(away_code, {})

            match_id = f"{match['league'].lower()}_{match['kickoff'].lower()}_{home_code.lower()}_x_{away_code.lower()}"

            enriched = {
                "match_id": match_id,
                "league": match.get("league"),
                "league_week_number": match.get("league_week_number"),
                "kickoff": match.get("kickoff"),
                "broadcasts": match.get("broadcasts", {}),
                "home_team": {
                    "id": home_code,
                    "name": home_team.get("name", home_code),
                    "badge": home_team.get("badge", ""),
                    "venue": home_team.get("venue", ""),
                },
                "away_team": {
                    "id": away_code,
                    "name": away_team.get("name", away_code),
                    "badge": away_team.get("badge", ""),
                    "venue": away_team.get("venue", ""),
                },
            }

            all_matches.append(enriched)

    return all_matches


def get_live_score(match_id: str) -> Dict:
    # Dummy fallback if scraping fails or is skipped
    return {
        "status": "upcoming",
        "minute": None,
        "score": {
            "home": None,
            "away": None
        }
    }

    # Example of actual scraping usage:
    # url = f"https://www.flashscore.com/match/{match_id}/"
    # try:
    #     response = requests.get(url)
    #     soup = BeautifulSoup(response.text, 'html.parser')
    #     # Scrape score and status here
    #     return { ... }
    # except:
    #     return fallback above

from scraper import get_live_score

from supabase import create_client, Client
import os

SUPABASE_URL = os.getenv("https://pmiwtahbdjzeifauzqun.supabase.co")
SUPABASE_KEY = os.getenv("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InBtaXd0YWhiZGp6ZWlmYXV6cXVuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDkwODc5NzgsImV4cCI6MjA2NDY2Mzk3OH0.of5GtYOz9THx6QdTgljZAbutuRTo4v77B819nRZ5GIM")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_live_score_from_supabase(match_id: str) -> dict:
    try:
        result = supabase.table("live_scores").select("*").eq("match_id", match_id).limit(1).execute()
        if result.data:
            row = result.data[0]
            return {
                "score": row.get("score"),
                "minute": row.get("minute"),
                "status": row.get("status")
            }
    except Exception as e:
        print(f"Error fetching live score from Supabase: {e}")
    
    # fallback
    return {
        "score": None,
        "minute": None,
        "status": None
    }
