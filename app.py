from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import json
import os
from datetime import datetime

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Change this to your frontend URL in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/data", StaticFiles(directory="data"), name="data")

def parse_date(date_str: str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except Exception:
        return None

@app.get("/matches")
async def get_matches(
    league: str | None = Query(None, description="League code, e.g. BRA_A"),
    date: str | None = Query(None, description="Match date, format YYYY-MM-DD"),
    country: str | None = Query(None, description="Country code for broadcast filtering, e.g. br"),
):
    with open("data/matches.json", "r", encoding="utf-8") as f:
        matches = json.load(f)

    filtered = []

    filter_date = parse_date(date) if date else None

    for match in matches:
        # Filter by league
        if league and match.get("league") != league:
            continue

        # Filter by date (compare only date portion of kickoff)
        if filter_date:
            try:
                kickoff_date = datetime.fromisoformat(match["kickoff"].replace("Z", "+00:00")).date()
                if kickoff_date != filter_date:
                    continue
            except Exception:
                continue

        # Filter by country in broadcasts
        if country:
            broadcasts = match.get("broadcasts", {})
            if country.lower() not in broadcasts:
                continue

        filtered.append(match)

    return filtered

@app.get("/standings/{league}")
async def get_standings(league: str):
    filename = f"data/standings-{league}.json"
    if not os.path.exists(filename):
        return {"error": "League standings not found."}
    with open(filename, "r", encoding="utf-8") as f:
        standings = json.load(f)
    return standings

# ... (rest of your existing app.py including /admin)


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

