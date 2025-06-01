from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import json
import os

app = FastAPI()

# Mount the "data" folder as static files accessible at "/data"
app.mount("/data", StaticFiles(directory="data"), name="data")

@app.get("/matches")
async def get_matches():
    with open("data/matches.json", "r", encoding="utf-8") as f:
        matches = json.load(f)
    return matches

# Debug route to list files in the data folder
@app.get("/debug/data-files")
async def list_data_files():
    files = os.listdir("data")
    return {"files_in_data_folder": files}
