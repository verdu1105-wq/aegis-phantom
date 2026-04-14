"""
AEGIS PHANTOM - YouTube Live Chat Monitor
"""
import os
import json
import asyncio
from datetime import datetime, timezone
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

yt_router = APIRouter(prefix="/api/youtube", tags=["youtube"])
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

_state = {"active": False, "video_id": None, "live_chat_id": None, "page_token": None, "count": 0}

async def get_live_chat_id(video_id):
    if not YOUTUBE_API_KEY:
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{YOUTUBE_API_BASE}/videos", params={"part":"liveStreamingDetails","id":video_id,"key":YOUTUBE_API_KEY})
        data = resp.json()
        items = data.get("items", [])
        if not items:
            return None
        return items[0].get("liveStreamingDetails", {}).get("activeLiveChatId")

async def fetch_messages(live_chat_id, page_token=None):
    params = {"liveChatId":live_chat_id,"part":"snippet,authorDetails","maxResults":200,"key":YOUTUBE_API_KEY}
    if page_token:
        params["pageToken"] = page_token
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{YOUTUBE_API_BASE}/liveChat/messages", params=params)
        return resp.json()

@yt_router.post("/start")
async def start_yt(request: Request):
    body = await request.json()
    video_id = body.get("video_id", "").strip()
    if not video_id:
        return JSONResponse({"error": "video_id required"}, status_code=400)
    if not YOUTUBE_API_KEY:
        return JSONResponse({"error": "YouTube API key not configured"}, status_code=503)
    live_chat_id = await get_live_chat_id(video_id)
    if not live_chat_id:
        return JSONResponse({"error": "No active live chat found. Is the stream live?"}, status_code=404)
    _state.update({"video_id":video_id,"live_chat_id":live_chat_id,"active":True,"page_token":None,"count":0})
    return JSONResponse({"status":"monitoring","video_id":video_id,"live_chat_id":live_chat_id})

@yt_router.post("/stop")
async def stop_yt():
    _state.update({"active":False,"video_id":None,"live_chat_id":None})
    return JSONResponse({"status":"stopped"})

@yt_router.get("/status")
async def yt_status():
    return JSONResponse({"active":_state["active"],"video_id":_state["video_id"],"messages_processed":_state["count"]})

@yt_router.get("/poll")
async def poll_yt():
    if not _state["active"] or not _state["live_chat_id"]:
        return JSONResponse({"messages":[],"active":False})
    try:
        data = await fetch_messages(_state["live_chat_id"], _state["page_token"])
        if "error" in data:
            return JSONResponse({"messages":[],"error":data["error"].get("message","Unknown error")})
        _state["page_token"] = data.get("nextPageToken")
        messages = []
        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            author = item.get("authorDetails", {})
            if snippet.get("type") != "textMessageEvent":
                continue
            messages.append({
                "id": item.get("id"),
                "author": author.get("displayName","Unknown"),
                "is_moderator": author.get("isChatModerator", False),
                "is_owner": author.get("isChatOwner", False),
                "text": snippet.get("displayMessage",""),
                "published_at": snippet.get("publishedAt",""),
                "platform": "youtube"
            })
        _state["count"] += len(messages)
        return JSONResponse({"messages":messages,"count":len(messages),"poll_interval_ms":data.get("pollingIntervalMillis",5000),"active":True,"total":_state["count"]})
    except Exception as e:
        return JSONResponse({"messages":[],"error":str(e)})
