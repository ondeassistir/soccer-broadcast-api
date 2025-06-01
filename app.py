from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

app = FastAPI()

# Serve static files from the "data" directory at the "/data" path
app.mount("/data", StaticFiles(directory="data"), name="data")

# Optional: redirect root URL to a test file or health check
@app.get("/")
def root():
    return RedirectResponse(url="/data/matches.json")

# Optional: health check route
@app.get("/health")
def health():
    return {"status": "ok"}
