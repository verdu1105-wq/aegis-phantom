"""
AEGIS Video Intel Router
Article → Gemini Prompt Engineering → Veo 3 Video → GCS → Queue for posting
"""

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException
from google.cloud import firestore, storage
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/video", tags=["video-intel"])

# ── env ──────────────────────────────────────────────────────────────────────
GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY", "")
GCS_BUCKET       = os.getenv("GCS_BUCKET", "cybergrid-media")
GCP_PROJECT      = os.getenv("GCP_PROJECT", "cybergrid")
GCP_REGION       = os.getenv("GCP_REGION", "us-central1")

SITREP_CTA = (
    "End with bold white text on black: 'SITREP MEDIA — "
    "The intelligence mainstream media won't cover.' "
    "with the SitRep satellite logo watermark bottom-right corner throughout."
)

PLATFORM_SPECS = {
    "tiktok":   {"duration": 30, "aspect": "9:16", "style": "fast cuts, high energy"},
    "youtube":  {"duration": 60, "aspect": "16:9", "style": "cinematic, detailed"},
    "substack": {"duration": 45, "aspect": "16:9", "style": "documentary, analytical"},
    "instagram":{"duration": 30, "aspect": "1:1",  "style": "punchy, visual"},
}

db  = firestore.Client(project=GCP_PROJECT)
gcs = storage.Client(project=GCP_PROJECT)


# ── request / response models ─────────────────────────────────────────────────
class VideoGenRequest(BaseModel):
    article_title:   str
    article_body:    str
    article_url:     Optional[str] = None
    platforms:       list[str] = ["tiktok", "youtube"]
    priority:        str = "normal"          # normal | urgent | breaking


class VideoJob(BaseModel):
    job_id:     str
    status:     str
    platforms:  list[str]
    created_at: str
    prompts:    Optional[dict] = None


# ── prompt engineering ─────────────────────────────────────────────────────────
async def engineer_video_prompts(title: str, body: str, platforms: list[str]) -> dict:
    """
    Use Gemini Flash to extract intel context and generate
    platform-optimized Veo 3 prompts from the article.
    """
    system_prompt = """You are a military/defense media video director for SitRep Media.
    
    Given a news article, generate Veo 3 video prompts that:
    - Use tactical/military aesthetic: isometric strike maps, HUD overlays, radar sweeps,
      missile trajectory arcs, satellite imagery, night vision green tones, FLIR thermal
    - Include target callout boxes, objective text overlays (military stencil font)
    - Show relevant geography: actual region being discussed
    - Animate the story visually (not just static images)
    - Are optimized per platform duration and style
    
    Return ONLY valid JSON, no markdown, no explanation:
    {
      "intel_summary": "2-sentence distilled intel for caption/description",
      "key_targets": ["target1", "target2"],
      "region": "geographic region name",
      "conflict_type": "air_strike|naval|cyber|ground|missile",
      "prompts": {
        "tiktok": "...",
        "youtube": "...",
        "substack": "...",
        "instagram": "..."
      }
    }"""

    user_msg = f"ARTICLE TITLE: {title}\n\nARTICLE BODY:\n{body[:3000]}"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent",
            params={"key": GEMINI_API_KEY},
            json={
                "system_instruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"parts": [{"text": user_msg}]}],
                "generationConfig": {"temperature": 0.7, "maxOutputTokens": 2048}
            }
        )
        resp.raise_for_status()
        raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        # strip any accidental markdown fences
        clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        data  = json.loads(clean)

    # inject platform specs + SitRep CTA into each prompt
    for platform in platforms:
        if platform in data["prompts"] and platform in PLATFORM_SPECS:
            spec = PLATFORM_SPECS[platform]
            data["prompts"][platform] = (
                f"{data['prompts'][platform]} "
                f"Duration: {spec['duration']} seconds. "
                f"Aspect ratio: {spec['aspect']}. "
                f"Style: {spec['style']}. "
                f"{SITREP_CTA}"
            )

    return data


# ── Veo 3 video generation ─────────────────────────────────────────────────────
async def generate_veo_video(prompt: str, platform: str, job_id: str) -> Optional[str]:
    """
    Submit to Veo 3 via Vertex AI, poll until complete, upload to GCS.
    Returns GCS URI or None on failure.
    """
    spec       = PLATFORM_SPECS.get(platform, PLATFORM_SPECS["youtube"])
    vertex_url = (
        f"https://{GCP_REGION}-aiplatform.googleapis.com/v1/"
        f"projects/{GCP_PROJECT}/locations/{GCP_REGION}/publishers/google/models/veo-3.0-generate-preview:predictLongRunning"
    )

    payload = {
        "instances": [{
            "prompt": prompt,
        }],
        "parameters": {
            "aspectRatio":        spec["aspect"],
            "durationSeconds":    spec["duration"],
            "enhancePrompt":      True,
            "generateAudio":      True,
            "storageUri":         f"gs://{GCS_BUCKET}/video_intel/{job_id}/",
        }
    }

    # get access token via metadata server (Cloud Run native auth)
    async with httpx.AsyncClient(timeout=10) as client:
        token_resp = await client.get(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"}
        )
        access_token = token_resp.json()["access_token"]

    async with httpx.AsyncClient(timeout=60) as client:
        submit = await client.post(
            vertex_url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type":  "application/json",
            },
            json=payload
        )
        if submit.status_code not in (200, 202):
            logger.error(f"Veo submit error {submit.status_code}: {submit.text}")
            return None

        operation_name = submit.json().get("name", "")
        if not operation_name:
            logger.error("No operation name returned from Veo")
            return None

    # poll operation until done (max 10 min)
    poll_url = f"https://{GCP_REGION}-aiplatform.googleapis.com/v1/{operation_name}"
    for attempt in range(60):          # 60 × 10s = 10 min
        await asyncio.sleep(10)
        async with httpx.AsyncClient(timeout=15) as client:
            poll = await client.get(
                poll_url,
                headers={"Authorization": f"Bearer {access_token}"}
            )
            result = poll.json()
            if result.get("done"):
                if "error" in result:
                    logger.error(f"Veo error: {result['error']}")
                    return None
                # extract GCS URI from response
                videos = (result.get("response", {})
                               .get("predictions", [{}])[0]
                               .get("bytesBase64Encoded"))  # or gcsUri depending on API version
                gcs_uri = (result.get("response", {})
                                 .get("predictions", [{}])[0]
                                 .get("gcsUri", ""))
                if gcs_uri:
                    return gcs_uri
                logger.error(f"Veo done but no gcsUri: {result}")
                return None

    logger.error(f"Veo timed out for job {job_id} platform {platform}")
    return None


# ── Firestore job tracking ────────────────────────────────────────────────────
def update_job(job_id: str, updates: dict):
    db.collection("video_jobs").document(job_id).set(updates, merge=True)


def get_job(job_id: str) -> Optional[dict]:
    doc = db.collection("video_jobs").document(job_id).get()
    return doc.to_dict() if doc.exists else None


# ── background pipeline ───────────────────────────────────────────────────────
async def run_video_pipeline(
    job_id:  str,
    title:   str,
    body:    str,
    url:     Optional[str],
    platforms: list[str]
):
    try:
        update_job(job_id, {"status": "engineering_prompt", "updated_at": datetime.now(timezone.utc).isoformat()})

        # Step 1: Engineer prompts
        intel = await engineer_video_prompts(title, body, platforms)
        update_job(job_id, {
            "status":        "generating_video",
            "intel_summary": intel.get("intel_summary"),
            "key_targets":   intel.get("key_targets"),
            "region":        intel.get("region"),
            "prompts":       intel.get("prompts"),
            "updated_at":    datetime.now(timezone.utc).isoformat()
        })

        # Step 2: Generate videos per platform (parallel)
        tasks = {
            platform: generate_veo_video(intel["prompts"][platform], platform, job_id)
            for platform in platforms
            if platform in intel.get("prompts", {})
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        gcs_uris = dict(zip(tasks.keys(), results))

        # Step 3: Queue for posting
        post_queue = []
        for platform, uri in gcs_uris.items():
            if isinstance(uri, str) and uri.startswith("gs://"):
                post_queue.append({
                    "platform":      platform,
                    "gcs_uri":       uri,
                    "status":        "queued",
                    "intel_summary": intel.get("intel_summary", ""),
                    "article_url":   url,
                    "cta":           "Get the intel mainstream media won't cover → sitrep.media",
                    "queued_at":     datetime.now(timezone.utc).isoformat()
                })
                # write to post_queue collection for the posting agent to pick up
                db.collection("post_queue").add({
                    "job_id":   job_id,
                    "platform": platform,
                    "gcs_uri":  uri,
                    "status":   "pending",
                    "title":    title,
                    "summary":  intel.get("intel_summary", ""),
                    "cta":      "Get the intel mainstream media won't cover → sitrep.media",
                    "article_url": url,
                    "created_at":  datetime.now(timezone.utc).isoformat()
                })

        update_job(job_id, {
            "status":     "queued_for_posting",
            "gcs_uris":   {p: u for p, u in gcs_uris.items() if isinstance(u, str)},
            "post_queue": post_queue,
            "updated_at": datetime.now(timezone.utc).isoformat()
        })

        logger.info(f"Video pipeline complete for job {job_id}: {len(post_queue)} videos queued")

    except Exception as e:
        logger.exception(f"Video pipeline failed for job {job_id}: {e}")
        update_job(job_id, {
            "status":     "failed",
            "error":      str(e),
            "updated_at": datetime.now(timezone.utc).isoformat()
        })


# ── routes ────────────────────────────────────────────────────────────────────
@router.post("/generate", response_model=VideoJob)
async def generate_video(req: VideoGenRequest, background_tasks: BackgroundTasks):
    """
    Kick off article → video pipeline.
    Returns job_id immediately; pipeline runs in background.
    """
    job_id = f"vid_{uuid.uuid4().hex[:12]}"
    now    = datetime.now(timezone.utc).isoformat()

    job_doc = {
        "job_id":        job_id,
        "status":        "queued",
        "platforms":     req.platforms,
        "article_title": req.article_title,
        "article_url":   req.article_url,
        "priority":      req.priority,
        "created_at":    now,
        "updated_at":    now,
    }
    db.collection("video_jobs").document(job_id).set(job_doc)

    background_tasks.add_task(
        run_video_pipeline,
        job_id, req.article_title, req.article_body, req.article_url, req.platforms
    )

    return VideoJob(
        job_id=job_id,
        status="queued",
        platforms=req.platforms,
        created_at=now
    )


@router.get("/job/{job_id}")
async def get_video_job(job_id: str):
    """Poll job status."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/jobs")
async def list_video_jobs(limit: int = 20):
    """List recent video jobs."""
    docs = (
        db.collection("video_jobs")
          .order_by("created_at", direction=firestore.Query.DESCENDING)
          .limit(limit)
          .stream()
    )
    return [d.to_dict() for d in docs]


@router.get("/queue")
async def get_post_queue(status: str = "pending"):
    """View current post queue."""
    docs = (
        db.collection("post_queue")
          .where("status", "==", status)
          .order_by("created_at", direction=firestore.Query.DESCENDING)
          .limit(50)
          .stream()
    )
    return [d.to_dict() for d in docs]


@router.post("/queue/{doc_id}/approve")
async def approve_post(doc_id: str):
    """Manually approve a queued video for posting."""
    db.collection("post_queue").document(doc_id).update({"status": "approved"})
    return {"approved": doc_id}


@router.post("/queue/{doc_id}/reject")
async def reject_post(doc_id: str):
    """Reject / remove from queue."""
    db.collection("post_queue").document(doc_id).update({"status": "rejected"})
    return {"rejected": doc_id}
