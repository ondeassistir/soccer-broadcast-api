# scraper.py

import requests
from bs4 import BeautifulSoup

def get_live_score(match_id):
    try:
        # Simulate fetching from Flashscore or another live source
        # Replace with your own logic or URL pattern
        # For now, this will simulate a dummy result
        if "bot_x_cea" in match_id:
            return {
                "status": "live",
                "minute": "35",
                "score": "1 - 0"
            }
        else:
            return {
                "status": "upcoming",
                "minute": None,
                "score": None
            }
    except Exception as e:
        print(f"Live score fetch failed for {match_id}: {e}")
        return {
            "status": "unknown",
            "minute": None,
            "score": None
        }

import requests
from bs4 import BeautifulSoup
from supabase import create_client, Client
import os

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def update_live_score(match_id: str, score: str, minute: str, status: str):
    response = supabase.table("live_scores").upsert({
        "match_id": match_id,
        "score": score,
        "minute": minute,
        "status": status
    }).execute()
    return response

# Example usage (Replace with your scraper logic)
if __name__ == "__main__":
    # These would be dynamic values from your scraper
    match_id = "bra_a_2025-06-06t20:00:00z_bot_x_cea"
    score = "1-0"
    minute = "35"
    status = "1st half"

    result = update_live_score(match_id, score, minute, status)
    print(result)

