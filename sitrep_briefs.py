"""
SitRep Daily Brief Engine v3
Live RSS ingestion + CISA KEV + Anthropic API + Imagen 3 dynamic images
Each brief gets a story-specific AI-generated hero image
"""
import httpx
import json
import os
import xml.etree.ElementTree as ET
from datetime import date, datetime
from typing import Optional

# ── LOCAL KEYWORD → IMAGE KEY EXTRACTOR ──────────────────────────────────────
# Mirrors the ASSET_WIKI_MAP logic in main.py without importing it
_KEY_MAP = {
    "teams": "teams", "microsoft": "microsoft", "malware": "malware",
    "ransomware": "ransomware", "cisa": "cisa", "cve": "cve",
    "hack": "hack", "cyber": "cyber", "supply": "supply",
    "phishing": "phishing", "zero-day": "zero", "zero day": "zero",
    "botnet": "botnet", "router": "router",
    "drone": "drone", "missile": "missile", "shahed": "shahed",
    "f-35": "f35", "f35": "f35", "stealth": "stealth",
    "carrier": "carrier", "destroyer": "burke", "navy": "navy",
    "tank": "tank", "leopard": "leopard", "patriot": "patriot",
    "himars": "himars", "iskander": "iskander", "submarine": "submarine",
    "iran": "iran", "russia": "russia", "china": "china",
    "ukraine": "ukraine", "israel": "israel", "taiwan": "taiwan",
    "nato": "nato", "houthi": "houthi", "hormuz": "hormuz",
    "gulf": "gulf", "korea": "korea",
    "oil": "oil", "crude": "crude", "brent": "brent",
    "opec": "opec", "sanctions": "sanctions", "lng": "lng",
    "pipeline": "pipeline", "tanker": "tanker",
    "senate": "senate", "congress": "congress", "pentagon": "pentagon",
    "ceasefire": "ceasefire",
}

def extract_image_key_local(title: str, category: str) -> str:
    t = title.lower()
    for phrase, key in _KEY_MAP.items():
        if phrase in t:
            return key
    return category

REDIS_URL         = os.getenv("REDIS_URL", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_URL     = "https://api.anthropic.com/v1/messages"
CISA_KEV_URL      = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"

# ── RSS FEED MAP ──────────────────────────────────────────────────────────────
# One primary + one backup per category.  All return standard RSS 2.0 XML.
RSS_FEEDS = {
    "cyber": [
        "https://feeds.feedburner.com/TheHackersNews",
        "https://www.bleepingcomputer.com/feed/",
        "https://krebsonsecurity.com/feed/",
        "https://www.darkreading.com/rss.xml",
    ],
    "military": [
        "https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml",
        "https://feedx.net/rss/militarytimes.xml",
        "https://rss.app/feeds/tNolGMz0zxMqFBh2.xml",   # ISW daily
        "https://www.janes.com/feeds/news",
    ],
    "political": [
        "https://thehill.com/rss/syndicator/19110",
        "https://rss.politico.com/congress.xml",
        "https://feeds.washingtonpost.com/rss/politics",
        "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",
    ],
    "economic": [
        "https://feeds.bloomberg.com/markets/news.rss",
        "https://www.investing.com/rss/news_14.rss",
        "https://feeds.reuters.com/reuters/businessNews",
        "https://www.cnbc.com/id/10001147/device/rss/rss.html",
    ],
}

# ── REDIS ─────────────────────────────────────────────────────────────────────
def get_redis():
    import redis
    return redis.from_url(REDIS_URL, decode_responses=True)

def get_brief_key():
    # Key rotates daily — new briefs every day automatically
    return f"sitrep:briefs:{date.today().isoformat()}"

# ── RSS INGESTION ─────────────────────────────────────────────────────────────
def fetch_rss(url: str, max_items: int = 6) -> list[dict]:
    """Fetch one RSS feed and return a list of {title, description, pubDate, link} dicts."""
    headers = {
        "User-Agent": "SitRep/2.0 (sitrep.media; intelligence aggregator)",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }
    try:
        resp = httpx.get(url, timeout=8, headers=headers, follow_redirects=True)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        ns   = {"atom": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//item") or root.findall(".//atom:entry", ns)
        results = []
        for item in items[:max_items]:
            title = (item.findtext("title") or item.findtext("atom:title", namespaces=ns) or "").strip()
            desc  = (item.findtext("description") or item.findtext("atom:summary", namespaces=ns) or "").strip()
            pub   = (item.findtext("pubDate") or item.findtext("atom:updated", namespaces=ns) or "").strip()
            link  = (item.findtext("link") or item.findtext("atom:link", namespaces=ns) or "").strip()
            # Strip any HTML tags from description
            import re
            desc = re.sub(r"<[^>]+>", "", desc)[:300]
            if title:
                results.append({"title": title, "description": desc, "pubDate": pub, "link": link})
        return results
    except Exception as e:
        print(f"RSS fetch failed [{url}]: {e}")
        return []

def fetch_category_news(category: str) -> str:
    """Try each feed in order until we get usable headlines. Returns formatted string."""
    feeds = RSS_FEEDS.get(category, [])
    all_items = []
    for url in feeds:
        items = fetch_rss(url, max_items=5)
        if items:
            all_items.extend(items)
        if len(all_items) >= 8:
            break

    if not all_items:
        return f"No live {category} feed available — use your knowledge of current events."

    today_str = datetime.utcnow().strftime("%B %d, %Y")
    lines = [f"Live {category.upper()} headlines ingested {today_str}:"]
    for i, item in enumerate(all_items[:8], 1):
        lines.append(f"{i}. {item['title']}")
        if item["description"]:
            lines.append(f"   {item['description'][:200]}")
    return "\n".join(lines)

# ── CISA KEV ──────────────────────────────────────────────────────────────────
def fetch_cisa_kev() -> str:
    try:
        r = httpx.get(CISA_KEV_URL, timeout=10)
        data = r.json()
        vulns = data.get("vulnerabilities", [])[-5:]
        lines = [f"CISA KEV — {len(data.get('vulnerabilities',[]))} total known exploited vulnerabilities. Latest 5:"]
        for v in vulns:
            lines.append(
                f"- {v.get('cveID')}: {v.get('vulnerabilityName')} — "
                f"{v.get('shortDescription','')[:120]} (Remediation due: {v.get('dueDate','')})"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"CISA KEV unavailable: {e}"

# ── INTEL CONTEXT ─────────────────────────────────────────────────────────────
def get_intel_context() -> dict:
    """Pull live data for all four categories concurrently-ish."""
    print("Ingesting live intelligence feeds...")
    return {
        "cyber":     fetch_cisa_kev() + "\n\n" + fetch_category_news("cyber"),
        "military":  fetch_category_news("military"),
        "political": fetch_category_news("political"),
        "economic":  fetch_category_news("economic"),
    }

# ── ANTHROPIC CALL ────────────────────────────────────────────────────────────
def call_anthropic(prompt: str) -> str:
    headers = {
        "x-api-key":         ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "Content-Type":      "application/json",
    }
    body = {
        "model":      "claude-sonnet-4-20250514",
        "max_tokens": 1500,
        "messages":   [{"role": "user", "content": prompt}],
    }
    r = httpx.post(ANTHROPIC_URL, headers=headers, json=body, timeout=45)
    data = r.json()
    return data["content"][0]["text"].strip()

# ── PROMPT BUILDER ────────────────────────────────────────────────────────────
def build_prompt(category: str, intel: str) -> str:
    today_full  = datetime.utcnow().strftime("%A, %B %d, %Y")   # e.g. Thursday, April 23, 2026
    today_id    = date.today().strftime("%Y%m%d")

    cat_map = {
        "cyber":     ("cyber threat",                 "CISA KEV - CVE - Vendor advisories",               "technical effect",   "infrastructure impact"),
        "military":  ("military/conflict development", "ISW - CENTCOM - Open source OSINT",                "military effect",    "regional escalation impact"),
        "political": ("political development",         "Treasury OFAC - State Dept - Congressional record", "political effect",   "diplomatic consequence"),
        "economic":  ("economic/market development",   "EIA - Treasury - TankerTrackers - Bloomberg",       "market effect",      "supply chain / consumer impact"),
    }
    desc, sources, effect2, effect3 = cat_map[category]

    return f"""You are SENTRY, an intelligence analyst generating a daily {desc} brief dated {today_full}.

IMPORTANT: Today is {today_full}. Base this brief on the most recent and significant event in the live intelligence feed below. Do NOT reference events from prior months as if they are current.

Live intelligence feed:
{intel}

Generate a brief as a JSON object with these exact fields:
- id: "{category}-{today_id}"
- cat: "{category}"
- title: compelling headline under 15 words about the MOST IMPORTANT {desc} as of {today_full}
- source: specific sources like {sources}
- score: priority number 1-100 as a string
- hook: one punchy sentence a content creator opens their video with TODAY
- wmm: 2-3 sentences on what mainstream media is missing about this story RIGHT NOW
- points: array of exactly 5 specific talking point strings, each grounded in today's news
- cascade: array of exactly 6 objects each with n (01-06 as strings), t (short title string), s (brief subtitle string). Chain: trigger event → {effect2} → {effect3} → economic consequence → market signal → consumer impact
- script: 60-second teleprompter script for creator to read on camera. Conversational, punchy, present tense. Ends with one provocative question for the audience. No asterisks no markdown no special characters no em-dashes.

Return ONLY the raw JSON object. No markdown fences. No explanation. No text before or after the JSON.
The JSON must be valid and parseable."""

# ── BRIEF GENERATION ──────────────────────────────────────────────────────────
def generate_brief(category: str, intel: str) -> dict:
    prompt = build_prompt(category, intel)
    text   = call_anthropic(prompt)

    # Strip any accidental markdown fences
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    # Find first { in case Claude added preamble
    brace = text.find("{")
    if brace > 0:
        text = text[brace:]

    brief = json.loads(text.strip())

    # ── WIKIPEDIA IMAGE — fetch story-matched image via asset key ────────────
    brief["image"] = ""
    brief["image_key"] = ""
    try:
        title = brief.get("title", "")
        img_key = extract_image_key_local(title, category)
        brief["image_key"] = img_key
        print(f"[Image] Brief [{category}] mapped to key: {img_key}")
    except Exception as e:
        print(f"[Image] Key extraction error: {e}")

    return brief

def generate_all_briefs() -> list:
    intel   = get_intel_context()
    briefs  = []
    for cat in ["cyber", "military", "political", "economic"]:
        try:
            brief = generate_brief(cat, intel[cat])
            briefs.append(brief)
            print(f"[SENTRY] Generated {cat} brief: {brief.get('title','')[:60]}")
        except Exception as e:
            print(f"[SENTRY] Error generating {cat} brief: {e}")
    return briefs

# ── CACHE ─────────────────────────────────────────────────────────────────────
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
        r   = get_redis()
        ttl = 1800  # 30 minutes — force refresh twice an hour so news stays hot
        r.setex(get_brief_key(), ttl, json.dumps(briefs))
        print(f"[SENTRY] Cached {len(briefs)} briefs (TTL {ttl}s) for {date.today().isoformat()}")
    except Exception as e:
        print(f"Redis cache error: {e}")
