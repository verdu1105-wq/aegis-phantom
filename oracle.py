"""
oracle.py — ORACLE Intelligence Engine v1.0
Powered by Perplexity API (Search + Agent + Embeddings)
Alfred's real-time OSINT and threat intelligence specialist.

Alfred orchestrates. Oracle researches.
"""

import os
import httpx
import json
import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger("oracle")

PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "")
SEARCH_URL         = "https://api.perplexity.ai/search"
AGENT_URL          = "https://api.perplexity.ai/v1/responses"
EMBED_URL          = "https://api.perplexity.ai/v1/embeddings"
EMBED_MODEL        = "pplx-embed-v1-4b"

HEADERS = {
    "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
    "Content-Type":  "application/json",
}

# ── Query Classification ──────────────────────────────────────────────────────
QUERY_TRIGGERS = {
    "threat_actor": ["apt", "group", "actor", "nation state", "tta", "sandworm", "fancy bear", "lazarus", "volt typhoon"],
    "cve":          ["cve-", "vulnerability", "zero-day", "exploit", "patch", "cvss"],
    "osint":        ["who is", "lookup", "profile", "account", "linked to", "connections"],
    "news":         ["what happened", "latest", "today", "breaking", "current", "recent"],
    "forensic":     ["analyze", "forensic", "evidence", "pattern", "behavior", "cluster"],
}

def classify_query(prompt: str) -> str:
    p = prompt.lower()
    for qtype, triggers in QUERY_TRIGGERS.items():
        if any(t in p for t in triggers):
            return qtype
    return "general"


# ── Core API Calls ────────────────────────────────────────────────────────────

async def _search(query: str, max_results: int = 5) -> dict:
    """Raw Perplexity Search API call."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(SEARCH_URL, headers=HEADERS, json={
            "query":               query,
            "max_results":         max_results,
            "max_tokens_per_page": 512,
        })
        resp.raise_for_status()
        return resp.json()


async def _agent(prompt: str, preset: str = "fast-search") -> dict:
    """Perplexity Agent API — multi-step reasoning with web search."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(AGENT_URL, headers=HEADERS, json={
            "preset": preset,
            "input":  prompt,
        })
        resp.raise_for_status()
        return resp.json()


async def _embed(texts: list[str]) -> list[list[float]]:
    """Perplexity Embeddings — semantic similarity for vault matching."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(EMBED_URL, headers=HEADERS, json={
            "input": texts,
            "model": EMBED_MODEL,
        })
        resp.raise_for_status()
        data = resp.json()
        return [item["embedding"] for item in data.get("data", [])]


# ── Oracle Query Engine ───────────────────────────────────────────────────────

async def oracle_query(prompt: str, context: Optional[str] = None) -> dict:
    """
    Main Oracle entry point — Alfred calls this.
    Routes to Search or Agent based on query type.
    Returns structured intel brief with citations.
    """
    qtype = classify_query(prompt)
    log.info(f"[ORACLE] Query type: {qtype} | Prompt: {prompt[:80]}")

    # Build enriched prompt for agent queries
    enriched = prompt
    if context:
        enriched = f"{context}\n\nQuery: {prompt}"

    try:
        if qtype in ("threat_actor", "forensic", "cve"):
            # Deep analysis — use Agent API with reasoning preset
            raw = await _agent(enriched, preset="fast-search")
            return _parse_agent_response(raw, qtype, prompt)
        else:
            # Fast lookup — use Search API
            raw = await _search(enriched, max_results=5)
            return _parse_search_response(raw, qtype, prompt)
    except Exception as e:
        log.error(f"[ORACLE] Error: {e}")
        return {
            "status":    "error",
            "error":     str(e),
            "brief":     "Oracle is temporarily unavailable. Falling back to Alfred's knowledge base.",
            "citations": [],
            "qtype":     qtype,
        }


def _parse_agent_response(raw: dict, qtype: str, prompt: str) -> dict:
    """Parse Agent API response into Oracle brief format."""
    # Extract text output
    output = raw.get("output", "")
    if isinstance(output, list):
        text = " ".join([o.get("text", "") or o.get("content", "") for o in output if isinstance(o, dict)])
    else:
        text = str(output)

    # Extract citations
    citations = []
    for item in raw.get("citations", raw.get("sources", [])):
        if isinstance(item, dict):
            citations.append({
                "title": item.get("title", "Source"),
                "url":   item.get("url", item.get("link", "")),
            })
        elif isinstance(item, str):
            citations.append({"title": "Source", "url": item})

    return {
        "status":    "ok",
        "qtype":     qtype,
        "brief":     text.strip(),
        "citations": citations[:5],
        "source":    "perplexity_agent",
        "timestamp": datetime.utcnow().isoformat(),
        "query":     prompt,
    }


def _parse_search_response(raw: dict, qtype: str, prompt: str) -> dict:
    """Parse Search API response into Oracle brief format."""
    results = raw.get("results", raw.get("items", []))
    if not results:
        return {
            "status":    "empty",
            "brief":     "No results found for this query.",
            "citations": [],
            "qtype":     qtype,
        }

    # Build brief from top results
    brief_parts = []
    citations   = []
    for r in results[:4]:
        title   = r.get("title", "")
        snippet = r.get("snippet", r.get("content", r.get("text", "")))
        url     = r.get("url", r.get("link", ""))
        if snippet:
            brief_parts.append(f"**{title}**: {snippet}")
        if url:
            citations.append({"title": title, "url": url})

    return {
        "status":    "ok",
        "qtype":     qtype,
        "brief":     "\n\n".join(brief_parts),
        "citations": citations,
        "source":    "perplexity_search",
        "timestamp": datetime.utcnow().isoformat(),
        "query":     prompt,
    }


# ── Specialized Oracle Functions ──────────────────────────────────────────────

async def threat_actor_lookup(name: str) -> dict:
    """Full OSINT profile on a threat actor or APT group."""
    prompt = f"""
    Provide a detailed threat intelligence profile on: {name}
    Include:
    - Attribution (nation state, criminal group, hacktivist)
    - Known TTPs (MITRE ATT&CK techniques)
    - Recent campaigns and targets
    - Infrastructure indicators (domains, IPs, tools)
    - Current activity level
    Format as a structured intelligence brief.
    """
    return await oracle_query(prompt)


async def cve_lookup(cve_id: str) -> dict:
    """Real-time CVE intelligence with current exploitation status."""
    prompt = f"""
    Provide current intelligence on {cve_id}:
    - CVSS score and severity
    - Affected systems and versions
    - Exploitation status (in the wild? ransomware use?)
    - Available patches or mitigations
    - Recent news or CISA KEV status
    """
    return await oracle_query(prompt)


async def osint_lookup(target: str) -> dict:
    """OSINT profile on a social media account, domain, or person."""
    prompt = f"""
    Conduct an OSINT lookup on: {target}
    Look for:
    - Account history and behavior patterns
    - Network connections and linked accounts
    - Platform activity and content patterns
    - Any reported abuse or coordinated activity
    - Infrastructure links if applicable
    Keep analysis factual and evidence-based.
    """
    return await oracle_query(prompt)


async def news_verify(headline: str) -> dict:
    """Verify a news claim across multiple sources."""
    prompt = f"""
    Verify this claim across multiple sources: "{headline}"
    - Is this confirmed by multiple credible outlets?
    - What is the original source?
    - Are there any conflicting reports?
    - What is the current status?
    Provide a confidence assessment: CONFIRMED / UNCONFIRMED / FALSE / DEVELOPING
    """
    return await oracle_query(prompt)


async def semantic_match(query: str, candidates: list[str]) -> list[float]:
    """
    Use embeddings to find semantically similar items.
    Useful for matching threat descriptions to vault accounts.
    Returns similarity scores.
    """
    if not candidates:
        return []
    texts   = [query] + candidates
    vectors = await _embed(texts)
    if len(vectors) < 2:
        return []

    query_vec = vectors[0]
    scores    = []
    for vec in vectors[1:]:
        # Cosine similarity
        dot    = sum(a * b for a, b in zip(query_vec, vec))
        mag_q  = sum(a ** 2 for a in query_vec) ** 0.5
        mag_v  = sum(a ** 2 for a in vec) ** 0.5
        score  = dot / (mag_q * mag_v) if mag_q and mag_v else 0.0
        scores.append(round(score, 4))
    return scores
