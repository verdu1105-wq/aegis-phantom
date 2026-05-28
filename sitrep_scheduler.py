"""
sitrep_scheduler.py
SitRep Automated Brief + Video Pipeline
Runs inside aegis-cwis Cloud Run service
Triggered by Cloud Scheduler every 6 hours + breaking news check every 15 min
"""

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import anthropic
import httpx
import redis as redis_lib

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sitrep_scheduler")

# ── Redis connection (same as existing aegis-cwis config) ──────────────────────
REDIS_URL = os.environ.get("REDIS_URL", "")
r = redis_lib.from_url(REDIS_URL, decode_responses=True)

# ── Category config ────────────────────────────────────────────────────────────
CATEGORIES = [
    {
        "key": "military",
        "label": "Military Intelligence",
        "style": "kinetic",          # video style
        "priority_rss": [
            "https://www.defensenews.com/rss/",
            "https://feeds.feedburner.com/jomsblog",
            "https://understandingwar.org/rss.xml",
        ],
        "post_cadence_hours": 6,
    },
    {
        "key": "cyber",
        "label": "Cyber Threat Intelligence",
        "style": "hud",
        "priority_rss": [
            "https://www.cisa.gov/news.xml",
            "https://feeds.feedburner.com/TheHackersNews",
            "https://krebsonsecurity.com/feed/",
        ],
        "post_cadence_hours": 6,
    },
    {
        "key": "political",
        "label": "Political Intelligence",
        "style": "breaking_news",
        "priority_rss": [
            "https://thehill.com/rss/syndicator/19109",
            "https://rss.politico.com/politics-news.xml",
            "https://feeds.npr.org/1001/rss.xml",
        ],
        "post_cadence_hours": 6,
    },
    {
        "key": "economic",
        "label": "Economic Intelligence",
        "style": "breaking_news",
        "priority_rss": [
            "https://feeds.reuters.com/reuters/businessNews",
            "https://feeds.bloomberg.com/markets/news.rss",
            "https://www.ft.com/?format=rss",
        ],
        "post_cadence_hours": 6,
    },
    {
        "key": "ufo",
        "label": "Anomalous Intelligence",
        "style": "classified",
        "priority_rss": [
            "https://www.thedrive.com/the-war-zone/rss",
            "https://thedebrief.org/feed/",
            "https://www.mysterywire.com/feed/",
        ],
        "post_cadence_hours": 6,
    },
    {
        "key": "naval",
        "label": "Naval Operations",
        "style": "satellite",
        "priority_rss": [
            "https://news.usni.org/feed",
            "https://www.navalnews.com/feed/",
            "https://www.thedrive.com/the-war-zone/rss",
        ],
        "post_cadence_hours": 6,
    },
]

# Breaking news keywords that trigger immediate post (checked every 15 min)
BREAKING_KEYWORDS = [
    "breaking", "just in", "developing", "urgent",
    "missile strike", "cyber attack", "zero-day", "critical vulnerability",
    "naval incident", "explosion", "warship", "stealth", "ufo", "uap",
    "sanctions", "nuclear", "emergency declaration", "coup",
]

ANTHROPIC_CLIENT = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))


# ── RSS fetch ──────────────────────────────────────────────────────────────────
async def fetch_rss_headlines(urls: list[str], max_items: int = 8) -> list[dict]:
    """Fetch and parse RSS headlines using feedparser."""
    import feedparser
    headlines = []

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for url in urls:
            try:
                resp = await client.get(url, headers={"User-Agent": "SitRepIntel/1.0"})
                if resp.status_code != 200:
                    continue
                feed = feedparser.parse(resp.text)
                for entry in feed.entries[:max_items]:
                    title = entry.get("title", "").strip()
                    desc = entry.get("summary", entry.get("description", "")).strip()
                    link = entry.get("link", "")
                    pub = entry.get("published", "")
                    if title:
                        headlines.append({
                            "title": title[:200],
                            "description": desc[:400],
                            "link": link,
                            "published": pub,
                            "source": url,
                        })
            except Exception as e:
                log.warning(f"RSS fetch failed for {url}: {e}")

    seen = set()
    unique = []
    for h in headlines:
        key = h["title"][:60].lower()
        if key not in seen:
            seen.add(key)
            unique.append(h)

    return unique[:max_items]

# ── Breaking news detector ─────────────────────────────────────────────────────
async def check_breaking_news() -> Optional[dict]:
    """
    Check all category feeds for breaking news keywords.
    Returns first breaking story found, or None.
    Tracks already-processed stories in Redis to avoid duplicates.
    """
    for cat in CATEGORIES:
        headlines = await fetch_rss_headlines(cat["priority_rss"], max_items=5)
        for h in headlines:
            text = (h["title"] + " " + h["description"]).lower()
            is_breaking = any(kw in text for kw in BREAKING_KEYWORDS)

            if is_breaking:
                # Check if we already processed this story
                story_hash = hashlib.md5(h["title"].encode()).hexdigest()[:12]
                redis_key = f"sitrep:processed:{story_hash}"

                if not r.exists(redis_key):
                    # Mark as processed for 24 hours
                    r.setex(redis_key, 86400, "1")
                    log.info(f"BREAKING detected [{cat['key']}]: {h['title'][:80]}")
                    return {
                        "category": cat["key"],
                        "style": cat["style"],
                        "headline": h,
                        "is_breaking": True,
                    }
    return None


# ── Brief generation ───────────────────────────────────────────────────────────
async def generate_scheduled_brief(category: dict) -> Optional[dict]:
    """
    Generate a full SitRep brief for a category using Claude.
    Returns structured brief dict or None on failure.
    """
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    headlines = await fetch_rss_headlines(category["priority_rss"], max_items=8)

    if not headlines:
        log.warning(f"No headlines for category {category['key']}")
        return None

    headlines_text = "\n".join(
        f"- {h['title']}: {h['description'][:150]}" for h in headlines
    )

    # Category-specific tone instructions
    tone_map = {
        "military": "Write in a military intelligence brief style. Use BLUF (Bottom Line Up Front). Be direct, operational, factual.",
        "cyber": "Write in a cyber threat intelligence style. Reference CVEs, TTPs, threat actors. Use technical but accessible language.",
        "political": "Write in a professional political analysis style. Neutral, authoritative, fact-based.",
        "economic": "Write in a financial intelligence style. Reference market impacts, sanctions effects, supply chain implications.",
        "ufo": "Write in a classified intelligence brief style with a TOP SECRET / SCI aesthetic. Build intrigue. Reference official sources like AARO, DoD, IC.",
        "naval": "Write in a naval operations intelligence style. Reference vessel classes, fleet movements, strait chokepoints.",
    }

    tone = tone_map.get(category["key"], "Write in a professional intelligence brief style.")

    system_prompt = f"""You are SitRep Intelligence, an AI-powered geopolitical and security intelligence platform.
Today is {today}. You ONLY report on events current as of {today}.
{tone}
Respond ONLY with valid JSON. No markdown, no preamble, no explanation."""

    user_prompt = f"""Generate a SitRep intelligence brief for category: {category['label']}

Latest headlines as of {today}:
{headlines_text}

Return this exact JSON structure:
{{
  "category": "{category['key']}",
  "label": "{category['label']}",
  "priority": "A1|A2|B1",
  "classification": "UNCLASSIFIED // FOR OFFICIAL USE ONLY",
  "headline": "Single punchy headline under 12 words",
  "hook": "One sentence that makes you need to know more",
  "talking_points": [
    {{"point": "Key finding 1", "impact": "So what — why this matters"}},
    {{"point": "Key finding 2", "impact": "So what — why this matters"}},
    {{"point": "Key finding 3", "impact": "So what — why this matters"}}
  ],
  "bottom_line": "The one thing viewers must take away",
  "source_tags": ["SOURCE1", "SOURCE2"],
  "coordinates": {{"lat": null, "lon": null}},
  "generated_at": "{today}"
}}

For coordinates: if the brief references a specific geographic location (a country, city, strait, base),
provide approximate lat/lon for the theater map. Otherwise null."""

    try:
        response = ANTHROPIC_CLIENT.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            messages=[{"role": "user", "content": user_prompt}],
            system=system_prompt,
        )

        raw = response.content[0].text.strip()
        # Strip any accidental markdown fences
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        brief = json.loads(raw)
        brief["video_style"] = category["style"]
        brief["brief_id"] = f"SR-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}-{category['key'].upper()}"
        brief["post_status"] = "pending"

        # Cache in Redis for 30 min
        cache_key = f"sitrep:brief:{category['key']}"
        r.setex(cache_key, 1800, json.dumps(brief))

        log.info(f"Brief generated: {brief['brief_id']} — {brief['headline'][:60]}")
        return brief

    except Exception as e:
        log.error(f"Brief generation failed for {category['key']}: {e}")
        return None


# ── Main scheduler entry points ────────────────────────────────────────────────
async def run_scheduled_cycle():
    """
    Called by Cloud Scheduler every 6 hours.
    Generates one brief per category and queues for video render + post.
    """
    log.info(f"=== SCHEDULED CYCLE START {datetime.now(timezone.utc).isoformat()} ===")
    results = []

    for cat in CATEGORIES:
        brief = await generate_scheduled_brief(cat)
        if brief:
            # Queue for video render
            r.lpush("sitrep:render_queue", json.dumps(brief))
            results.append({"category": cat["key"], "status": "queued", "id": brief["brief_id"]})
        else:
            results.append({"category": cat["key"], "status": "failed"})

    log.info(f"Cycle complete: {len([r for r in results if r['status'] == 'queued'])} briefs queued")
    return results


async def run_breaking_check():
    """
    Called by Cloud Scheduler every 15 minutes.
    Checks for breaking news and queues immediate post if found.
    """
    log.info("=== BREAKING NEWS CHECK ===")
    breaking = await check_breaking_news()

    if breaking:
        cat = next((c for c in CATEGORIES if c["key"] == breaking["category"]), CATEGORIES[0])
        brief = await generate_scheduled_brief(cat)

        if brief:
            brief["is_breaking"] = True
            brief["priority"] = "A1"
            brief["headline"] = f"BREAKING: {brief['headline']}"
            r.lpush("sitrep:render_queue", json.dumps(brief))
            log.info(f"Breaking brief queued: {brief['brief_id']}")
            return {"status": "breaking_queued", "id": brief["brief_id"]}

    return {"status": "no_breaking"}
