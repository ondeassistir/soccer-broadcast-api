# app.py

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from helpers import load_teams, load_leagues, load_matches_from_all_leagues, get_live_score

app = FastAPI()

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files (e.g., JSON data)
app.mount("/data", StaticFiles(directory="data"), name="data")

@app.get("/matches")
async def get_matches():
    leagues_dict = load_leagues()
    teams_dict = load_teams()
    all_matches = load_matches_from_all_leagues(leagues_dict, teams_dict)

    def sort_key(m):
        kickoff = m.get("kickoff", "No time yet")
        return kickoff if kickoff != "No time yet" else "9999-99-99T99:99:99Z"

    sorted_matches = sorted(all_matches, key=sort_key, reverse=True)
    return sorted_matches

@app.get("/matches/{match_id}")
async def get_match_detail(match_id: str):
    leagues_dict = load_leagues()
    teams_dict = load_teams()
    all_matches = load_matches_from_all_leagues(leagues_dict, teams_dict)

    for match in all_matches:
        if match.get("match_id") == match_id:
            return match

    raise HTTPException(status_code=404, detail="Match not found")
