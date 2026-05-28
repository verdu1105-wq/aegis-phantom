"""
SitRep Publish Router
Accepts video upload from React studio → triggers post agent
Endpoints:
  POST /api/sitrep/publish        — upload video + brief, post to platforms
  POST /api/sitrep/approve/{id}   — approve pending post from iPhone
  GET  /api/sitrep/publish/status/{id} — check post status
  GET  /api/sitrep/publish/history     — recent posts
"""

import json
import os
import tempfile
import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
import redis as redis_lib

publish_router = APIRouter(prefix="/api/sitrep", tags=["publish"])

REDIS_URL = os.getenv("REDIS_URL", "")
AUTOPOST  = os.getenv("AUTOPOST", "false").lower() == "true"

def get_redis():
    return redis_lib.from_url(REDIS_URL, decode_responses=True)


# ── CAPTION BUILDER ────────────────────────────────────────────────────────────
def build_caption(brief: dict, platforms: dict) -> str:
    cat   = brief.get("cat", brief.get("category", "intel")).upper()
    hook  = brief.get("hook", "")
    title = brief.get("title", brief.get("headline", ""))
    score = brief.get("score", "")

    hashtags = {
        "cyber":     "#CyberSecurity #InfoSec #ZeroDay #ThreatIntel",
        "military":  "#Military #Defense #OSINT #NationalSecurity",
        "political": "#Politics #Geopolitics #Intelligence #OSINT",
        "economic":  "#Economics #Markets #Sanctions #GlobalTrade",
    }
    tags = hashtags.get(brief.get("cat", ""), "#Intelligence #OSINT")

    caption = f"{hook}\n\n{title}\n\n{tags} #SitRep #Intelligence\n\nsitrep.media"

    # TikTok has 2200 char limit
    if platforms.get("tiktok") and len(caption) > 2200:
        caption = caption[:2197] + "..."

    return caption


# ── MARKETING CAPTIONS ─────────────────────────────────────────────────────────
MARKETING_POSTS = [
    {
        "feature": "Creator Studio",
        "hook": "You don't need OBS. You don't need a green screen. You need SitRep.",
        "body": "The SitRep Creator Studio gives you AI background removal, live intelligence teleprompter, and one-click TikTok posting — all in your browser.",
        "cta": "Start creating at sitrep.media",
        "tags": "#CreatorEconomy #ContentCreator #Intelligence #TikTok",
    },
    {
        "feature": "Theater Map",
        "hook": "Real-time conflict tracking. Live vessel positions. Strike overlays.",
        "body": "The SitRep Theater Map tracks active conflict zones — Gulf, Ukraine, Taiwan — with live AIS shipping data and kinetic event overlays.",
        "cta": "Go deeper at sitrep.media",
        "tags": "#Geopolitics #Military #OSINT #NationalSecurity",
    },
    {
        "feature": "Zero-Day CVE Monitor",
        "hook": "Every critical vulnerability. Scored. Ranked. Delivered.",
        "body": "SitRep SENTRY monitors CISA KEV and NVD in real time — every CVE scored 0-100 by exploitation status, ransomware use, and infrastructure impact.",
        "cta": "Monitor threats at sitrep.media",
        "tags": "#CyberSecurity #ZeroDay #InfoSec #CISA",
    },
    {
        "feature": "SENTRY AI Analyst",
        "hook": "What is mainstream media not telling you? SENTRY knows.",
        "body": "Ask SENTRY anything. It pulls from live intelligence feeds and delivers structured briefs with threat scoring, cascade analysis, and talking points — in seconds.",
        "cta": "Access SENTRY at sitrep.media",
        "tags": "#AI #Intelligence #OSINT #ThreatAnalysis",
    },
    {
        "feature": "Breaking Alerts",
        "hook": "Missile strike. Zero-day exploit. Naval incident. You hear it first.",
        "body": "SitRep Breaking Alerts push priority A1 events directly to your device — before mainstream media picks it up. Signal tier and above.",
        "cta": "Get alerts at sitrep.media",
        "tags": "#BreakingNews #Intelligence #NationalSecurity #OSINT",
    },
    {
        "feature": "Intelligence Feed",
        "hook": "34 verified sources. Updated every 5 minutes. No noise.",
        "body": "The SitRep Intelligence Feed aggregates CISA KEV, ISW daily updates, OSINT networks, maritime trackers, and financial wires — structured and scored.",
        "cta": "Read the feed at sitrep.media",
        "tags": "#Intelligence #OSINT #CyberSecurity #Geopolitics",
    },
]

def get_marketing_caption(index: int) -> dict:
    post = MARKETING_POSTS[index % len(MARKETING_POSTS)]
    caption = f"{post['hook']}\n\n{post['body']}\n\n{post['cta']}\n\n{post['tags']} #SitRep"
    return {"caption": caption, "feature": post["feature"]}


# ── PUBLISH ENDPOINT ───────────────────────────────────────────────────────────
@publish_router.post("/publish")
async def publish_brief(
    video: UploadFile = File(...),
    brief: str = Form(...),
    caption: str = Form(""),
    autopost: str = Form("false"),
    platforms: str = Form('{"tiktok": true, "linkedin": false, "x": false}'),
):
    """
    Accept video upload from React studio.
    Saves to temp file, triggers post agent.
    """
    try:
        brief_data = json.loads(brief)
        platforms_data = json.loads(platforms)
        force_autopost = autopost.lower() == "true"
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    # Build caption if not provided
    if not caption.strip():
        caption = build_caption(brief_data, platforms_data)

    # Save uploaded video to temp file
    suffix = ".webm" if "webm" in (video.content_type or "") else ".mp4"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await video.read()
        tmp.write(content)
        tmp_path = tmp.name

    brief_id = brief_data.get("brief_id") or f"SR-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    brief_data["brief_id"] = brief_id

    # Store in Redis for tracking
    r = get_redis()
    r.setex(f"sitrep:upload:{brief_id}", 3600, json.dumps({
        "brief_id": brief_id,
        "video_path": tmp_path,
        "caption": caption,
        "platforms": platforms_data,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "status": "processing"
    }))

    # Trigger post agent async
    asyncio.create_task(_run_post(brief_data, tmp_path, caption, platforms_data, force_autopost, brief_id))

    return JSONResponse({
        "status": "processing",
        "brief_id": brief_id,
        "message": "Video received — posting in progress",
        "autopost": force_autopost or AUTOPOST,
        "platforms": platforms_data,
    })


async def _run_post(brief, video_path, caption, platforms, force_autopost, brief_id):
    """Background task — runs post agent."""
    try:
        from sitrep_post_agent import dispatch_post
        import os as _os
        if force_autopost:
            _os.environ["AUTOPOST"] = "true"
        result = await dispatch_post(brief, video_path, caption)
        r = get_redis()
        r.setex(f"sitrep:upload:{brief_id}", 86400, json.dumps({
            **result, "status": result.get("status", "posted")
        }))
    except Exception as e:
        r = get_redis()
        r.setex(f"sitrep:upload:{brief_id}", 3600, json.dumps({
            "brief_id": brief_id, "status": "error", "error": str(e)
        }))


# ── APPROVE ENDPOINT (iPhone tap) ─────────────────────────────────────────────
@publish_router.post("/approve/{brief_id}")
async def approve_post(brief_id: str):
    """Called when Vern taps approve on iPhone FCM notification."""
    try:
        from sitrep_post_agent import approve_and_post
        result = await approve_and_post(brief_id)
        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── STATUS CHECK ───────────────────────────────────────────────────────────────
@publish_router.get("/publish/status/{brief_id}")
async def publish_status(brief_id: str):
    r = get_redis()
    data = r.get(f"sitrep:upload:{brief_id}") or r.get(f"sitrep:posted:{brief_id}")
    if not data:
        return JSONResponse({"status": "not_found", "brief_id": brief_id})
    return JSONResponse(json.loads(data))


# ── HISTORY ────────────────────────────────────────────────────────────────────
@publish_router.get("/publish/history")
async def publish_history():
    r = get_redis()
    keys = list(r.scan_iter("sitrep:posted:*"))[-20:]
    history = []
    for key in keys:
        raw = r.get(key)
        if raw:
            try:
                history.append(json.loads(raw))
            except Exception:
                pass
    history.sort(key=lambda x: x.get("brief_id", ""), reverse=True)
    return JSONResponse({"history": history, "count": len(history)})


# ── MARKETING POST ─────────────────────────────────────────────────────────────
@publish_router.post("/publish/marketing")
async def post_marketing(
    video: UploadFile = File(...),
    index: int = Form(0),
    platforms: str = Form('{"tiktok": true, "linkedin": false, "x": false}'),
):
    """Post a marketing/feature highlight video."""
    platforms_data = json.loads(platforms)
    marketing = get_marketing_caption(index)
    caption = marketing["caption"]
    feature = marketing["feature"]

    suffix = ".webm" if "webm" in (video.content_type or "") else ".mp4"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await video.read())
        tmp_path = tmp.name

    brief_id = f"MKT-{feature.replace(' ', '')}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}"
    brief_data = {
        "brief_id": brief_id,
        "cat": "marketing",
        "title": f"SitRep — {feature}",
        "hook": caption.split('\n')[0],
    }

    asyncio.create_task(_run_post(brief_data, tmp_path, caption, platforms_data, True, brief_id))

    return JSONResponse({
        "status": "processing",
        "brief_id": brief_id,
        "feature": feature,
        "caption_preview": caption[:100] + "..."
    })
