# app.py

import os
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from helpers import (
    load_teams,
    load_leagues,
    load_matches_from_all_leagues,
    get_live_score_from_supabase
)

app = FastAPI()

# ─── CORS CONFIGURATION ──────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # adjust to your front-end origin if desired
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── SERVE STATIC “data” FOLDER ───────────────────────────────────────────────
app.mount("/data", StaticFiles(directory="data"), name="data")


# ─── DEBUG / TEST ROUTE: Query Supabase Directly ──────────────────────────────
@app.get("/test-live-score/{match_id}")
async def test_live_score(match_id: str):
    """
    Isolate a single Supabase lookup for debugging.
    """
    print(f"⚠️ [DEBUG] test-live-score called for: {match_id}")
    live = get_live_score_from_supabase(match_id)
    print(f"🧪 Test live result: {live}")
    return {"match_id": match_id, "live": live}


# ─── GET ALL MATCHES (with optional filters) ──────────────────────────────────
@app.get("/matches")
async def get_matches(
    country: Optional[str] = Query(None),
    league:  Optional[str] = Query(None),
    team:    Optional[str] = Query(None),
    channel: Optional[str] = Query(None),
    date:    Optional[str] = Query(None)
):
    """
    Returns a list of all enriched matches, optionally filtered by country, league,
    team, channel, or date. Each match is “enriched” by adding live score info
    (status, minute, score) from Supabase.
    """
    # 1) Load dictionaries
    leagues_dict = load_leagues()    # { "BRA_A": {...}, "QUALIFIERS_2026": {...}, ... }
    teams_dict   = load_teams()      # { "BOT": {...}, "NT_PAR": {...}, ... }

    # 2) Read every league’s JSON file and build a flat list of matches
    all_matches = load_matches_from_all_leagues(leagues_dict, teams_dict)

    # 3) Apply optional filters:
    if country:
        # Only keep matches whose league’s country matches
        leagues_in_country = [
            code for code, info in leagues_dict.items()
            if info.get("country", "").lower() == country.lower()
        ]
        all_matches = [m for m in all_matches if m.get("league") in leagues_in_country]

    if league:
        all_matches = [m for m in all_matches if m.get("league") == league]

    if team:
        team_upper = team.upper()
        all_matches = [
            m for m in all_matches
            if m.get("home_team", {}).get("id") == team_upper
               or m.get("away_team", {}).get("id") == team_upper
        ]

    if channel:
        all_matches = [
            m for m in all_matches
            if any(
                channel.lower() in [c.lower() for c in m.get("broadcasts", {}).get(loc, [])]
                for loc in m.get("broadcasts", {})
            )
        ]

    if date:
        # date must match the ISO prefix (e.g. “2025-06-05”)
        all_matches = [m for m in all_matches if m.get("kickoff", "").startswith(date)]

    # 4) Sort by kickoff descending (most recent first)
    def sort_key(m):
        return m.get("kickoff", "9999-99-99T99:99:99Z")

    sorted_matches = sorted(all_matches, key=sort_key, reverse=True)

    # 5) For each match, call Supabase to fill in “status”, “minute”, “score”
    enriched_matches = []
    for match in sorted_matches:
        mid = match["match_id"]
        print(f"⚠️ [DEBUG] get_live_score_from_supabase CALLED for: {mid}")
        live = get_live_score_from_supabase(mid)
        print(f"🧾 Supabase result for {mid}: {live}")

        # If live data exists, merge into the match dict
        if live:
            match.update({
                "status": live.get("status"),
                "minute": live.get("minute"),
                "score": live.get("score")
            })

        enriched_matches.append(match)

    return enriched_matches


# ─── GET SINGLE MATCH BY ID ──────────────────────────────────────────────────
@app.get("/matches/{match_id}")
async def get_match_detail(match_id: str):
    """
    Return one specific match, enriched with live score fields from Supabase.
    """
    leagues_dict = load_leagues()
    teams_dict   = load_teams()
    all_matches = load_matches_from_all_leagues(leagues_dict, teams_dict)

    for match in all_matches:
        if match.get("match_id") == match_id:
            print(f"⚠️ [DEBUG] get_live_score_from_supabase CALLED for: {match_id}")
            live = get_live_score_from_supabase(match_id)
            print(f"🧾 Supabase result for {match_id}: {live}")

            if live:
                match.update({
                    "status": live.get("status"),
                    "minute": live.get("minute"),
                    "score": live.get("score")
                })
            return match

    raise HTTPException(status_code=404, detail="Match not found")


# ─── LIST TEAMS (optional filter by league or country) ───────────────────────
@app.get("/teams")
async def get_teams(
    league: Optional[str] = Query(None),
    country: Optional[str] = Query(None)
):
    teams_dict = load_teams()
    filtered = []

    for team in teams_dict.values():
        if league and league not in team.get("leagues", []):
            continue
        if country and team.get("country", "").lower() != country.lower():
            continue
        filtered.append({
            "id":         team["id"],
            "name":       team["name"],
            "short_name": team.get("short_name", team["id"]),
            "badge":      team.get("badge", ""),
            "venue":      team.get("venue", ""),
            "country":    team.get("country", "")
        })

    return filtered


# ─── TEAM DETAILS (matches for a single team) ────────────────────────────────
@app.get("/teams/{team_id}")
async def get_team_details(team_id: str):
    teams_dict   = load_teams()
    team         = teams_dict.get(team_id.upper())
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    leagues_dict = load_leagues()
    all_matches  = load_matches_from_all_leagues(leagues_dict, teams_dict)
    team_matches = [
        m for m in all_matches
        if m.get("home_team", {}).get("id") == team_id.upper()
           or m.get("away_team", {}).get("id") == team_id.upper()
    ]

    return {"team": team, "matches": team_matches}


# ─── COUNTRY PAGE (list leagues in a country) ─────────────────────────────────
@app.get("/country/{country_name}")
async def get_country_page(country_name: str):
    leagues_dict = load_leagues()
    country_leagues = [
        {"code": code, **info}
        for code, info in leagues_dict.items()
        if info.get("country", "").lower() == country_name.lower()
    ]
    if not country_leagues:
        raise HTTPException(status_code=404, detail="Country not found")
    return {"country": country_name, "leagues": country_leagues}


# ─── LEAGUE PAGE (upcoming + past for a league) ───────────────────────────────
@app.get("/league/{league_code}")
async def get_league_page(league_code: str):
    leagues_dict = load_leagues()
    league = leagues_dict.get(league_code)
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    teams_dict   = load_teams()
    all_matches  = load_matches_from_all_leagues(leagues_dict, teams_dict)
    league_matches = [m for m in all_matches if m.get("league") == league_code]

    def sort_key(m):
        return m.get("kickoff", "9999-99-99T99:99:99Z")

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
        "league":          league,
        "upcoming_matches": upcoming[:19],
        "past_matches":     past[:19]
    }


# ─── TEAM PAGE (matches for a team) ──────────────────────────────────────────
@app.get("/team/{team_id}")
async def get_team_page(team_id: str):
    teams_dict   = load_teams()
    team         = teams_dict.get(team_id.upper())
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    leagues_dict = load_leagues()
    all_matches  = load_matches_from_all_leagues(leagues_dict, teams_dict)
    team_matches = [
        m for m in all_matches
        if m.get("home_team", {}).get("id") == team_id.upper()
           or m.get("away_team", {}).get("id") == team_id.upper()
    ]

    return {"team": team, "matches": team_matches}
