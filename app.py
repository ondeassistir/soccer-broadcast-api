from fastapi import FastAPI
from fastapi.responses import FileResponse
import os

app = FastAPI()

@app.get("/data/matches")
async def get_matches():
    file_path = os.path.join("data", "matches.json")
    return FileResponse(file_path, media_type="application/json")

import os
from fastapi import FastAPI

app = FastAPI()

@app.get("/debug-files")
async def debug_files():
    root_files = os.listdir(".")
    data_files = os.listdir("./data") if os.path.exists("./data") else []
    return {
        "files_in_root": root_files,
        "files_in_data": data_files
    }
