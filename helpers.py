import json
import os
from typing import Dict, List
from supabase import create_client, Client

# Load environment variables for Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError(
        "Missing Supabase credentials: please set SUPABASE_URL and SUPABASE_KEY in the environment"
    )

# Initialize Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def load_teams() -> Dict:
    """Load teams dictionary from data/teams.json"""
    with open("data/teams.json", "r", encoding="utf-8") as f:
        return json.load(f)


def load_leagues() -> Dict:
    """Load leagues dictionary from data/leagues.json"""
    with open("data/leagues.json", "r", encoding="utf-8") as f:
        return json.load(f)


def load_matches_from_all_leagues(leagues_dict: Dict, teams_dict: Dict) -> List[Dict]:
    """
    Read every league JSON file under data/, enrich each match with team info,
    and return a flat list of matches.
    """
    all_matches: List[Dict] = []
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

            match_id = f"{match.get('league','').lower()}_{match.get('kickoff','').lower()}_{home_code.lower()}_x_{away_code.lower()}"

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


def get_live_score_from_supabase(match_id: str) -> Dict:
    """
    Query Supabase live_scores table for a given match_id.
    Returns a dict with keys: score (JSON or None), minute (str or None), status (str or None).
    """
    print(f"⚠️ [DEBUG] get_live_score_from_supabase CALLED for: {match_id}")
    try:
        result = (
            supabase
            .table("live_scores")
            .select("*")
            .eq("match_id", match_id)
            .limit(1)
            .execute()
        )
        print(f"🧾 Supabase result: data={result.data}, count={result.count}")
        if not result.data:
            return {"score": None, "minute": None, "status": None}

        row = result.data[0]
        print(f"📛 Raw row from DB: {row}")

        raw_score = row.get("score")
        score = None
        if isinstance(raw_score, str):
            try:
                score = json.loads(raw_score)
            except json.JSONDecodeError:
                print(f"⚠️ Failed to parse score JSON: {raw_score}")
                score = None
        else:
            score = raw_score

        return {
            "score": score,
            "minute": row.get("minute"),
            "status": row.get("status"),
        }
    except Exception as e:
        print(f"❌ Error fetching live score from Supabase: {e}")
        return {"score": None, "minute": None, "status": None}
