from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import json
import os

app = FastAPI()

# Mount the "data" folder as static files accessible at "/data"
app.mount("/data", StaticFiles(directory="data"), name="data")

# Example API endpoint to serve matches from the JSON file
@app.get("/matches")
async def get_matches():
    with open("data/matches.json", "r", encoding="utf-8") as f:
        matches = json.load(f)
    return matches
