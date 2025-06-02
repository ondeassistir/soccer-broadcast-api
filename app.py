from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import json
import os
from datetime import datetime

app = FastAPI()

# CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/data", StaticFiles(directory="data"), name="data")


@app.get("/matches")
@app.get("/matches/{match_id}")
async def get_match_detail(match_id: str):
    from urllib.parse import unquote
    match_id = unquote(match_id)  # Ensure encoded characters are handled

    with open("data/matches.json", "r", encoding="utf-8") as f:
        matches = json.load(f)

    with open("data/teams.json", "r", encoding="utf-8") as f:
        teams = json.load(f)

    for match in matches:
        home_code = match["home_team"].upper()
        away_code = match["away_team"].upper()
        current_id = f"{match['league'].lower()}_{match['kickoff'].lower()}_{home_code.lower()}_x_{away_code.lower()}"

        if current_id == match_id:
            home = teams.get(home_code, {})
            away = teams.get(away_code, {})
            return {
                "match_id": current_id,
                "league": match["league"],
                "league_week_number": match.get("league_week_number"),
                "kickoff": match["kickoff"],
                "broadcasts": match.get("broadcasts", {}),
                "home_team": {
                    "id": home_code.lower(),
                    "name": home.get("name", home_code),
                    "badge": home.get("badge", ""),
                    "venue": home.get("venue", "")
                },
                "away_team": {
                    "id": away_code.lower(),
                    "name": away.get("name", away_code),
                    "badge": away.get("badge", ""),
                    "venue": away.get("venue", "")
                }
            }

    return {"error": "Match not found"}

async def get_matches(
    league: str = Query(None),
    country: str = Query(None),
    date: str = Query(None),  # Format: YYYY-MM-DD
):
    with open("data/matches.json", "r", encoding="utf-8") as f:
        matches = json.load(f)

    with open("data/teams.json", "r", encoding="utf-8") as f:
        teams = json.load(f)

    enriched_matches = []
    for match in matches:
        home_code = match["home_team"].upper()
        away_code = match["away_team"].upper()
        home = teams.get(home_code, {})
        away = teams.get(away_code, {})

        # Country-level filtering (based on broadcasts)
        if country:
            broadcasts = match.get("broadcasts", {})
            if country.lower() not in broadcasts:
                continue

        # Date filtering
        if date and match["kickoff"] != "No time yet":
            try:
                match_date = datetime.fromisoformat(match["kickoff"].replace("Z", "+00:00")).date()
                if match_date.isoformat() != date:
                    continue
            except:
                continue

        enriched_matches.append({
            "match_id": f"{match['league'].lower()}_{match['kickoff'].lower()}_{home_code.lower()}_x_{away_code.lower()}",
            "league": match["league"],
            "league_week_number": match.get("league_week_number"),
            "kickoff": match["kickoff"],
            "broadcasts": match.get("broadcasts", {}),
            "home_team": {
                "id": home_code.lower(),
                "name": home.get("name", home_code),
                "badge": home.get("badge", ""),
                "venue": home.get("venue", "")
            },
            "away_team": {
                "id": away_code.lower(),
                "name": away.get("name", away_code),
                "badge": away.get("badge", ""),
                "venue": away.get("venue", "")
            }
        })

    # League filtering after enrichment
    if league:
        enriched_matches = [m for m in enriched_matches if m["league"] == league]

    return enriched_matches

@app.get("/standings/{league}")
async def get_standings(league: str):
    import os

    league = league.upper()
    standings_file = f"data/standings_{league}.json"

    if not os.path.exists(standings_file):
        return {"error": f"Standings file for league {league} not found"}

    with open(standings_file, "r", encoding="utf-8") as f:
        standings = json.load(f)

    with open("data/teams.json", "r", encoding="utf-8") as f:
        teams = json.load(f)

    enriched = []
    for row in standings:
        team_code = row["team_id"].upper()
        team = teams.get(team_code, {})
        enriched.append({
            **row,
            "team_name": team.get("name", team_code),
            "badge": team.get("badge", ""),
        })

    return enriched

from fastapi.responses import HTMLResponse

@app.get("/admin", response_class=HTMLResponse)
async def admin_interface():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Admin JSON Editor</title>
        <style>
            body { font-family: sans-serif; padding: 2rem; background: #f2f2f2; }
            textarea { width: 100%; height: 400px; font-family: monospace; }
            select, button { margin: 1rem 0; padding: 0.5rem; }
        </style>
    </head>
    <body>
        <h2>📂 Edit JSON File</h2>
        <select id="fileSelect">
            <option value="matches.json">matches.json</option>
            <option value="teams.json">teams.json</option>
            <option value="standings_BRA_A.json">standings_BRA_A.json</option>
        </select>
        <button onclick="load()">Load</button>
        <button onclick="save()">Save</button>
        <br/>
        <textarea id="editor"></textarea>
        <script>
            async function load() {
                const file = document.getElementById('fileSelect').value;
                const res = await fetch('/data/' + file);
                const text = await res.text();
                document.getElementById('editor').value = text;
            }
            async function save() {
                const file = document.getElementById('fileSelect').value;
                const json = document.getElementById('editor').value;
                const res = await fetch('/admin/save', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ file, json })
                });
                const result = await res.json();
                alert(result.message || result.error);
            }
        </script>
    </body>
    </html>
    """
from fastapi import Request

@app.post("/admin/save")
async def save_file(request: Request):
    data = await request.json()
    filename = data.get("file")
    json_data = data.get("json")

    filepath = os.path.join("data", filename)
    if not os.path.exists(filepath):
        return {"error": f"{filename} not found"}

    try:
        parsed = json.loads(json_data)  # Validate JSON before saving
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(parsed, f, ensure_ascii=False, indent=2)
        return {"message": f"{filename} saved successfully ✅"}
    except json.JSONDecodeError as e:
        return {"error": f"JSON Error: {str(e)}"}

