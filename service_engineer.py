"""
SitRep Service Engineer Agent — Powered by Gemini
Monitors GCP infrastructure, AEGIS health, and auto-fixes common issues.
Run on a schedule or trigger manually via:
  POST /api/sitrep/service-engineer/run
  GET  /api/sitrep/service-engineer/status
  GET  /api/sitrep/service-engineer/report
"""
import os
import json
import time
import httpx
import asyncio
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter
from fastapi.responses import JSONResponse

svc_router = APIRouter(prefix="/api/sitrep/service-engineer", tags=["service-engineer"])

AEGIS_URL    = os.getenv("AEGIS_CLOUD_URL", "https://aegis-cwis-974184310088.us-east1.run.app")
GCP_PROJECT  = os.getenv("GCP_PROJECT", "cybergrid")
GCS_BUCKET   = "cybergrid-sitrep-videos"
COLLECTION   = "aegis_service_reports"

# ── HEALTH CHECKS ─────────────────────────────────────────────────────────────

async def check_aegis_health() -> dict:
    """Check AEGIS API is responding."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{AEGIS_URL}/api/sitrep/health")
            return {"status": "ok", "code": r.status_code, "response_ms": r.elapsed.total_seconds() * 1000}
    except Exception as e:
        return {"status": "error", "error": str(e)}


async def check_briefs_pipeline() -> dict:
    """Check if AEGIS is generating briefs."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{AEGIS_URL}/api/sitrep/briefs")
            data = r.json()
            count = data.get("count", 0)
            cached = data.get("cached", False)
            status = "ok" if count > 0 else "warning"
            return {"status": status, "brief_count": count, "cached": cached}
    except Exception as e:
        return {"status": "error", "error": str(e)}


async def check_tiktok_token() -> dict:
    """Check TikTok token status."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{AEGIS_URL}/api/token/status")
            data = r.json()
            updated = data.get("updated_at", "")
            expires = data.get("expires_in", 86400)
            
            if updated:
                updated_dt = datetime.fromisoformat(updated.replace('Z', '+00:00')).replace(tzinfo=timezone.utc) if '+' not in updated else datetime.fromisoformat(updated)
                age_seconds = (datetime.now(timezone.utc) - updated_dt).total_seconds()
                remaining = expires - age_seconds
                
                if remaining < 3600:
                    return {"status": "critical", "message": "Token expires in < 1 hour", "remaining_seconds": remaining}
                elif remaining < 21600:
                    return {"status": "warning", "message": "Token expires in < 6 hours", "remaining_seconds": remaining}
                else:
                    return {"status": "ok", "remaining_seconds": remaining, "updated_at": updated}
            return {"status": "warning", "message": "No token found"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


async def check_gcs_storage() -> dict:
    """Check GCS bucket accessibility."""
    try:
        from google.cloud import storage
        gcs = storage.Client(project=GCP_PROJECT)
        bucket = gcs.bucket(GCS_BUCKET)
        blobs = list(bucket.list_blobs(prefix="briefs/", max_results=5))
        video_count = len(blobs)
        return {"status": "ok", "recent_videos": video_count, "bucket": GCS_BUCKET}
    except Exception as e:
        return {"status": "error", "error": str(e)}


async def check_asset_cache() -> dict:
    """Check Firestore asset image cache."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{AEGIS_URL}/api/sitrep/asset-cache/library")
            data = r.json()
            count = data.get("count", 0)
            return {"status": "ok", "cached_assets": count}
    except Exception as e:
        return {"status": "error", "error": str(e)}


async def check_stripe() -> dict:
    """Check Stripe checkout endpoint."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{AEGIS_URL}/api/stripe/checkout",
                json={"tier": "brief", "email": "healthcheck@sitrep.media"},
                headers={"Content-Type": "application/json"}
            )
            data = r.json()
            if "url" in data:
                return {"status": "ok", "checkout": "functional"}
            return {"status": "warning", "message": data.get("error", "No checkout URL")}
    except Exception as e:
        return {"status": "error", "error": str(e)}


async def check_firebase_hosting() -> dict:
    """Check sitrep.media is serving."""
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            r = await client.get("https://sitrep.media")
            return {"status": "ok", "code": r.status_code, "size_bytes": len(r.content)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── AUTO-FIX ACTIONS ──────────────────────────────────────────────────────────

async def auto_refresh_tiktok_token() -> dict:
    """Auto-refresh TikTok token if expiring."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{AEGIS_URL}/api/token/refresh")
            data = r.json()
            return {"action": "tiktok_token_refresh", "result": data}
    except Exception as e:
        return {"action": "tiktok_token_refresh", "error": str(e)}


async def auto_trigger_brief_cycle() -> dict:
    """Auto-trigger brief generation if count is low."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(f"{AEGIS_URL}/api/sitrep/scheduled-cycle")
            data = r.json()
            return {"action": "brief_cycle_triggered", "result": data}
    except Exception as e:
        return {"action": "brief_cycle_triggered", "error": str(e)}


# ── GEMINI ANALYSIS ───────────────────────────────────────────────────────────

async def analyze_with_gemini(health_report: dict) -> str:
    """Use Gemini/Claude to analyze health report and recommend actions."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
        
        prompt = f"""You are a senior SRE (Site Reliability Engineer) for SitRep Intelligence platform.

Analyze this infrastructure health report and provide:
1. Overall health score (0-100)
2. Critical issues requiring immediate action
3. Warnings to monitor
4. Recommended auto-fixes
5. A one-line executive summary

Health Report:
{json.dumps(health_report, indent=2)}

Respond in JSON format:
{{
  "health_score": 85,
  "status": "healthy|degraded|critical",
  "critical_issues": ["issue1"],
  "warnings": ["warning1"],
  "auto_fixes_applied": ["fix1"],
  "recommended_actions": ["action1"],
  "executive_summary": "All systems operational. TikTok token refreshed automatically."
}}"""

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())
    except Exception as e:
        return {"health_score": 0, "status": "unknown", "error": str(e)}


# ── MAIN ENGINEER RUN ─────────────────────────────────────────────────────────

async def run_service_engineer() -> dict:
    """Full service engineer run — check, analyze, auto-fix."""
    start = time.time()
    timestamp = datetime.utcnow().isoformat() + "Z"
    
    print(f"[ServiceEngineer] Starting health check at {timestamp}")
    
    # Run all checks in parallel
    checks = await asyncio.gather(
        check_aegis_health(),
        check_briefs_pipeline(),
        check_tiktok_token(),
        check_gcs_storage(),
        check_asset_cache(),
        check_stripe(),
        check_firebase_hosting(),
    )
    
    health_report = {
        "timestamp": timestamp,
        "checks": {
            "aegis_health":     checks[0],
            "briefs_pipeline":  checks[1],
            "tiktok_token":     checks[2],
            "gcs_storage":      checks[3],
            "asset_cache":      checks[4],
            "stripe_checkout":  checks[5],
            "firebase_hosting": checks[6],
        }
    }
    
    # Auto-fix critical issues
    auto_fixes = []
    
    # Auto-refresh TikTok token if warning/critical
    if checks[2].get("status") in ("warning", "critical"):
        fix = await auto_refresh_tiktok_token()
        auto_fixes.append(fix)
        print(f"[ServiceEngineer] Auto-fixed: TikTok token refresh")
    
    # Auto-trigger brief cycle if count is 0
    if checks[1].get("brief_count", 0) == 0:
        fix = await auto_trigger_brief_cycle()
        auto_fixes.append(fix)
        print(f"[ServiceEngineer] Auto-fixed: Triggered brief cycle")
    
    health_report["auto_fixes"] = auto_fixes
    
    # Analyze with AI
    print("[ServiceEngineer] Analyzing with AI...")
    analysis = await analyze_with_gemini(health_report)
    health_report["analysis"] = analysis
    
    # Store report in Firestore
    try:
        from google.cloud import firestore
        db = firestore.Client(project=GCP_PROJECT)
        report_id = f"report-{int(time.time())}"
        db.collection(COLLECTION).document(report_id).set(health_report)
        print(f"[ServiceEngineer] Report stored: {report_id}")
    except Exception as e:
        print(f"[ServiceEngineer] Firestore store failed: {e}")
    
    elapsed = round(time.time() - start, 2)
    health_report["elapsed_seconds"] = elapsed
    
    print(f"[ServiceEngineer] Complete in {elapsed}s — Score: {analysis.get('health_score', '?')}")
    return health_report


# ── API ROUTES ────────────────────────────────────────────────────────────────

@svc_router.post("/run")
async def trigger_service_engineer():
    """Manually trigger service engineer run."""
    report = await run_service_engineer()
    return JSONResponse(report)


@svc_router.get("/status")
async def quick_status():
    """Quick status check — no AI analysis."""
    checks = await asyncio.gather(
        check_aegis_health(),
        check_briefs_pipeline(),
        check_tiktok_token(),
        check_firebase_hosting(),
    )
    
    statuses = [c.get("status") for c in checks]
    overall = "critical" if "critical" in statuses else "warning" if "warning" in statuses or "error" in statuses else "ok"
    
    return JSONResponse({
        "overall": overall,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "aegis": checks[0],
        "briefs": checks[1],
        "tiktok": checks[2],
        "firebase": checks[3],
    })


@svc_router.get("/report")
async def get_latest_report():
    """Get latest service engineer report from Firestore."""
    try:
        from google.cloud import firestore
        db = firestore.Client(project=GCP_PROJECT)
        docs = db.collection(COLLECTION).order_by(
            "timestamp", direction=firestore.Query.DESCENDING
        ).limit(1).stream()
        for doc in docs:
            return JSONResponse(doc.to_dict())
        return JSONResponse({"message": "No reports yet"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@svc_router.get("/history")
async def get_report_history():
    """Get last 10 service engineer reports."""
    try:
        from google.cloud import firestore
        db = firestore.Client(project=GCP_PROJECT)
        docs = db.collection(COLLECTION).order_by(
            "timestamp", direction=firestore.Query.DESCENDING
        ).limit(10).stream()
        reports = []
        for doc in docs:
            d = doc.to_dict()
            reports.append({
                "timestamp": d.get("timestamp"),
                "health_score": d.get("analysis", {}).get("health_score"),
                "status": d.get("analysis", {}).get("status"),
                "summary": d.get("analysis", {}).get("executive_summary"),
                "auto_fixes": len(d.get("auto_fixes", [])),
            })
        return JSONResponse({"reports": reports})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
