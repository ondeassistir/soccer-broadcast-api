from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from typing import Optional
from datetime import datetime, timezone
from helpers import (
    load_teams,
    load_leagues,
    load_matches_from_all_leagues,
    get_live_score_from_supabase
)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/data", StaticFiles(directory="data"), name="data")

@app.get("/matches")
async def get_matches(
    country: Optional[str] = Query(None),
    league: Optional[str] = Query(None),
    team: Optional[str] = Query(None),
    channel: Optional[str] = Query(None),
    date: Optional[str] = Query(None)
):
    leagues_dict = load_leagues()
    teams_dict = load_teams()
    all_matches = load_matches_from_all_leagues(leagues_dict, teams_dict)

    if country:
        leagues_in_country = [code for code, l in leagues_dict.items() if l.get("country", "").lower() == country.lower()]
        all_matches = [m for m in all_matches if m.get("league") in leagues_in_country]

    if league:
        all_matches = [m for m in all_matches if m.get("league") == league]

    if team:
        team_upper = team.upper()
        all_matches = [m for m in all_matches if m.get("home_team", {}).get("id") == team_upper or m.get("away_team", {}).get("id") == team_upper]

    if channel:
        all_matches = [m for m in all_matches if any(channel.lower() in [c.lower() for c in m.get("broadcasts", {}).get(loc, [])] for loc in m.get("broadcasts", {}))]

    if date:
        all_matches = [m for m in all_matches if m.get("kickoff", "").startswith(date)]

    def sort_key(m):
        kickoff = m.get("kickoff", "9999-99-99T99:99:99Z")
        return kickoff

    sorted_matches = sorted(all_matches, key=sort_key, reverse=True)
    enriched_matches = []
    for match in sorted_matches:
        try:
            live = get_live_score_from_supabase(match["match_id"])
            if live:
                match.update({
                    "status": live.get("status"),
                    "minute": live.get("minute"),
                    "score": live.get("score")
                })
        except Exception:
            pass
        enriched_matches.append(match)

    return enriched_matches

@app.get("/matches/{match_id}")
def test_live_score(match_id: str):
    live = get_live_score_from_supabase(match_id)
    print(f"🧪 Test live result: {live}")
    return {"match_id": match_id, "live": live}
async def get_match_detail(match_id: str):
    leagues_dict = load_leagues()
    teams_dict = load_teams()
    all_matches = load_matches_from_all_leagues(leagues_dict, teams_dict)

    for match in all_matches:
        if match.get("match_id") == match_id:
            try:
                live = get_live_score_from_supabase(match_id)
                if live:
                    match.update({
                        "status": live.get("status"),
                        "minute": live.get("minute"),
                        "score": live.get("score")
                    })
                    print(f"✅ Live score found: {match.get('score')} for {match['match_id']}")
            except Exception:
                pass
            return match

    raise HTTPException(status_code=404, detail="Match not found")
@app.get("/teams")
async def get_teams(league: Optional[str] = Query(None), country: Optional[str] = Query(None)):
    teams_dict = load_teams()
    filtered_teams = []

    for team in teams_dict.values():
        if league and league not in team.get("leagues", []):
            continue
        if country and team.get("country", "").lower() != country.lower():
            continue
        filtered_teams.append({
            "id": team["id"],
            "name": team["name"],
            "short_name": team.get("short_name", team["id"]),
            "badge": team.get("badge", ""),
            "venue": team.get("venue", ""),
            "country": team.get("country", "")
        })

    return filtered_teams

@app.get("/teams/{team_id}")
async def get_team_details(team_id: str):
    teams_dict = load_teams()
    team = teams_dict.get(team_id.upper())
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    leagues_dict = load_leagues()
    all_matches = load_matches_from_all_leagues(leagues_dict, teams_dict)
    team_matches = [m for m in all_matches if m.get("home_team", {}).get("id") == team_id.upper() or m.get("away_team", {}).get("id") == team_id.upper()]

    return {
        "team": team,
        "matches": team_matches
    }

@app.get("/country/{country_name}")
async def get_country_page(country_name: str):
    leagues_dict = load_leagues()
    country_leagues = [
        {"code": code, **data}
        for code, data in leagues_dict.items()
        if data.get("country", "").lower() == country_name.lower()
    ]
    if not country_leagues:
        raise HTTPException(status_code=404, detail="Country not found")
    return {"country": country_name, "leagues": country_leagues}

@app.get("/league/{league_code}")
async def get_league_page(league_code: str):
    leagues_dict = load_leagues()
    league = leagues_dict.get(league_code)
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    teams_dict = load_teams()
    all_matches = load_matches_from_all_leagues(leagues_dict, teams_dict)
    league_matches = [m for m in all_matches if m.get("league") == league_code]

    def sort_key(m):
        kickoff = m.get("kickoff", "9999-99-99T99:99:99Z")
        return kickoff

    sorted_matches = sorted(league_matches, key=sort_key, reverse=True)

    now = datetime.now(timezone.utc)
    upcoming = []
    past = []

    for match in sorted_matches:
        try:
            kickoff_time = datetime.fromisoformat(match.get("kickoff").replace("Z", "+00:00"))
            if kickoff_time >= now:
                upcoming.append(match)
            else:
                past.append(match)
        except Exception:
            pass

    return {
        "league": league,
        "upcoming_matches": upcoming[:19],
        "past_matches": past[:19]
    }

@app.get("/team/{team_id}")
async def get_team_page(team_id: str):
    teams_dict = load_teams()
    team = teams_dict.get(team_id.upper())
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    leagues_dict = load_leagues()
    all_matches = load_matches_from_all_leagues(leagues_dict, teams_dict)
    team_matches = [m for m in all_matches if m.get("home_team", {}).get("id") == team_id.upper() or m.get("away_team", {}).get("id") == team_id.upper()]

    return {
        "team": team,
        "matches": team_matches
    }
