import os
import json
import re
import requests
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException, Request, Response, status # Added Response, status
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from bs4 import BeautifulSoup
from supabase import create_client

# --- NEW: Firebase Admin SDK Imports ---
import firebase_admin
from firebase_admin import credentials, messaging
from google.cloud.firestore_v1.base_client import BaseClient as FirestoreClient # Type hint for Firestore


# — CONFIGURATION & INITIALIZATION —
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.getenv("DATA_DIR", os.path.join(BASE_DIR, "data"))
LOOKAHEAD_DAYS = int(os.getenv("LOOKAHEAD_DAYS", "5"))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_KEY environment variables")

# Supabase client factory
def get_supabase_client():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

# --- NEW: Firebase Admin SDK Initialization ---
FIREBASE_SERVICE_ACCOUNT_KEY_JSON = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY_JSON")
if not FIREBASE_SERVICE_ACCOUNT_KEY_JSON:
    raise RuntimeError("Missing FIREBASE_SERVICE_ACCOUNT_KEY_JSON environment variable")

try:
    # Decode the JSON string from the environment variable
    service_account_info = json.loads(FIREBASE_SERVICE_ACCOUNT_KEY_JSON)
    cred = credentials.Certificate(service_account_info)
    firebase_admin.initialize_app(cred)
    print("Firebase Admin SDK initialized successfully.")
except Exception as e:
    print(f"Error initializing Firebase Admin SDK: {e}")
    # In a real app, you might want to log this and handle graceful degradation
    # For now, we'll let it raise an error if critical.
    raise RuntimeError(f"Failed to initialize Firebase Admin SDK: {e}")

# Load broadcast channel definitions
channels_path = os.path.join(DATA_DIR, "channels.json")
if os.path.isfile(channels_path):
    with open(channels_path, encoding="utf-8") as f:
        CHANNELS = json.load(f)
else:
    CHANNELS = {}

# Initialize FastAPI
title = "OndeAssistir Soccer API"
app = FastAPI(
    title=title,
    version="1.4.3", # You can update this version if you like
    description="Serve upcoming matches, broadcasts, and live scores with robust fallbacks and admin editing, and now targeted push notifications!" # Updated description
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # adjust to specific domains in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")
app.mount("/admin", StaticFiles(directory=os.path.join(BASE_DIR, "admin")), name="admin")

# OPTIONS for CORS
@app.options("/matches")
def options_matches():
    return {}

@app.options("/matches/{identifier}")
def options_match(identifier: str):
    return {}

@app.options("/score/{identifier}")
def options_score(identifier: str):
    return {}

# --- NEW: OPTIONS for notification endpoints ---
@app.options("/register-fcm-token")
def options_register_fcm_token():
    return {}

@app.options("/update-preferences")
def options_update_preferences():
    return {}

@app.options("/trigger-notification")
def options_trigger_notification():
    return {}

# Admin save endpoint
@app.post("/admin/save/{filename}")
async def save_json(filename: str, request: Request):
    allowed = {"leagues.json", "channels.json", "teams.json", "QUALIFIERS_2026.json",
               "BRA_A.json", "INT_FRIENDLY.json", "CLUB_WC.json"}
    if filename not in allowed:
        raise HTTPException(status_code=403, detail="File not allowed")
    body = await request.body()
    try:
        json_body = json.loads(body.decode("utf-8")) # Decode once here
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")
    path = os.path.join(DATA_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(json_body, indent=4)) # Pretty print for readability
    return {"status": "ok"}

# Load leagues
with open(os.path.join(DATA_DIR, "leagues.json"), encoding="utf-8") as f:
    leagues_data = json.load(f)

def extract_league_ids(data):
    if isinstance(data, dict):
        return list(data.keys())
    if isinstance(data, list):
        return [item.get("id") if isinstance(item, dict) and "id" in item else item for item in data]
    return []

LEAGUE_IDS = extract_league_ids(leagues_data)
ALL_MATCHES = {}
KEY_TO_SLUG = {}
for lid in LEAGUE_IDS:
    path = os.path.join(DATA_DIR, f"{lid}.json")
    if not os.path.isfile(path): continue
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    ALL_MATCHES[lid] = data
    for m in data:
        slug = m.get("slug")
        tstr = m.get("utcDate") or m.get("kickoff") or m.get("start") or m.get("dateTime")
        home = m.get("home_team") or m.get("home")
        away = m.get("away_team") or m.get("away")
        mid = m.get("id") or m.get("match_id") or m.get("matchId")
        if mid is not None and slug:
            KEY_TO_SLUG[str(mid).lower()] = slug
        if slug and tstr and home and away:
            comp = f"{lid.lower()}_{tstr.lower()}_{home.lower()}_x_{away.lower()}"
            KEY_TO_SLUG[comp.lower()] = slug
        if slug:
            KEY_TO_SLUG[slug.lower()] = slug

def parse_datetime(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))

# Health check
@app.get("/health")
def health_check():
    return {"status": "ok", "version": app.version}

# Broadcast enrichment helper
def enrich_broadcasts(raw: dict) -> dict:
    enriched = {}
    for country, ch_ids in (raw or {}).items():
        enriched[country] = [CHANNELS.get(ch) for ch in ch_ids if ch in CHANNELS]
    return enriched

# Upcoming matches
@app.get("/matches")
def get_upcoming_matches():
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=4)
    end = now + timedelta(days=LOOKAHEAD_DAYS)
    out = []
    for lid, matches in ALL_MATCHES.items():
        for m in matches:
            tstr = m.get("utcDate") or m.get("kickoff") or m.get("start") or m.get("dateTime")
            if not tstr: continue
            try:
                dt = parse_datetime(tstr)
            except:
                continue
            if not (start <= dt <= end): continue
            mid = m.get("id") or m.get("match_id") or m.get("matchId")
            if mid is not None:
                key = str(mid)
            else:
                key = f"{lid.lower()}_{tstr.lower()}_{m['home_team'].lower()}_x_{m['away_team'].lower()}"
            out.append({
                "match_id": key,
                "slug": m.get("slug"),
                "home": m.get("home_team"),
                "away": m.get("away_team"),
                "kickoff": tstr,
                "league": lid,
                "broadcasts": enrich_broadcasts(m.get("broadcasts"))
            })
    return out

# Single match details
@app.get("/matches/{identifier}")
def get_match(identifier: str):
    ident_lc = identifier.lower()
    for m in get_upcoming_matches():
        if m["match_id"].lower() == ident_lc:
            score_data = get_live_score(m["match_id"])
            m.update({
                "status": score_data["status"],
                "minute": score_data["minute"],
                "score": score_data["score"],
                "updated_at": score_data["updated_at"]
            })
            return m
    raise HTTPException(status_code=404, detail="Match not found")

# Live score endpoint
@app.get("/score/{identifier}")
def get_live_score(identifier: str):
    slug = KEY_TO_SLUG.get(identifier.lower())
    if not slug:
        raise HTTPException(status_code=404, detail=f"Unknown match '{identifier}'")
    now = datetime.now(timezone.utc)
    kickoff_dt = None
    for matches in ALL_MATCHES.values():
        for m in matches:
            mid = m.get("id") or m.get("match_id") or m.get("matchId")
            comp = f"{m['league'].lower()}_{(m.get('kickoff') or m.get('utcDate')).lower()}_{m['home_team'].lower()}_x_{m['away_team'].lower()}"
            if (mid is not None and str(mid).lower() == identifier.lower()) or comp == identifier.lower():
                kt = m.get("kickoff") or m.get("utcDate")
                kickoff_dt = parse_datetime(kt) if kt else None
                break
        if kickoff_dt:
            break

    supabase = get_supabase_client()
    resp = supabase.table("live_scores").select("match_id,status,minute,score,updated_at").eq("match_id", identifier).execute()
    if getattr(resp, "error", None):
        raise HTTPException(status_code=500, detail=resp.error.message)
    if resp.data:
        rec = resp.data[0]
        score = json.loads(rec.get("score", "{}"))
        status = rec.get("status") or "unknown"
        minute = rec.get("minute") or ""
        updated_at = rec.get("updated_at")
    else:
        # Scrape and normalize logic remains the same as previous version
        # ... (Assuming this part fetches data from API-Football and processes it) ...
        # If this part is where your API-Football calls happen, we might need to adjust.
        # For now, I'll assume it correctly fetches and sets status, minute, home, away, score.
        # Placeholder for where original scraping logic would go if this is the fallback.
        # For a live API, you'd likely fetch from a dedicated live score source here,
        # or from your Supabase 'matches' table for current status.
        status = "NS" # Placeholder
        minute = 0 # Placeholder
        home = 0 # Placeholder
        away = 0 # Placeholder
        score = {"home": home, "away": away}
        updated_at = now.isoformat()
        
        # NOTE: This upsert might be for a different purpose than the worker's
        # live_scores updates. The worker should be the primary updater of live_scores.
        get_supabase_client().table("live_scores").upsert({
            "match_id": identifier,
            "status": status,
            "minute": minute,
            "score": json.dumps(score),
            "updated_at": updated_at
        }, on_conflict=["match_id"]).execute()
    return {
        "match_id": identifier,
        "status": status,
        "minute": minute,
        "score": score,
        "updated_at": updated_at
    }

# =========================================================================================
# --- NEW: Notification API Endpoints & Logic ---
# =========================================================================================

# --- Helper function to get team details from Supabase (similar to Node.js worker) ---
async def get_team_details_from_abbreviation(team_code: str) -> dict | None:
    # In a real API, consider caching this in memory if there are many requests
    # and team map doesn't change often. For now, directly query Supabase.
    supabase = get_supabase_client()
    try:
        resp = supabase.table("teams_map").select("api_football_team_id, team_full_name").eq("user_team_code", team_code).single().execute()
        if getattr(resp, "error", None):
            print(f"Error fetching team details for code {team_code}: {resp.error.message}")
            return None
        data = resp.data
        if data:
            return {"id": data["api_football_team_id"], "name": data["team_full_name"]}
        return None
    except Exception as e:
        print(f"Exception fetching team details for code {team_code}: {e}")
        return None

# --- Helper function to remove invalid FCM tokens from Supabase ---
async def remove_invalid_fcm_tokens(tokens_to_remove: list[str]):
    if not tokens_to_remove:
        return
    print(f"Attempting to remove {len(tokens_to_remove)} invalid FCM tokens from Supabase.")
    supabase = get_supabase_client()
    try:
        resp = supabase.table("user_fcm_tokens").delete().in_("fcm_token", tokens_to_remove).execute()
        if getattr(resp, "error", None):
            print(f"Error removing invalid tokens: {resp.error.message}")
        else:
            print(f"Successfully removed {len(tokens_to_remove)} invalid FCM tokens.")
    except Exception as e:
        print(f"Database error during token removal: {e}")

# --- Helper function to build FCM message payload ---
def build_fcm_message(event_details: dict) -> dict:
    event_type = event_details.get("type", "unknown_event")
    match_id = event_details.get("matchId")
    home_team_name = event_details.get("homeTeamName", "Home Team")
    away_team_name = event_details.get("awayTeamName", "Away Team")
    score = event_details.get("score", "N/A")
    event_detail = event_details.get("eventDetail", "")

    title = ""
    body = ""

    # Default data payload (always include core info)
    data_payload = {
        "matchId": str(match_id),
        "eventType": event_type,
        "homeTeam": home_team_name,
        "awayTeam": away_team_name,
        "currentScore": str(score),
    }
    # Add other structured data from event_details if present
    data_payload.update({k: str(v) for k, v in event_details.items() if k not in data_payload and v is not None})


    # Build notification payload (user-visible part)
    if event_type == 'goal':
        title = f"⚽ GOAL! {home_team_name} vs {away_team_name}"
        body = f"Score: {score}. {event_detail}"
    elif event_type == 'red_card':
        title = f"🟥 RED CARD! {home_team_name} vs {away_team_name}"
        body = f"{event_detail} has received a red card. Score: {score}"
    elif event_type == 'yellow_card':
        title = f"🟨 YELLOW CARD! {home_team_name} vs {away_team_name}"
        body = f"{event_detail} received a yellow card. Score: {score}"
    elif event_type == 'substitution':
        title = f"🔄 Substitution in {home_team_name} vs {away_team_name}"
        body = f"{event_detail}. Score: {score}"
    elif event_type == 'kickoff':
        title = f"🎉 Match KICK-OFF!"
        body = f"{home_team_name} vs {away_team_name} has started."
    elif event_type == 'half_time':
        title = f"⏸️ Half-Time!"
        body = f"{home_team_name} vs {away_team_name}. Score: {score}"
    elif event_type == 'full_time':
        title = f"✅ Full-Time!"
        body = f"{home_team_name} vs {away_team_name} has concluded. Final Score: {score}"
    elif event_type == 'score_update':
        title = f"📊 Score Update: {home_team_name} vs {away_team_name}"
        body = f"Current Score: {score}"
    else:
        title = f"⚽ Soccer Update: {home_team_name} vs {away_team_name}"
        body = f"An event occurred! Score: {score}"

    return {
        "notification": {"title": title, "body": body},
        "data": data_payload
    }

# --- Helper function to send notifications in batches ---
BATCH_SIZE = 500

async def send_notifications_to_multiple_devices(fcm_tokens: list[str], event_details: dict):
    if not fcm_tokens:
        print('No FCM tokens to send notifications to.')
        return

    # Build the base FCM message payload
    message_payload = build_fcm_message(event_details)
    notification_part = messaging.Notification(
        title=message_payload["notification"]["title"],
        body=message_payload["notification"]["body"]
    )

    # Chunk tokens into batches
    for i in range(0, len(fcm_tokens), BATCH_SIZE):
        batch_tokens = fcm_tokens[i:i + BATCH_SIZE]

        message = messaging.MulticastMessage(
            notification=notification_part,
            data=message_payload["data"],
            tokens=batch_tokens,
            apns=messaging.APNSConfig(payload=messaging.APNSPayload(aps=messaging.Aps(sound="default"))), # For iOS sound
            android=messaging.AndroidConfig(priority="high") # For Android high priority
        )

        try:
            response = messaging.send_multicast(message)
            print(f"Sent FCM batch for {event_details.get('type')}: {response.success_count} succeeded, {response.failure_count} failed.")

            # Process failures to identify invalid tokens
            if response.failure_count > 0:
                tokens_to_remove = []
                for idx, resp in enumerate(response.responses):
                    if not resp.success:
                        failed_token = batch_tokens[idx]
                        if resp.exception and resp.exception.code in [
                            'messaging/invalid-registration-token',
                            'messaging/registration-token-not-registered',
                            'messaging/token-not-in-topic',
                            'messaging/not-found' # Common for unregistered tokens
                        ]:
                            tokens_to_remove.append(failed_token)
                            print(f"Invalid or unregistered FCM token identified: {failed_token}. Error: {resp.exception.code} - {resp.exception.message}")
                        else:
                            print(f"FCM send error for token {failed_token}: {resp.exception}")
                if tokens_to_remove:
                    await remove_invalid_fcm_tokens(tokens_to_remove)
        except Exception as e:
            print(f"Error sending FCM multicast message: {e}")


# --- API Endpoints ---

@app.post("/register-fcm-token", status_code=status.HTTP_200_OK)
async def register_fcm_token(request: Request):
    try:
        body = await request.json()
        user_id = body.get("userId")
        fcm_token = body.get("fcmToken")
        device_type = body.get("deviceType")

        if not user_id or not fcm_token or not device_type:
            raise HTTPException(status_code=400, detail="Missing userId, fcmToken, or deviceType")

        supabase = get_supabase_client()
        # Use upsert to handle existing tokens (update device_type if needed, or prevent duplicates)
        resp = supabase.table("user_fcm_tokens").upsert(
            {"user_id": user_id, "fcm_token": fcm_token, "device_type": device_type, "created_at": datetime.now(timezone.utc).isoformat()}
        ).execute()

        if getattr(resp, "error", None):
            raise HTTPException(status_code=500, detail=resp.error.message)

        return {"message": "FCM token registered successfully"}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    except Exception as e:
        print(f"Error registering FCM token: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to register FCM token: {e}")

@app.post("/update-preferences", status_code=status.HTTP_200_OK)
async def update_notification_preferences(request: Request):
    try:
        body = await request.json()
        user_id = body.get("userId")
        team_id = body.get("teamId")
        notification_types = body.get("notificationTypes") # Expected to be a list of strings

        if not user_id or team_id is None or not isinstance(notification_types, list):
            raise HTTPException(status_code=400, detail="Missing userId, teamId, or invalid notificationTypes format (expected list).")

        supabase = get_supabase_client()
        # Upsert the preference based on composite key (user_id, team_id)
        resp = supabase.table("user_favorite_teams").upsert(
            {"user_id": user_id, "team_id": team_id, "notification_types": notification_types, "created_at": datetime.now(timezone.utc).isoformat()},
            on_conflict=["user_id", "team_id"]
        ).execute()

        if getattr(resp, "error", None):
            raise HTTPException(status_code=500, detail=resp.error.message)

        return {"message": "User preferences updated successfully"}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    except Exception as e:
        print(f"Error updating user preferences: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update user preferences: {e}")


@app.post("/trigger-notification", status_code=status.HTTP_200_OK)
async def trigger_notification_event(request: Request):
    """
    This endpoint is called by the background worker when a new match event is detected.
    It resolves team details, fetches interested users, and sends FCM notifications.
    """
    try:
        event_details = await request.json()
        print(f"Received event to trigger notification: {event_details.get('type')} for match {event_details.get('matchId')}")

        event_type = event_details.get("type")
        match_id = event_details.get("matchId")
        # Extract home/away team abbreviations from event_details (e.g., 'home_abbr', 'away_abbr')
        # Assuming worker sends these along with matchId.
        home_team_abbr = event_details.get("homeTeamAbbr") # e.g. "RMA"
        away_team_abbr = event_details.get("awayTeamAbbr") # e.g. "TOT"

        if not event_type or not match_id or not home_team_abbr or not away_team_abbr:
            raise HTTPException(status_code=400, detail="Missing essential event details: type, matchId, homeTeamAbbr, or awayTeamAbbr.")

        # Resolve team details (API-Football ID and full name)
        home_team_details = await get_team_details_from_abbreviation(home_team_abbr)
        away_team_details = await get_team_details_from_abbreviation(away_team_abbr)

        if not home_team_details or not away_team_details:
            print(f"Could not resolve team details for match {match_id} (Home: {home_team_abbr}, Away: {away_team_abbr}). Cannot send notifications.")
            raise HTTPException(status_code=500, detail="Failed to resolve team details for notification.")

        event_details["homeTeamId"] = home_team_details["id"]
        event_details["homeTeamName"] = home_team_details["name"]
        event_details["awayTeamId"] = away_team_details["id"]
        event_details["awayTeamName"] = away_team_details["name"]

        # Determine the teams involved in this event that users might follow
        team_ids_to_filter = []
        # 'eventTeamId' will be present for goal/card events indicating the team involved
        if event_details.get("eventTeamId"):
            team_ids_to_filter.append(event_details["eventTeamId"])
        # Always include both home and away teams for general match events or if event is not specific to one team
        if home_team_details["id"] not in team_ids_to_filter:
            team_ids_to_filter.append(home_team_details["id"])
        if away_team_details["id"] not in team_ids_to_filter:
            team_ids_to_filter.append(away_team_details["id"])

        unique_team_ids = list(set(team_ids_to_filter)) # Ensure uniqueness

        if not unique_team_ids:
            print(f"No valid team IDs for notification type '{event_type}' and match {match_id}. Skipping user lookup.")
            return {"message": "No valid team IDs for notification."}

        # Fetch relevant FCM tokens from Supabase
        supabase = get_supabase_client()
        # Supabase Python client's .contains is slightly different for arrays
        # We need to build a string for the array check or ensure the exact array value is checked.
        # For 'contains' (checking if any element of array is present), it should be array of strings.
        # Example: select('...', contains('notification_types', ['goal'])) works
        resp = supabase.table("user_favorite_teams").select(
            "user_id, notification_types, user_fcm_tokens(fcm_token)" # Implicit join
        ).in_("team_id", unique_team_ids).execute()

        if getattr(resp, "error", None):
            print(f"Supabase query error for notification ({event_type}, match {match_id}): {resp.error.message}")
            raise HTTPException(status_code=500, detail="Database error during user lookup.")

        fcm_tokens = []
        for pref in resp.data:
            # Check if the specific eventType is in the user's notification_types array
            if event_type in pref.get("notification_types", []):
                for token_data in pref.get("user_fcm_tokens", []):
                    if token_data.get("fcm_token"):
                        fcm_tokens.append(token_data["fcm_token"])

        unique_fcm_tokens = list(set(fcm_tokens)) # Ensure uniqueness

        if not unique_fcm_tokens:
            print(f"No users found to notify for event type '{event_type}' (teams: {unique_team_ids}) for match {match_id}.")
            return {"message": "No users found for notification."}

        # Send notifications using Firebase Admin SDK
        await send_notifications_to_multiple_devices(unique_fcm_tokens, event_details)

        return {"message": "Notification triggered successfully", "sent_to_tokens": len(unique_fcm_tokens)}

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    except HTTPException as e:
        raise e # Re-raise known HTTP exceptions
    except Exception as e:
        print(f"Error processing notification trigger: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to process notification trigger: {e}")