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

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static JSON data
app.mount("/data", StaticFiles(directory="data"), name="data")


@app.get("/matches")
async def get_matches(
    country: Optional[str] = Query(None),
    league:  Optional[str] = Query(None),
    team:    Optional[str] = Query(None),
    channel: Optional[str] = Query(None),
    date:    Optional[str] = Query(None)
):
    leagues_dict = load_leagues()
    teams_dict   = load_teams()
    all_matches  = load_matches_from_all_leagues(leagues_dict, teams_dict)

    # Apply filters
    if country:
        codes = [
            code for code, info in leagues_dict.items()
            if info.get("country", "").lower() == country.lower()
        ]
        all_matches = [m for m in all_matches if m["league"] in codes]

    if league:
        all_matches = [m for m in all_matches if m["league"] == league]

    if team:
        tu = team.upper()
        all_matches = [
            m for m in all_matches
            if m["home_team"]["id"] == tu or m["away_team"]["id"] == tu
        ]

    if channel:
        key = channel.lower()
        all_matches = [
            m for m in all_matches
            if any(
                key in [c.lower() for c in m["broadcasts"].get(loc, [])]
                for loc in m["broadcasts"]
            )
        ]

    if date:
        all_matches = [
            m for m in all_matches
            if m["kickoff"].startswith(date)
        ]

    # Sort by kickoff descending
    sorted_matches = sorted(
        all_matches,
        key=lambda m: m.get("kickoff", ""),
        reverse=True
    )

    # Enrich with live score
    enriched = []
    for m in sorted_matches:
        live = get_live_score_from_supabase(m["match_id"])
        if live:
            m.update({
                "status": live.get("status"),
                "minute": live.get("minute"),
                "score":  live.get("score"),
            })
        enriched.append(m)

    return enriched


@app.get("/matches/{match_id}")
async def get_match_detail(match_id: str):
    leagues_dict = load_leagues()
    teams_dict   = load_teams()
    all_matches  = load_matches_from_all_leagues(leagues_dict, teams_dict)

    for m in all_matches:
        if m["match_id"] == match_id:
            live = get_live_score_from_supabase(match_id)
            if live:
                m.update({
                    "status": live.get("status"),
                    "minute": live.get("minute"),
                    "score":  live.get("score"),
                })
            return m

    raise HTTPException(status_code=404, detail="Match not found")


@app.get("/teams")
async def get_teams(
    league: Optional[str] = Query(None),
    country: Optional[str] = Query(None)
):
    teams_dict = load_teams()
    out = []
    for t in teams_dict.values():
        if league and league not in t.get("leagues", []):
            continue
        if country and t.get("country", "").lower() != country.lower():
            continue
        out.append({
            "id":         t["id"],
            "name":       t["name"],
            "short_name": t.get("short_name", t["id"]),
            "badge":      t.get("badge", ""),
            "venue":      t.get("venue", ""),
            "country":    t.get("country", "")
        })
    return out


@app.get("/teams/{team_id}")
async def get_team_details(team_id: str):
    teams_dict = load_teams()
    team = teams_dict.get(team_id.upper())
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    leagues_dict = load_leagues()
    all_matches  = load_matches_from_all_leagues(leagues_dict, teams_dict)
    team_matches = [
        m for m in all_matches
        if m["home_team"]["id"] == team_id.upper()
           or m["away_team"]["id"] == team_id.upper()
    ]
    return {"team": team, "matches": team_matches}


@app.get("/country/{country_name}")
async def get_country_page(country_name: str):
    leagues_dict = load_leagues()
    cl = [
        {"code": code, **info}
        for code, info in leagues_dict.items()
        if info.get("country", "").lower() == country_name.lower()
    ]
    if not cl:
        raise HTTPException(status_code=404, detail="Country not found")
    return {"country": country_name, "leagues": cl}


@app.get("/league/{league_code}")
async def get_league_page(league_code: str):
    leagues_dict = load_leagues()
    league = leagues_dict.get(league_code)
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    teams_dict    = load_teams()
    all_matches   = load_matches_from_all_leagues(leagues_dict, teams_dict)
    league_matches = [m for m in all_matches if m["league"] == league_code]

    # Sort and split upcoming/past
    now = datetime.now(timezone.utc)
    upcoming, past = [], []
    for m in sorted(league_matches, key=lambda x: x.get("kickoff", ""), reverse=True):
        try:
            k = datetime.fromisoformat(m["kickoff"].replace("Z", "+00:00"))
            (upcoming if k >= now else past).append(m)
        except:
            continue

    return {
        "league":          league,
        "upcoming_matches": upcoming[:19],
        "past_matches":     past[:19]
    }


@app.get("/team/{team_id}")
async def get_team_page(team_id: str):
    # same as get_team_details
    teams_dict   = load_teams()
    team         = teams_dict.get(team_id.upper())
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    leagues_dict = load_leagues()
    all_matches  = load_matches_from_all_leagues(leagues_dict, teams_dict)
    team_matches = [
        m for m in all_matches
        if m["home_team"]["id"] == team_id.upper()
           or m["away_team"]["id"] == team_id.upper()
    ]
    return {"team": team, "matches": team_matches}
