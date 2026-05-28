"""
AEGIS — TikTok Token Refresh Router
POST /api/token/refresh — called daily by Cloud Scheduler
GET  /api/token/status  — check current token status
"""
import os
import httpx
from fastapi import APIRouter, HTTPException
from datetime import datetime
from google.cloud import firestore

router = APIRouter()

def get_firestore():
    return firestore.Client(project="cybergrid")

@router.post("/api/token/refresh")
async def refresh_tiktok_token():
    client_key    = os.getenv("TIKTOK_CLIENT_KEY")
    client_secret = os.getenv("TIKTOK_CLIENT_SECRET")
    refresh_token = os.getenv("TIKTOK_REFRESH_TOKEN")

    if not all([client_key, client_secret, refresh_token]):
        raise HTTPException(status_code=500, detail="Missing TikTok credentials in environment")

    try:
        r = httpx.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            data={
                "client_key":    client_key,
                "client_secret": client_secret,
                "grant_type":    "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15
        )
        data = r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"TikTok token refresh failed: {e}")

    if "access_token" not in data:
        raise HTTPException(status_code=400, detail=f"TikTok error: {data}")

    new_access_token  = data["access_token"]
    new_refresh_token = data.get("refresh_token", refresh_token)
    expires_in        = data.get("expires_in", 86400)

    try:
        db = get_firestore()
        db.collection("aegis_config").document("tiktok_tokens").set({
            "access_token":  new_access_token,
            "refresh_token": new_refresh_token,
            "updated_at":    datetime.utcnow().isoformat(),
            "expires_in":    expires_in,
        })
        print(f"[TokenRefresh] Token written to Firestore at {datetime.utcnow().isoformat()}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Firestore write failed: {e}")

    os.environ["TIKTOK_ACCESS_TOKEN"]  = new_access_token
    os.environ["TIKTOK_REFRESH_TOKEN"] = new_refresh_token

    return {
        "status":     "refreshed",
        "expires_in": expires_in,
        "timestamp":  datetime.utcnow().isoformat(),
    }

@router.get("/api/token/status")
async def token_status():
    try:
        db = get_firestore()
        doc = db.collection("aegis_config").document("tiktok_tokens").get()
        if doc.exists:
            data = doc.to_dict()
            return {
                "status":     "found",
                "updated_at": data.get("updated_at"),
                "expires_in": data.get("expires_in"),
            }
        return {"status": "not_found"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
