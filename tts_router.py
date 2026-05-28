"""
SitRep TTS Auto-Post Router
POST /api/sitrep/autopost — generate TTS video + post to TikTok
POST /api/sitrep/autopost/preview — generate video only, return download URL
"""
import json
import logging
import os
import tempfile
import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel

tts_router = APIRouter(prefix="/api/sitrep", tags=["autopost"])
log = logging.getLogger("tts_router")

GCS_BUCKET = os.getenv("GCS_BUCKET", "")

class AutoPostRequest(BaseModel):
    brief_id: str = ""
    category: str = "cyber"
    override_brief: dict = {}

async def _run_autopost(brief: dict, platforms: dict):
    """Background task: render TTS video + post."""
    try:
        from tts_avatar_renderer import render_tts_video
        from sitrep_post_agent import dispatch_post

        brief_id = brief.get("brief_id", f"TTS-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
        brief["brief_id"] = brief_id

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            output_path = tmp.name

        log.info(f"[AutoPost] Rendering TTS video for {brief_id}")
        success = render_tts_video(brief, output_path)

        if not success:
            log.error(f"[AutoPost] Render failed for {brief_id}")
            return

        log.info(f"[AutoPost] Video rendered, posting...")

        # Build caption
        hook = brief.get("hook", "")
        cat = brief.get("cat", brief.get("category", "intel"))
        tags = {
            "cyber":    "#CyberSecurity #ZeroDay #ThreatIntel #CISA #SitRep",
            "military": "#Military #OSINT #NationalSecurity #DefenseNews #SitRep",
            "political":"#Politics #Geopolitics #Intelligence #BreakingNews #SitRep",
            "economic": "#Economics #Markets #Geopolitics #Sanctions #SitRep",
            "health":   "#HealthAlert #Outbreak #Pandemic #Biohazard #SitRep",
            "naval":    "#Naval #Maritime #FleetOps #Warship #SitRep",
            "ufo":      "#UAP #UFO #Disclosure #Pentagon #SitRep",
        }.get(cat, "#Intelligence #OSINT #SitRep")
        caption = f"{hook}\n\n{tags}\n\nsitrep.media"

        result = await dispatch_post(brief, output_path, caption)
        log.info(f"[AutoPost] Post result: {result}")

        # Cleanup
        try:
            os.unlink(output_path)
        except:
            pass

    except Exception as e:
        log.error(f"[AutoPost] Error: {e}")
        import traceback
        log.error(traceback.format_exc())


@tts_router.post("/autopost")
async def autopost_brief(req: AutoPostRequest, background_tasks: BackgroundTasks):
    """
    Trigger automated TTS video generation + TikTok post.
    Called by scheduler or manually.
    """
    try:
        # Get brief from Redis or use override
        if req.override_brief:
            brief = req.override_brief
        else:
            import redis as redis_lib
            r = redis_lib.from_url(os.getenv("REDIS_URL",""), decode_responses=True)
            cached = r.get(f"sitrep:brief:{req.category}")
            if not cached:
                # Generate fresh brief
                from sitrep_briefs import get_cached_briefs, generate_all_briefs, cache_briefs
                briefs = get_cached_briefs()
                if not briefs:
                    briefs = generate_all_briefs()
                    if briefs:
                        cache_briefs(briefs)
                brief = next((b for b in (briefs or []) if b.get("cat") == req.category), None)
                if not brief:
                    return JSONResponse({"status":"error","error":f"No brief for category {req.category}"}, status_code=404)
            else:
                brief = json.loads(cached)

        platforms = {'tiktok': True, 'linkedin': False, 'x': False}
        os.environ['AUTOPOST'] = 'true'
        background_tasks.add_task(_run_autopost, brief, platforms)

        return JSONResponse({
            "status": "rendering",
            "brief_id": brief.get("brief_id",""),
            "category": req.category,
            "avatar": brief.get("cat","cyber"),
            "message": "TTS video rendering started — will post when complete"
        })

    except Exception as e:
        log.error(f"[AutoPost] Error: {e}")
        return JSONResponse({"status":"error","error":str(e)}, status_code=500)


@tts_router.post("/autopost/all")
async def autopost_all(background_tasks: BackgroundTasks):
    """Post all 4 categories."""
    results = []
    for cat in ["cyber","military","political","economic"]:
        req = AutoPostRequest(category=cat)
        result = await autopost_brief(req, background_tasks)
        results.append({"category": cat, "status": "queued"})
    return JSONResponse({"status":"ok","queued":results})


@tts_router.get("/autopost/status")
async def autopost_status():
    """Check TTS pipeline status."""
    try:
        from tts_avatar_renderer import CATEGORY_AVATAR, VOICES
        return JSONResponse({
            "status": "ready",
            "avatars": list(CATEGORY_AVATAR.keys()),
            "voices": {k: v["name"] for k,v in VOICES.items()},
        })
    except Exception as e:
        return JSONResponse({"status":"error","error":str(e)})


