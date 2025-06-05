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
