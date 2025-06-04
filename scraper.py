# scraper.py

# This is a mock dictionary to simulate live scores.
# Replace this later with actual scraping logic or API calls.
MOCK_LIVE_SCORES = {
    "bra_a_2025-06-06t20:00:00z_bot_x_cea": {
        "status": "live",  # could be 'live', 'finished', 'upcoming'
        "minute": 53,
        "score": {
            "home": 1,
            "away": 0
        }
    },
    "club_wc_2025-06-08t18:00:00z_fla_x_ala": {
        "status": "upcoming",
        "minute": None,
        "score": None
    }
}


def get_live_score(match_id: str):
    """
    Given a match ID, return live score information.
    This function can later be replaced with real scraping logic.
    """
    return MOCK_LIVE_SCORES.get(match_id)
