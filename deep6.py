"""
DEEP6 — Real-Time Pattern Analysis Engine
==========================================
Triggers every 5 vault uploads.
Clusters accounts by behavioral signatures.
Preemptively blocks coordinated goon waves.
"""

import os
import re
import json
import asyncio
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from typing import List, Dict, Any
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

deep6_router = APIRouter(prefix="/api/deep6", tags=["deep6"])

CLOUD_RUN = os.getenv("CLOUD_RUN_URL", "https://aegis-cwis-974184310088.us-east1.run.app")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Upload counter — triggers Deep6 every 5 uploads
_upload_counter = {"count": 0, "last_analysis": None, "last_result": None}

# ── PATTERN SIGNATURES ─────────────────────────────────────────────────────────

def score_username(username: str) -> Dict:
    """Score a username for goon indicators."""
    score = 0
    flags = []

    # Numeric bot pattern: user + long number string
    if re.match(r'^user\d{8,}$', username):
        score += 40
        flags.append("BOT_PATTERN: user+numeric")

    # Generic numeric suffix
    if re.search(r'\d{6,}$', username):
        score += 25
        flags.append("NUMERIC_SUFFIX")

    # No profile picture indicator (grey avatar accounts often have these patterns)
    if re.match(r'^user\d+$', username):
        score += 30
        flags.append("DEFAULT_USERNAME")

    # Impersonation patterns
    impersonation_keywords = ['communityguidelines', 'tiktok', 'support', 'official', 'admin', 'moderator', 'staff']
    for kw in impersonation_keywords:
        if kw in username.lower():
            score += 80
            flags.append(f"IMPERSONATION: {kw}")

    # Known hostile handle patterns
    hostile_patterns = ['goon', 'troll', 'hate', 'ban', 'report', 'fake']
    for p in hostile_patterns:
        if p in username.lower():
            score += 35
            flags.append(f"HOSTILE_KEYWORD: {p}")

    # Short random string (throwaway accounts)
    if len(username) <= 6 and re.match(r'^[a-z0-9]+$', username):
        score += 20
        flags.append("SHORT_RANDOM")

    return {"score": min(score, 100), "flags": flags}


def detect_timing_clusters(blocks: List[Dict]) -> List[Dict]:
    """Find accounts blocked within tight time windows — coordinated entry."""
    if len(blocks) < 3:
        return []

    clusters = []
    window_seconds = 120  # 2 minute window

    for i, block in enumerate(blocks):
        try:
            t1 = datetime.fromisoformat(block['time'].replace('Z', '+00:00'))
        except:
            continue

        cluster = [block]
        for j, other in enumerate(blocks):
            if i == j:
                continue
            try:
                t2 = datetime.fromisoformat(other['time'].replace('Z', '+00:00'))
                if abs((t1 - t2).total_seconds()) <= window_seconds:
                    cluster.append(other)
            except:
                continue

        if len(cluster) >= 3:
            cluster_usernames = list(set([b['username'] for b in cluster]))
            if len(cluster_usernames) >= 3:
                clusters.append({
                    "anchor_time": block['time'],
                    "accounts": cluster_usernames,
                    "count": len(cluster_usernames),
                    "threat_level": "CRITICAL" if len(cluster_usernames) >= 5 else "HIGH"
                })

    # Deduplicate clusters
    seen = set()
    unique = []
    for c in clusters:
        key = frozenset(c['accounts'])
        if key not in seen:
            seen.add(key)
            unique.append(c)

    return unique


def detect_repeat_offenders(blocks: List[Dict]) -> List[str]:
    """Find accounts that appear multiple times."""
    counts = defaultdict(int)
    for b in blocks:
        counts[b['username']] += 1
    return [u for u, c in counts.items() if c >= 2]


def detect_bot_cluster(usernames: List[str]) -> Dict:
    """Find clusters of bot-pattern accounts."""
    bot_accounts = []
    for u in usernames:
        result = score_username(u)
        if result['score'] >= 30:
            bot_accounts.append({
                "username": u,
                "score": result['score'],
                "flags": result['flags']
            })

    bot_accounts.sort(key=lambda x: x['score'], reverse=True)
    return {
        "bot_accounts": bot_accounts,
        "count": len(bot_accounts),
        "high_confidence": [b for b in bot_accounts if b['score'] >= 60]
    }


async def run_deep6_analysis(blocks: List[Dict], vault: List[str]) -> Dict:
    """Full Deep6 hive mind pattern analysis."""
    analysis = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "accounts_analyzed": len(vault),
        "recent_blocks_analyzed": len(blocks),
        "findings": {}
    }

    # 1. Timing cluster analysis
    timing_clusters = detect_timing_clusters(blocks)
    analysis["findings"]["timing_clusters"] = {
        "detected": len(timing_clusters),
        "clusters": timing_clusters[:5],  # Top 5
        "assessment": "COORDINATED ATTACK DETECTED" if timing_clusters else "No timing coordination detected"
    }

    # 2. Repeat offenders
    repeat = detect_repeat_offenders(blocks)
    analysis["findings"]["repeat_offenders"] = {
        "accounts": repeat,
        "count": len(repeat),
        "assessment": f"{len(repeat)} accounts blocked multiple times — high persistence"
    }

    # 3. Bot pattern detection
    bot_analysis = detect_bot_cluster(vault[-50:])  # Analyze last 50 added
    analysis["findings"]["bot_patterns"] = {
        "suspicious_accounts": len(bot_analysis['bot_accounts']),
        "high_confidence_bots": len(bot_analysis['high_confidence']),
        "top_suspects": bot_analysis['high_confidence'][:10],
        "assessment": f"{len(bot_analysis['high_confidence'])} accounts match bot/throwaway patterns"
    }

    # 4. Impersonation detection
    impersonators = [u for u in vault if any(kw in u.lower() for kw in ['communityguidelines', 'tiktok', 'official', 'support'])]
    analysis["findings"]["impersonation"] = {
        "accounts": impersonators,
        "count": len(impersonators),
        "assessment": "CRITICAL: TikTok impersonation accounts detected" if impersonators else "No impersonation detected"
    }

    # 5. AI hive mind summary
    if ANTHROPIC_API_KEY and len(blocks) > 0:
        try:
            prompt = f"""You are Deep6, an AI threat intelligence analyst for AEGIS PHANTOM.

Analyze this harassment pattern data from a TikTok live stream defense operation:

Recent blocks ({len(blocks)} accounts):
{json.dumps([b['username'] for b in blocks[:20]], indent=2)}

Timing clusters detected: {len(timing_clusters)}
Repeat offenders: {repeat}
Bot-pattern accounts: {len(bot_analysis['high_confidence'])}
Impersonators: {impersonators}

Provide:
1. THREAT ASSESSMENT (1 sentence)
2. ATTACK PATTERN (what type of coordinated attack is this?)
3. HIVE MIND ANALYSIS (are these accounts connected? How?)
4. PREEMPTIVE RECOMMENDATIONS (3 specific actions to block the next wave)
5. RISK LEVEL: CRITICAL / HIGH / MEDIUM / LOW

Be direct and tactical. This is real-time threat intelligence."""

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json"
                    },
                    json={
                        "model": "claude-sonnet-4-20250514",
                        "max_tokens": 500,
                        "messages": [{"role": "user", "content": prompt}]
                    }
                )
                data = resp.json()
                if data.get("content"):
                    analysis["ai_assessment"] = data["content"][0]["text"]
        except Exception as e:
            analysis["ai_assessment"] = f"AI analysis unavailable: {str(e)}"

    _upload_counter["last_result"] = analysis
    _upload_counter["last_analysis"] = datetime.now(timezone.utc).isoformat()
    return analysis


# ── API ENDPOINTS ──────────────────────────────────────────────────────────────

@deep6_router.post("/trigger")
async def trigger_analysis(request: Request):
    """Manually trigger Deep6 analysis."""
    try:
        # Get recent blocks from evidence
        async with httpx.AsyncClient(timeout=15) as client:
            # Get auth token for internal call
            token_resp = await client.post(
                f"{CLOUD_RUN}/api/auth/login",
                json={"username": "vern", "password": "aegis2026vern"}
            )
            token = token_resp.json().get("token", "")

            # Get evidence/blocks
            ev_resp = await client.get(
                f"{CLOUD_RUN}/api/evidence",
                headers={"Authorization": f"Bearer {token}"}
            )
            blocks = ev_resp.json().get("blocks", [])

            # Get vault
            vault_resp = await client.get(
                f"{CLOUD_RUN}/api/goons",
                headers={"Authorization": f"Bearer {token}"}
            )
            vault_data = vault_resp.json()
            vault = vault_data if isinstance(vault_data, list) else vault_data.get("goons", [])

        result = await run_deep6_analysis(blocks, vault)
        return JSONResponse({"status": "analysis_complete", "result": result})

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@deep6_router.get("/status")
async def deep6_status():
    """Get Deep6 status and last analysis."""
    return JSONResponse({
        "upload_count": _upload_counter["count"],
        "next_trigger_in": max(0, 5 - (_upload_counter["count"] % 5)),
        "last_analysis": _upload_counter["last_analysis"],
        "last_result_summary": {
            "timing_clusters": _upload_counter["last_result"]["findings"]["timing_clusters"]["detected"] if _upload_counter["last_result"] else 0,
            "repeat_offenders": _upload_counter["last_result"]["findings"]["repeat_offenders"]["count"] if _upload_counter["last_result"] else 0,
            "bot_patterns": _upload_counter["last_result"]["findings"]["bot_patterns"]["high_confidence_bots"] if _upload_counter["last_result"] else 0,
        } if _upload_counter["last_result"] else None
    })


@deep6_router.get("/last")
async def get_last_analysis():
    """Get the full last Deep6 analysis report."""
    if not _upload_counter["last_result"]:
        return JSONResponse({"message": "No analysis run yet. POST to /api/deep6/trigger to run now."})
    return JSONResponse(_upload_counter["last_result"])


def increment_and_check(count_to_add: int = 1):
    """Call this after every vault upload. Returns True if Deep6 should trigger."""
    _upload_counter["count"] += count_to_add
    return _upload_counter["count"] % 5 == 0
