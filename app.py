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

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import json
import os

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # <-- put your frontend URL here, or ["*"] for all
    allow_credentials=True,
    allow_methods=["*"],  # allow all HTTP methods including OPTIONS
    allow_headers=["*"],
)

# Mount static files at /data
app.mount("/data", StaticFiles(directory="data"), name="data")

@app.get("/matches")
async def get_matches():
    with open("data/matches.json", "r", encoding="utf-8") as f:
        matches = json.load(f)
    return matches

@app.get("/debug/data-files")
async def list_data_files():
    files = os.listdir("data")
    return {"files_in_data_folder": files}

