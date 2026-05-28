"""
ADD THIS TO sitrep_scheduler.py
Marketing post scheduler — runs twice a week (Mon + Thu)
Triggered by Cloud Scheduler: 0 14 * * 1,4  (2pm UTC Mon/Thu)
Cloud Scheduler endpoint: POST /api/sitrep/schedule/marketing
"""

# Add this import at top of sitrep_scheduler.py:
# from publish_router import get_marketing_caption

MARKETING_VIDEO_STYLES = [
    "creator_studio",
    "theater_map", 
    "cve_monitor",
    "sentry_ai",
    "breaking_alerts",
    "intel_feed",
]

async def generate_marketing_brief(index: int) -> dict:
    """
    Generate a marketing brief using Claude.
    Index rotates through feature highlights.
    """
    from publish_router import get_marketing_caption
    marketing = get_marketing_caption(index)
    
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    feature = marketing["feature"]

    system_prompt = f"""You are SitRep Intelligence marketing AI.
Today is {today}.
Generate a punchy TikTok video script for a SitRep platform feature highlight.
Respond ONLY with valid JSON. No markdown."""

    user_prompt = f"""Generate a 30-second TikTok marketing script for SitRep's "{feature}" feature.

Return this exact JSON:
{{
  "brief_id": "MKT-{feature.replace(' ', '')}-{datetime.now(timezone.utc).strftime('%Y%m%d')}",
  "cat": "marketing",
  "title": "SitRep — {feature}",
  "hook": "Opening line that stops the scroll",
  "script": "Full 30-second teleprompter script. Conversational, punchy, present tense. No asterisks.",
  "caption": "{marketing['caption']}",
  "video_style": "marketing",
  "feature": "{feature}"
}}"""

    try:
        response = ANTHROPIC_CLIENT.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": user_prompt}],
            system=system_prompt,
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        brief = json.loads(raw.strip())
        
        # Cache in Redis
        cache_key = f"sitrep:marketing:{index}"
        r.setex(cache_key, 86400 * 3, json.dumps(brief))
        log.info(f"Marketing brief generated: {feature}")
        return brief
    except Exception as e:
        log.error(f"Marketing brief failed: {e}")
        return {
            "brief_id": f"MKT-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}",
            "cat": "marketing",
            "title": f"SitRep — {feature}",
            "hook": marketing["caption"].split('\n')[0],
            "script": marketing["caption"],
            "caption": marketing["caption"],
            "video_style": "marketing",
            "feature": feature,
        }


async def run_marketing_cycle():
    """
    Called by Cloud Scheduler twice a week (Mon + Thu at 2pm UTC).
    Generates marketing brief + queues for video render + post.
    """
    # Get current marketing index from Redis (rotates through features)
    idx_raw = r.get("sitrep:marketing:index") or "0"
    idx = int(idx_raw)
    
    log.info(f"=== MARKETING CYCLE — Feature index {idx} ===")
    
    brief = await generate_marketing_brief(idx)
    if brief:
        brief["post_status"] = "pending"
        brief["is_marketing"] = True
        r.lpush("sitrep:render_queue", json.dumps(brief))
        
        # Advance index
        r.set("sitrep:marketing:index", str((idx + 1) % 6))
        
        log.info(f"Marketing brief queued: {brief['feature']}")
        return {"status": "queued", "feature": brief["feature"], "id": brief["brief_id"]}
    
    return {"status": "failed"}


# ── ADD THESE FASTAPI ENDPOINTS TO main.py ────────────────────────────────────

"""
@app.post("/api/sitrep/schedule/marketing")
async def trigger_marketing():
    result = await run_marketing_cycle()
    return result

@app.post("/api/sitrep/schedule/cycle") 
async def trigger_cycle():
    result = await run_scheduled_cycle()
    return result

@app.post("/api/sitrep/schedule/breaking")
async def trigger_breaking():
    result = await run_breaking_check()
    return result
"""

# ── CLOUD SCHEDULER CONFIG ────────────────────────────────────────────────────
"""
Add these Cloud Scheduler jobs in GCP Console:

1. SCHEDULED BRIEFS (every 6 hours):
   Name: sitrep-scheduled-cycle
   Schedule: 0 */6 * * *
   Target: POST https://aegis-cwis-xxx.run.app/api/sitrep/schedule/cycle
   Auth: OIDC

2. BREAKING NEWS CHECK (every 15 min):
   Name: sitrep-breaking-check  
   Schedule: */15 * * * *
   Target: POST https://aegis-cwis-xxx.run.app/api/sitrep/schedule/breaking
   Auth: OIDC

3. MARKETING POSTS (Mon + Thu 2pm UTC):
   Name: sitrep-marketing
   Schedule: 0 14 * * 1,4
   Target: POST https://aegis-cwis-xxx.run.app/api/sitrep/schedule/marketing
   Auth: OIDC
"""
