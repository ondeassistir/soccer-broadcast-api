import json
import os
from typing import Dict, List
from supabase import create_client, Client

# Load Supabase credentials from environment
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def load_teams() -> Dict:
    with open("data/teams.json", "r", encoding="utf-8") as f:
        return json.load(f)


def load_leagues() -> Dict:
    with open("data/leagues.json", "r", encoding="utf-8") as f:
        return json.load(f)


def load_matches_from_all_leagues(leagues_dict: Dict, teams_dict: Dict) -> List[Dict]:
    all_matches: List[Dict] = []
    for code in leagues_dict:
        path = f"data/{code}.json"
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            try:
                matches = json.load(f)
            except json.JSONDecodeError:
                continue

        for m in matches:
            h = m.get("home_team", "").upper()
            a = m.get("away_team", "").upper()
            home = teams_dict.get(h, {})
            away = teams_dict.get(a, {})

            match_id = f"{m.get('league','').lower()}_{m.get('kickoff','').lower()}_{h.lower()}_x_{a.lower()}"

            enriched = {
                "match_id":        match_id,
                "league":          m.get("league"),
                "league_week_number": m.get("league_week_number"),
                "kickoff":         m.get("kickoff"),
                "broadcasts":      m.get("broadcasts", {}),
                "home_team": {
                    "id":    h,
                    "name":  home.get("name", h),
                    "badge": home.get("badge", ""),
                    "venue": home.get("venue", ""),
                },
                "away_team": {
                    "id":    a,
                    "name":  away.get("name", a),
                    "badge": away.get("badge", ""),
                    "venue": away.get("venue", ""),
                },
            }
            all_matches.append(enriched)
    return all_matches


def get_live_score_from_supabase(match_id: str) -> Dict:
    """
    Pulls the row for match_id from live_scores table and parses JSON scores.
    """
    try:
        res = (
            supabase
            .table("live_scores")
            .select("*")
            .eq("match_id", match_id)
            .limit(1)
            .execute()
        )
        if not res.data:
            return {"score": None, "minute": None, "status": None}

        row = res.data[0]
        raw = row.get("score")
        parsed = None
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
        else:
            parsed = raw

        return {
            "score":  parsed,
            "minute": row.get("minute"),
            "status": row.get("status"),
        }
    except Exception:
        return {"score": None, "minute": None, "status": None}
