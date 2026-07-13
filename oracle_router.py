"""
oracle_router.py — Oracle API Router
Exposes Oracle endpoints for Bat-Computer and Alfred.

Endpoints:
  POST /oracle/query       — General intelligence query
  POST /oracle/threat      — Threat actor lookup
  POST /oracle/cve         — CVE intelligence
  POST /oracle/osint       — OSINT profile
  POST /oracle/verify      — News verification
  GET  /oracle/status      — Oracle health check
"""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from oracle import (
    oracle_query,
    threat_actor_lookup,
    cve_lookup,
    osint_lookup,
    news_verify,
    PERPLEXITY_API_KEY,
)

oracle_router = APIRouter(prefix="/oracle", tags=["oracle"])


class OracleRequest(BaseModel):
    query:   str
    context: Optional[str] = None


class ThreatRequest(BaseModel):
    name: str


class CVERequest(BaseModel):
    cve_id: str


class OSINTRequest(BaseModel):
    target: str


class VerifyRequest(BaseModel):
    headline: str


@oracle_router.get("/status")
async def oracle_status():
    """Oracle health check — called during Bat-Computer boot."""
    return {
        "status":  "ONLINE" if PERPLEXITY_API_KEY else "DEGRADED",
        "engine":  "Perplexity Sonar",
        "version": "1.0.0",
        "capabilities": [
            "real_time_search",
            "threat_actor_lookup",
            "cve_intelligence",
            "osint_profiling",
            "news_verification",
            "semantic_matching",
        ],
    }


@oracle_router.post("/query")
async def oracle_general(req: OracleRequest):
    """General Oracle query — Alfred routes here for current intelligence."""
    result = await oracle_query(req.query, req.context)
    return result


@oracle_router.post("/threat")
async def oracle_threat(req: ThreatRequest):
    """Threat actor OSINT profile."""
    return await threat_actor_lookup(req.name)


@oracle_router.post("/cve")
async def oracle_cve(req: CVERequest):
    """CVE real-time intelligence."""
    return await cve_lookup(req.cve_id)


@oracle_router.post("/osint")
async def oracle_osint(req: OSINTRequest):
    """OSINT lookup on account, domain, or person."""
    return await osint_lookup(req.target)


@oracle_router.post("/verify")
async def oracle_verify(req: VerifyRequest):
    """News verification across multiple sources."""
    return await news_verify(req.headline)
