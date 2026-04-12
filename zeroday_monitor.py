"""
AEGIS SENTRY — Zero-Day Monitor
================================
Scheduled job that runs every 2 hours via Cloud Scheduler.
Fetches CISA KEV and NVD CVE feeds, scores each vulnerability,
stores high-priority items in Redis for SENTRY Monitor Mode.

Add to main.py or deploy as standalone Cloud Run Job.

Cloud Scheduler config:
  Schedule:  0 */2 * * *  (every 2 hours)
  Target:    POST https://aegis-cwis-xxx.run.app/api/zeroday/refresh
  Auth:      OIDC token
"""

import os
from typing import List, Optional
import json
import asyncio
from datetime import datetime, timedelta, timezone
import httpx
import redis as redis_lib
from fastapi import APIRouter

# ── ROUTER (add to main FastAPI app) ──────────────────────────────────────────
zeroday_router = APIRouter(prefix="/api/zeroday", tags=["zeroday"])

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
_redis_client = None

def get_redis():
    """Lazy Redis connection — reads REDIS_URL at call time, not module load."""
    global _redis_client
    if _redis_client is None:
        try:
            url = os.getenv("REDIS_URL", REDIS_URL)
            _redis_client = redis_lib.from_url(url, decode_responses=True, socket_connect_timeout=5)
            _redis_client.ping()
            print(f"Zero-day monitor: Redis connected")
        except Exception as e:
            print(f"Zero-day monitor: Redis unavailable: {e}")
            _redis_client = None
    return _redis_client

# ── SCORING MODEL ─────────────────────────────────────────────────────────────
def compute_priority_score(cve: dict) -> int:
    """
    Compute 0-100 priority score per Aegis vuln_alert scoring model.
    KEV listed:        35 pts
    Exploitation:      0-25 pts
    CVSS (normalized): 0-20 pts
    Ransomware use:    10 pts
    Zero-day:          8 pts
    Environment:       0-10 pts (unknown = 0 for now)
    """
    score = 0

    if cve.get("kev_listed"):
        score += 35

    exploit_status = cve.get("exploitation_status", "unknown")
    exploit_scores = {
        "actively_exploited": 25,
        "exploit_available":  15,
        "poc_available":       8,
        "no_known_exploit":    0,
        "unknown":             3,
    }
    score += exploit_scores.get(exploit_status, 3)

    cvss = cve.get("cvss_score", 0) or 0
    if cvss >= 9.0:
        score += 20
    elif cvss >= 7.0:
        score += 14
    elif cvss >= 5.0:
        score += 8
    elif cvss == 0 and cve.get("kev_listed"):
        # KEV entries often lack CVSS — assume high severity
        score += 12

    if cve.get("ransomware_use"):
        score += 10

    if cve.get("zero_day"):
        score += 8

    return min(score, 100)


# ── CISA KEV FEED ─────────────────────────────────────────────────────────────
async def fetch_cisa_kev():
    """Fetch the CISA Known Exploited Vulnerabilities catalog."""
    url = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

    vulnerabilities = data.get("vulnerabilities", [])
    results = []

    # Only process KEV entries added in the last 30 days
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)

    for v in vulnerabilities:
        date_added_str = v.get("dateAdded", "")
        try:
            date_added = datetime.strptime(date_added_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            continue

        if date_added < cutoff:
            continue

        ransomware = v.get("knownRansomwareCampaignUse", "Unknown").lower() == "known"

        cve_obj = {
            "cve_id":             v.get("cveID", ""),
            "title":              v.get("vulnerabilityName", ""),
            "vendor":             v.get("vendorProject", ""),
            "product":            v.get("product", ""),
            "description":        v.get("shortDescription", ""),
            "required_action":    v.get("requiredAction", ""),
            "kev_listed":         True,
            "kev_date_added":     date_added_str,
            "kev_due_date":       v.get("dueDate", ""),
            "ransomware_use":     ransomware,
            "exploitation_status": "actively_exploited",
            "zero_day":           False,
            "cvss_score":         0.0,
            "patch_available":    True,
            "source":             "CISA KEV",
            "ingested_at":        datetime.now(timezone.utc).isoformat(),
        }
        cve_obj["priority_score"] = compute_priority_score(cve_obj)
        results.append(cve_obj)

    return results


# ── NVD API FEED ──────────────────────────────────────────────────────────────
async def fetch_nvd_recent():
    """
    Fetch CVEs from NVD published in the last 7 days with CVSS >= 7.0.
    NVD API v2: https://nvd.nist.gov/developers/vulnerabilities
    Rate limit: 5 req/30s without API key — add NVD_API_KEY env var for higher limits.
    """
    nvd_api_key = os.getenv("NVD_API_KEY", "")
    end_date   = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=7)

    pub_start = start_date.strftime("%Y-%m-%dT%H:%M:%S.000")
    pub_end   = end_date.strftime("%Y-%m-%dT%H:%M:%S.000")

    url = "https://services.nvd.nist.gov/rest/json/cves/2.0"
    params = {
        "pubStartDate": pub_start,
        "pubEndDate":   pub_end,
        "cvssV3Severity": "CRITICAL",
    }

    headers = {}
    if nvd_api_key:
        headers["apiKey"] = nvd_api_key

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"NVD fetch error: {e}")
            return []

    results = []
    for item in data.get("vulnerabilities", []):
        cve_data = item.get("cve", {})
        cve_id   = cve_data.get("id", "")

        # Get CVSS score
        cvss_score = 0.0
        metrics = cve_data.get("metrics", {})
        for metric_key in ["cvssMetricV31", "cvssMetricV30", "cvssMetricV40"]:
            metric_list = metrics.get(metric_key, [])
            if metric_list:
                cvss_score = metric_list[0].get("cvssData", {}).get("baseScore", 0.0)
                break

        if cvss_score < 7.0:
            continue

        # Description
        descriptions = cve_data.get("descriptions", [])
        desc = next((d["value"] for d in descriptions if d.get("lang") == "en"), "")

        # Published date
        published = cve_data.get("published", "")

        # Check if zero-day (no patch within 24h of publication)
        vuln_status = cve_data.get("vulnStatus", "")
        zero_day = "Received" in vuln_status or "Awaiting Analysis" in vuln_status

        cve_obj = {
            "cve_id":              cve_id,
            "title":               f"{cve_id} — {desc[:80]}..." if len(desc) > 80 else f"{cve_id} — {desc}",
            "description":         desc,
            "cvss_score":          cvss_score,
            "kev_listed":          False,
            "kev_date_added":      None,
            "kev_due_date":        None,
            "ransomware_use":      False,
            "exploitation_status": "unknown",
            "zero_day":            zero_day,
            "patch_available":     "patch" in desc.lower() or "update" in desc.lower(),
            "published_at":        published,
            "source":              "NVD",
            "ingested_at":         datetime.now(timezone.utc).isoformat(),
        }
        cve_obj["priority_score"] = compute_priority_score(cve_obj)

        # Only store if priority score is meaningful
        if cve_obj["priority_score"] >= 20:
            results.append(cve_obj)

    return results


# ── VENDOR ZERO-DAY KEYWORDS ──────────────────────────────────────────────────
ZERODAY_KEYWORDS = [
    "zero-day", "0-day", "actively exploited", "no patch available",
    "emergency patch", "out-of-band", "critical vulnerability",
    "remote code execution", "authentication bypass", "privilege escalation",
    "unauthenticated", "pre-auth", "wormable"
]

WATCHED_VENDORS = [
    "Microsoft", "Fortinet", "Cisco", "Palo Alto", "Citrix",
    "Ivanti", "VMware", "F5", "SolarWinds", "MOVEit", "Juniper"
]


# ── REDIS STORAGE ─────────────────────────────────────────────────────────────
def store_zeroday_alerts(alerts, source):
    """Store zero-day alerts in Redis with TTL."""
    rc = get_redis()
    if not rc:
        print(f"store_zeroday_alerts: Redis not available, skipping {len(alerts)} alerts")
        return 0

    stored = 0
    for alert in alerts:
        cve_id = alert.get("cve_id", "unknown")
        key = f"aegis:zeroday:{cve_id}"

        existing = rc.get(key)
        if existing:
            try:
                existing_obj = json.loads(existing)
                if existing_obj.get("priority_score", 0) >= alert.get("priority_score", 0):
                    continue
            except Exception:
                pass

        rc.setex(key, 30 * 24 * 3600, json.dumps(alert))
        stored += 1

    all_keys = rc.keys("aegis:zeroday:CVE-*")
    rc.set("aegis:zeroday:index", json.dumps([k.replace("aegis:zeroday:", "") for k in all_keys]))
    rc.set("aegis:zeroday:last_refresh", datetime.now(timezone.utc).isoformat())

    return stored


def get_zeroday_alerts(min_score=40, limit=20):
    """Retrieve zero-day alerts from Redis, sorted by priority score."""
    rc = get_redis()
    if not rc:
        return []

    index = rc.get("aegis:zeroday:index")
    if not index:
        return []

    cve_ids = json.loads(index)
    alerts = []

    for cve_id in cve_ids:
        raw = rc.get(f"aegis:zeroday:{cve_id}")
        if raw:
            try:
                alert = json.loads(raw)
                if alert.get("priority_score", 0) >= min_score:
                    alerts.append(alert)
            except Exception:
                continue

    alerts.sort(key=lambda x: x.get("priority_score", 0), reverse=True)
    return alerts[:limit]


# ── FASTAPI ENDPOINTS ─────────────────────────────────────────────────────────
@zeroday_router.post("/refresh")
async def refresh_zeroday_feed():
    """
    Scheduled endpoint — called by Cloud Scheduler every 2 hours.
    Fetches CISA KEV and NVD, scores, stores in Redis.
    """
    results = {"kev": 0, "nvd": 0, "errors": []}

    # Fetch CISA KEV
    try:
        kev_alerts = await fetch_cisa_kev()
        stored = store_zeroday_alerts(kev_alerts, "CISA KEV")
        results["kev"] = stored
        print(f"KEV: fetched {len(kev_alerts)}, stored {stored} new/updated")
    except Exception as e:
        results["errors"].append(f"KEV fetch failed: {str(e)}")
        print(f"KEV error: {e}")

    # Fetch NVD recent critical CVEs
    try:
        nvd_alerts = await fetch_nvd_recent()
        stored = store_zeroday_alerts(nvd_alerts, "NVD")
        results["nvd"] = stored
        print(f"NVD: fetched {len(nvd_alerts)}, stored {stored} new/updated")
    except Exception as e:
        results["errors"].append(f"NVD fetch failed: {str(e)}")
        print(f"NVD error: {e}")

    results["timestamp"] = datetime.now(timezone.utc).isoformat()
    results["total_stored"] = results["kev"] + results["nvd"]
    return results


@zeroday_router.get("/alerts")
async def get_alerts(min_score: int = 40, limit: int = 20):
    """
    Return current zero-day alerts from Redis.
    Used by SENTRY Monitor Mode and COMMAND threat panel.
    """
    try:
        rc = get_redis()
        if not rc:
            return {"alerts": [], "count": 0, "last_refresh": None, "error": "Redis unavailable"}

        last_refresh = None
        try:
            last_refresh = rc.get("aegis:zeroday:last_refresh")
        except Exception:
            pass

        index_raw = None
        try:
            index_raw = rc.get("aegis:zeroday:index")
        except Exception:
            pass

        if not index_raw:
            return {"alerts": [], "count": 0, "last_refresh": last_refresh, "min_score_filter": min_score}

        try:
            cve_ids = json.loads(index_raw)
        except Exception:
            return {"alerts": [], "count": 0, "last_refresh": last_refresh, "error": "index parse error"}

        alerts = []
        for cve_id in cve_ids:
            try:
                raw = rc.get(f"aegis:zeroday:{cve_id}")
                if raw:
                    alert = json.loads(raw)
                    if alert.get("priority_score", 0) >= min_score:
                        alerts.append(alert)
            except Exception:
                continue

        alerts.sort(key=lambda x: x.get("priority_score", 0), reverse=True)
        return {
            "alerts": alerts[:limit],
            "count": len(alerts[:limit]),
            "total_above_threshold": len(alerts),
            "last_refresh": last_refresh,
            "min_score_filter": min_score
        }
    except Exception as e:
        return {"alerts": [], "count": 0, "error": str(e), "last_refresh": None}


@zeroday_router.get("/status")
async def zeroday_status():
    """Health check for zero-day monitoring system."""
    rc = get_redis()
    if not rc:
        return {"status": "degraded", "reason": "Redis unavailable"}

    last_refresh = rc.get("aegis:zeroday:last_refresh")
    index = rc.get("aegis:zeroday:index")
    count = len(json.loads(index)) if index else 0

    status = "healthy"
    if last_refresh:
        last_dt = datetime.fromisoformat(last_refresh)
        if datetime.now(timezone.utc) - last_dt > timedelta(hours=4):
            status = "stale"
    else:
        status = "never_run"

    return {
        "status": status,
        "last_refresh": last_refresh,
        "alert_count": count,
        "feeds": ["CISA KEV", "NVD Critical CVEs"],
        "refresh_schedule": "every 2 hours"
    }
