import os
import json
from typing import Dict, List
from supabase import create_client, Client

# ✅ Load Supabase credentials from environment
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("❌ Missing Supabase credentials. Set SUPABASE_URL and SUPABASE_KEY in your environment.")

# ✅ Create Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

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

def get_live_score_from_supabase(match_id: str) -> dict:
    print("📛 Raw match_id in DB:", row.get("match_id"))
    print("📛 Raw score value:", row.get("score"), type(row.get("score")))
    print(f"🔍 Querying Supabase for match_id: {match_id}")
    try:
        result = supabase.table("live_scores").select("*").eq("match_id", match_id).limit(1).execute()
        print(f"🧾 Supabase result: data={result.data}")

        if result.data:
            row = result.data[0]

            score = row.get("score")
            if isinstance(score, str):
                try:
                    score = json.loads(score)
                except Exception:
                    print("⚠️ Could not parse score string as JSON")
                    score = None

            print(f"✅ Returning live score for {match_id}: {score}, {row.get('minute')}, {row.get('status')}")
            return {
                "score": score,
                "minute": row.get("minute"),
                "status": row.get("status")
            }

    except Exception as e:
        print(f"❌ Error fetching live score from Supabase: {e}")

    return {
        "score": None,
        "minute": None,
        "status": None
    }
