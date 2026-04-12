"""
AEGIS SENTRY — Creator Mode
============================
Generates creator_brief objects from threat alerts and zero-day CVEs.
Free tier: snippet only (hook + 1 talking point).
Subscription tier: full brief (script + all talking points + what media missed).

Add to main.py:
    from creator_mode import creator_router
    app.include_router(creator_router)
"""

import os
import json
import asyncio
from datetime import datetime, timezone
import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

creator_router = APIRouter(prefix="/api/creator", tags=["creator"])

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ── SUBSCRIPTION CHECK ────────────────────────────────────────────────────────
# Simple tier check — expand with Firebase Auth + Stripe webhook later
# For now: pass ?tier=subscription in request for full brief (demo mode)
# Production: verify Firebase ID token and check subscription status in Firestore

def get_user_tier(request: Request) -> str:
    """
    Check user subscription tier.
    Demo: reads ?tier= query param.
    Production: verify Firebase JWT and check Firestore subscription record.
    """
    tier = request.query_params.get("tier", "free")
    return tier if tier in ["free", "subscription", "institutional"] else "free"


# ── BRIEF GENERATION ──────────────────────────────────────────────────────────
async def generate_creator_brief(alert: dict, full: bool = False) -> dict:
    """
    Generate a creator_brief from a threat alert or zero-day CVE.
    full=True returns complete brief for subscribers.
    full=False returns snippet only for free tier.
    """
    if not ANTHROPIC_API_KEY:
        return {"error": "AI engine not configured"}

    alert_context = json.dumps(alert, indent=2)

    if full:
        prompt = f"""You are a cybersecurity intelligence analyst who specializes in translating 
complex threat intelligence into compelling content for creators, journalists, and media professionals.

Given this threat alert or vulnerability:

{alert_context}

Generate a creator brief in this EXACT JSON format — no preamble, no markdown, just the JSON object:

{{
  "hook": "One punchy opening sentence for a video or post. Should create urgency and curiosity. Plain language, no jargon.",
  "what_media_missed": "2-3 sentences explaining the angle mainstream media overlooked. Why does this matter beyond the patch notice? What is the real-world impact most people don't understand?",
  "talking_points": [
    "First talking point — plain language, specific fact or implication",
    "Second talking point — connect to something the audience cares about (prices, safety, privacy)",
    "Third talking point — what should people actually do or watch for",
    "Fourth talking point — the bigger picture geopolitical or economic angle",
    "Fifth talking point — the question this raises that hasn't been answered yet"
  ],
  "script_60": "A complete 60-second script the creator can read verbatim. Conversational tone. Opens with the hook. Explains what happened in plain language. Connects to real-world impact. Ends with a clear call to action or question for the audience.",
  "discussion_questions": [
    "Question 1 — for a live discussion or interview format",
    "Question 2 — provocative, audience will want the answer",
    "Question 3 — connects threat to everyday life"
  ],
  "citation": "How to source this: mention CISA KEV catalog, NVD, or the specific vendor advisory. One sentence.",
  "urgency": "immediate|this_week|this_month",
  "audience_relevance": "Who specifically should care about this — be specific about the audience segment",
  "energy_connection": "If this threat connects to energy prices, fuel costs, supply chain, or economic impact — explain that connection in one sentence. If no connection, return null."
}}"""
    else:
        prompt = f"""You are a cybersecurity intelligence analyst translating threat intelligence for content creators.

Given this threat alert or vulnerability:

{alert_context}

Generate ONLY the free snippet in this EXACT JSON format — no preamble, no markdown, just the JSON:

{{
  "hook": "One punchy opening sentence for a video or post. Plain language, creates urgency and curiosity.",
  "teaser_point": "One compelling talking point that makes the creator want the full brief. Hint at the bigger story without giving it away.",
  "urgency": "immediate|this_week|this_month",
  "preview_locked": ["What media actually missed about this", "The 60-second script", "4 more talking points", "Discussion questions for live shows", "Citation package"]
}}"""

    headers = {
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01"
    }
    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1500 if full else 400,
        "messages": [{"role": "user", "content": prompt}]
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                json=payload,
                headers=headers
            )
            resp.raise_for_status()
            data = resp.json()
            text = data["content"][0]["text"].strip()

            # Strip markdown fences if present
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1])

            brief = json.loads(text)
            brief["generated_at"] = datetime.now(timezone.utc).isoformat()
            brief["alert_id"] = alert.get("cve_id") or alert.get("alert_id", "unknown")
            brief["tier"] = "subscription" if full else "free"
            return brief

    except json.JSONDecodeError as e:
        return {"error": f"Brief parse error: {str(e)}", "raw": text if 'text' in dir() else ""}
    except Exception as e:
        return {"error": str(e)}


# ── ENDPOINTS ─────────────────────────────────────────────────────────────────

@creator_router.post("/brief")
async def create_brief(request: Request):
    """
    Generate a creator brief from a provided alert object.
    Free tier: hook + teaser only.
    Subscription: full brief with script, talking points, discussion questions.

    Body: { "alert": { ...threat_alert or vuln_alert object... } }
    Query: ?tier=free|subscription
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body")

    alert = body.get("alert")
    if not alert:
        raise HTTPException(status_code=400, detail="alert object required")

    tier = get_user_tier(request)
    full = tier in ["subscription", "institutional"]

    brief = await generate_creator_brief(alert, full=full)

    if "error" in brief:
        raise HTTPException(status_code=502, detail=brief["error"])

    return JSONResponse(content=brief)


@creator_router.get("/brief/{cve_id}")
async def get_brief_for_cve(cve_id: str, request: Request):
    """
    Generate a creator brief for a specific CVE from the zero-day feed.
    Looks up the CVE from Redis, generates brief based on tier.
    """
    import redis as redis_lib
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")

    try:
        rc = redis_lib.from_url(redis_url, decode_responses=True, socket_connect_timeout=5)
        raw = rc.get(f"aegis:zeroday:{cve_id}")
        if not raw:
            raise HTTPException(status_code=404, detail=f"{cve_id} not found in zero-day feed")
        alert = json.loads(raw)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Redis error: {str(e)}")

    tier = get_user_tier(request)
    full = tier in ["subscription", "institutional"]
    brief = await generate_creator_brief(alert, full=full)

    if "error" in brief:
        raise HTTPException(status_code=502, detail=brief["error"])

    return JSONResponse(content=brief)


@creator_router.get("/feed")
async def creator_feed(request: Request, limit: int = 10, min_score: int = 60):
    """
    Return the top zero-day alerts formatted for the creator feed.
    Free tier: snippet previews only.
    Subscription: full briefs (generated on-demand per item).

    For performance, free feed returns alert metadata only — briefs generated on click.
    """
    import redis as redis_lib
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")

    try:
        rc = redis_lib.from_url(redis_url, decode_responses=True, socket_connect_timeout=5)
        index_raw = rc.get("aegis:zeroday:index")
        if not index_raw:
            return {"items": [], "count": 0, "tier": get_user_tier(request)}

        cve_ids = json.loads(index_raw)
        items = []

        for cve_id in cve_ids:
            raw = rc.get(f"aegis:zeroday:{cve_id}")
            if raw:
                try:
                    alert = json.loads(raw)
                    if alert.get("priority_score", 0) >= min_score:
                        items.append({
                            "cve_id": alert.get("cve_id"),
                            "title": alert.get("title"),
                            "vendor": alert.get("vendor"),
                            "product": alert.get("product"),
                            "priority_score": alert.get("priority_score"),
                            "kev_listed": alert.get("kev_listed"),
                            "ransomware_use": alert.get("ransomware_use"),
                            "exploitation_status": alert.get("exploitation_status"),
                            "kev_date_added": alert.get("kev_date_added"),
                            "brief_url": f"/api/creator/brief/{alert.get('cve_id')}",
                            "locked": get_user_tier(request) == "free"
                        })
                except Exception:
                    continue

        items.sort(key=lambda x: x.get("priority_score", 0), reverse=True)

        return {
            "items": items[:limit],
            "count": len(items[:limit]),
            "total_available": len(items),
            "tier": get_user_tier(request),
            "upgrade_url": "https://cybergrid.web.app/#pricing",
            "last_refresh": rc.get("aegis:zeroday:last_refresh")
        }

    except Exception as e:
        return {"items": [], "count": 0, "error": str(e)}


@creator_router.get("/topics")
async def creator_topics(request: Request):
    """
    Return high-level topic clusters for content planning.
    Free: topic names and count only.
    Subscription: topic clusters with top CVEs per cluster.
    """
    import redis as redis_lib
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")

    topics = {
        "remote_access": {"name": "Remote Access & VPN Vulnerabilities", "cves": [], "count": 0},
        "authentication": {"name": "Authentication Bypass & Credential Theft", "cves": [], "count": 0},
        "code_execution": {"name": "Remote Code Execution", "cves": [], "count": 0},
        "network_infra": {"name": "Network Infrastructure & Firewall", "cves": [], "count": 0},
        "ransomware": {"name": "Ransomware-Linked Vulnerabilities", "cves": [], "count": 0},
        "energy_industrial": {"name": "Energy & Industrial Control Systems", "cves": [], "count": 0},
    }

    keywords = {
        "remote_access": ["remote support", "vpn", "remote access", "rdp", "citrix", "ivanti"],
        "authentication": ["authentication bypass", "auth bypass", "missing authentication", "improper authentication"],
        "code_execution": ["code execution", "rce", "execute", "arbitrary code", "command injection"],
        "network_infra": ["firewall", "router", "network", "cisco", "fortinet", "palo alto", "juniper"],
        "ransomware": [],  # Uses ransomware_use flag
        "energy_industrial": ["scada", "ics", "plc", "industrial", "energy", "water", "ot/ics"],
    }

    try:
        rc = redis_lib.from_url(redis_url, decode_responses=True, socket_connect_timeout=5)
        index_raw = rc.get("aegis:zeroday:index")
        if not index_raw:
            return {"topics": list(topics.values()), "tier": get_user_tier(request)}

        cve_ids = json.loads(index_raw)
        tier = get_user_tier(request)

        for cve_id in cve_ids:
            raw = rc.get(f"aegis:zeroday:{cve_id}")
            if not raw:
                continue
            try:
                alert = json.loads(raw)
                desc = (alert.get("description", "") + " " + alert.get("title", "")).lower()

                if alert.get("ransomware_use"):
                    topics["ransomware"]["count"] += 1
                    if tier != "free" and len(topics["ransomware"]["cves"]) < 3:
                        topics["ransomware"]["cves"].append(alert.get("cve_id"))

                for topic, kws in keywords.items():
                    if topic == "ransomware":
                        continue
                    if any(kw in desc for kw in kws):
                        topics[topic]["count"] += 1
                        if tier != "free" and len(topics[topic]["cves"]) < 3:
                            topics[topic]["cves"].append(alert.get("cve_id"))
            except Exception:
                continue

        # Free tier: hide CVE lists
        if tier == "free":
            for t in topics.values():
                t["cves"] = []
                t["locked"] = True

        return {
            "topics": list(topics.values()),
            "tier": tier,
            "total_alerts": len(cve_ids),
            "upgrade_url": "https://cybergrid.web.app/#pricing"
        }

    except Exception as e:
        return {"topics": [], "error": str(e)}
