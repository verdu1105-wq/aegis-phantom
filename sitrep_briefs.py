"""
SitRep Daily Brief Engine
Uses httpx to call Anthropic API directly - no SDK needed
"""
import httpx
import json
import os
from datetime import date
from typing import Optional

REDIS_URL = os.getenv("REDIS_URL", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
CISA_KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"

def get_redis():
    import redis
    return redis.from_url(REDIS_URL, decode_responses=True)

def get_brief_key():
    return f"sitrep:briefs:{date.today().isoformat()}"

def fetch_cisa_kev() -> str:
    try:
        r = httpx.get(CISA_KEV_URL, timeout=10)
        data = r.json()
        vulns = data.get("vulnerabilities", [])[-5:]
        lines = []
        for v in vulns:
            lines.append(f"- {v.get('cveID')}: {v.get('vulnerabilityName')} - {v.get('shortDescription','')[:120]} (Due: {v.get('dueDate','')})")
        return "\n".join(lines)
    except Exception as e:
        return f"CISA KEV unavailable: {e}"

def get_intel_context() -> dict:
    return {
        "cyber": fetch_cisa_kev(),
        "military": """Latest military/conflict open source intelligence:
- Iran IRGC forces maintaining elevated readiness in Gulf region
- F-35 stealth detection claims from Iranian state media unconfirmed by US and Israel
- Ukrainian drone operations targeting Russian oil infrastructure continuing
- Taiwan Strait PLA naval exercises frequency elevated Q1 2026
- Red Sea shipping diversions continue due to Houthi anti-ship missile threat
- US CENTCOM forces repositioning in Gulf with carrier strike group movements noted""",
        "political": """Latest political and policy intelligence:
- Iran nuclear talks stalled with IAEA inspectors denied access to Fordow facility
- US Senate Armed Services Committee hearing on cyber vulnerabilities in defense supply chain
- Executive order on AI in critical infrastructure signed with 90-day compliance window
- NATO Article 5 consultations ongoing regarding hybrid warfare definitions
- House Armed Services markup includes 2.1 billion for cyber operations expansion
- Gulf Cooperation Council emergency session called with agenda undisclosed""",
        "economic": """Latest economic and market intelligence:
- Brent crude trading with elevated risk premium as Hormuz transit insurance up 18 percent Q1 2026
- OFAC issued new Iran sanctions targeting shadow fleet operators with 12 entities designated
- US Treasury yield curve signaling uncertainty with defense sector ETFs outperforming
- LNG spot prices elevated as European storage remains below seasonal average
- Dollar strengthening on safe-haven flows with emerging market debt under pressure
- Gulf sovereign wealth funds increasing defensive allocations"""
    }

def call_anthropic(prompt: str) -> str:
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json"
    }
    body = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1500,
        "messages": [{"role": "user", "content": prompt}]
    }
    r = httpx.post(ANTHROPIC_URL, headers=headers, json=body, timeout=30)
    data = r.json()
    return data["content"][0]["text"].strip()

def build_prompt(category: str, intel: str) -> str:
    today = date.today().strftime("%Y%m%d")
    cat_map = {
        "cyber": ("cyber threat", "CISA KEV - CVE - Priority score", "technical effect", "infrastructure impact"),
        "military": ("military/conflict development", "ISW - CENTCOM - Open source", "military effect", "regional impact"),
        "political": ("political development", "Treasury OFAC - State Dept - Congressional record", "political effect", "diplomatic consequence"),
        "economic": ("economic/market development", "EIA - Treasury - TankerTrackers - GasBuddy", "market effect", "supply chain impact")
    }
    desc, sources, effect2, effect3 = cat_map[category]
    return f"""You are SENTRY, an intelligence analyst generating a daily {desc} brief for content creators covering national security.

Live intelligence feed:
{intel}

Generate a brief as a JSON object with these exact fields:
- id: "{category}-{today}"
- cat: "{category}"
- title: compelling headline under 15 words about the most important {desc} today
- source: specific sources like {sources}
- score: priority number 1-100 as a string
- hook: one punchy sentence a creator opens their video with
- wmm: 2-3 sentences on what mainstream media missed
- points: array of exactly 5 specific talking point strings
- cascade: array of exactly 6 objects each with n (01-06), t (short title), s (brief subtitle). Steps: trigger event, {effect2}, {effect3}, economic consequence, market signal, consumer impact
- script: 60-second teleprompter script for creator to read on camera. Conversational and punchy. Ends with a question for the audience. No asterisks no markdown no special characters.

Return ONLY the raw JSON object with no markdown fences no explanation no other text."""

def generate_brief(category: str, intel: str) -> dict:
    prompt = build_prompt(category, intel)
    text = call_anthropic(prompt)
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())

def generate_all_briefs() -> list:
    intel = get_intel_context()
    briefs = []
    for cat in ["cyber", "military", "political", "economic"]:
        try:
            brief = generate_brief(cat, intel[cat])
            briefs.append(brief)
            print(f"Generated {cat} brief: {brief.get('title','')[:50]}")
        except Exception as e:
            print(f"Error generating {cat} brief: {e}")
    return briefs

def get_cached_briefs() -> Optional[list]:
    try:
        r = get_redis()
        cached = r.get(get_brief_key())
        if cached:
            return json.loads(cached)
    except Exception as e:
        print(f"Redis cache miss: {e}")
    return None

def cache_briefs(briefs: list):
    try:
        r = get_redis()
        r.setex(get_brief_key(), 86400, json.dumps(briefs))
        print(f"Cached {len(briefs)} briefs for {date.today().isoformat()}")
    except Exception as e:
        print(f"Redis cache error: {e}")
