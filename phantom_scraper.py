#!/usr/bin/env python3
"""
AEGIS PHANTOM — Follower Audit Scraper
Cybergrid Solutions LLC

Scrapes a TikTok account's follower list, scores each account
for bot/goon indicators, writes results to Redis vault, and
exports a review CSV for manual block decisions.

NO AUTO-BLOCK. Jess reviews and approves before any action.
"""

import json
import time
import random
import asyncio
import logging
import argparse
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import redis
import pandas as pd

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("phantom_scraper")

# ── Config ────────────────────────────────────────────────────────────────────
REDIS_HOST     = "localhost"          # swap with Redis Cloud host when deployed
REDIS_PORT     = 6379
REDIS_PASSWORD = None                 # set via env var REDIS_PASSWORD in prod
REDIS_DB       = 0

VAULT_PREFIX   = "follower"           # keys: follower:{uid}
GOON_PREFIX    = "goon"               # existing goon vault keys: goon:{uid}

OUTPUT_DIR     = Path("./phantom_output")

# Scoring thresholds
SCORE_CLEAN    = 0.35
SCORE_WATCH    = 0.65
# anything above SCORE_WATCH = GOON (block candidate)

# Known hostile keywords for bio scanning
HOSTILE_KEYWORDS = [
    "army", "barbee", "armybarbee", "goon", "squad", "troll",
    "hater", "exposing", "fake", "fraud", "liar", "drama",
    "devildog", "devil_dog", "hrgator", "badgwell"
]

# Suspicious bio patterns
SUSPICIOUS_PATTERNS = [
    "follow for follow", "f4f", "follow back", "gain followers",
    "i expose", "truth about", "real story"
]

# ── Redis Connection ──────────────────────────────────────────────────────────
def get_redis():
    import os
    password = os.environ.get("REDIS_PASSWORD", REDIS_PASSWORD)
    r = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=password,
        db=REDIS_DB,
        decode_responses=True
    )
    try:
        r.ping()
        log.info("Redis vault connected.")
    except redis.ConnectionError:
        log.warning("Redis not reachable — running in CSV-only mode.")
        return None
    return r

# ── Risk Scoring ──────────────────────────────────────────────────────────────
def score_account(account: dict, r: redis.Redis | None) -> tuple[float, list[str]]:
    """
    Returns (score: float 0.0-1.0, flags: list[str])
    Higher score = more suspicious.
    """
    score = 0.0
    flags = []

    following   = account.get("following_count", 0)
    followers   = account.get("follower_count", 0)
    post_count  = account.get("post_count", 0)
    bio         = (account.get("bio") or "").lower()
    username    = (account.get("username") or "").lower()
    has_avatar  = account.get("has_avatar", True)
    uid         = account.get("uid", "")

    # 1. Vault cross-match — already known goon
    if r:
        goon_key = f"{GOON_PREFIX}:{uid}"
        if r.exists(goon_key):
            score += 0.80
            flags.append("VAULT_HIT")

    # 2. Following >> followers (classic bot/burner ratio)
    if followers == 0 and following > 50:
        score += 0.30
        flags.append("ZERO_FOLLOWERS_HIGH_FOLLOWING")
    elif followers > 0:
        ratio = following / followers
        if ratio > 20:
            score += 0.25
            flags.append(f"HIGH_RATIO_{ratio:.0f}x")
        elif ratio > 10:
            score += 0.10
            flags.append(f"ELEVATED_RATIO_{ratio:.0f}x")

    # 3. No posts
    if post_count == 0:
        score += 0.15
        flags.append("NO_POSTS")
    elif post_count < 3:
        score += 0.05
        flags.append("FEW_POSTS")

    # 4. Default/no avatar
    if not has_avatar:
        score += 0.10
        flags.append("NO_AVATAR")

    # 5. Bio hostile keyword match
    for kw in HOSTILE_KEYWORDS:
        if kw in bio or kw in username:
            score += 0.25
            flags.append(f"KEYWORD:{kw.upper()}")
            break  # one flag per keyword category

    # 6. Suspicious bio patterns
    for pat in SUSPICIOUS_PATTERNS:
        if pat in bio:
            score += 0.15
            flags.append(f"PATTERN:{pat.replace(' ','_').upper()}")
            break

    # 7. Username entropy — lots of numbers = likely burner
    digits = sum(c.isdigit() for c in username)
    if len(username) > 0 and digits / len(username) > 0.5:
        score += 0.10
        flags.append("HIGH_DIGIT_USERNAME")

    # Clamp to 1.0
    score = min(score, 1.0)
    return round(score, 3), flags


def classify(score: float) -> str:
    if score < SCORE_CLEAN:
        return "CLEAN"
    elif score < SCORE_WATCH:
        return "WATCH"
    else:
        return "GOON"


# ── Vault Write ───────────────────────────────────────────────────────────────
def vault_write(r: redis.Redis, account: dict, score: float, flags: list, status: str):
    uid = account.get("uid", "")
    if not uid:
        return
    key = f"{VAULT_PREFIX}:{uid}"
    payload = {
        "username":   account.get("username", ""),
        "uid":        uid,
        "score":      score,
        "status":     status,
        "flags":      json.dumps(flags),
        "followers":  account.get("follower_count", 0),
        "following":  account.get("following_count", 0),
        "posts":      account.get("post_count", 0),
        "bio":        account.get("bio", "")[:200],
        "seen_at":    datetime.now(timezone.utc).isoformat(),
        "source":     "follower_audit"
    }
    r.hset(key, mapping=payload)
    r.expire(key, 60 * 60 * 24 * 90)  # 90 day TTL


# ── Mock Scraper (Playwright stub) ────────────────────────────────────────────
async def scrape_followers_mock(target_username: str, max_followers: int = 200) -> list[dict]:
    """
    MOCK — returns synthetic data for testing.
    Replace with real Playwright implementation below once
    TikTok Developer API is approved (use official endpoints).
    """
    log.info(f"[MOCK] Simulating follower scrape for @{target_username}")
    await asyncio.sleep(0.5)

    mock_accounts = []
    patterns = [
        # (username_tmpl, followers, following, posts, has_avatar, bio)
        ("regular_user_{}", 1200, 400, 45, True, "Just here for the vibes"),
        ("user{:08d}", 0, 850, 0, False, ""),          # classic burner
        ("fan_of_jess_{}", 340, 120, 22, True, "Love this creator!"),
        ("troll_acc_{:05d}", 2, 999, 0, False, "exposing fake people"),
        ("army_backup_{}", 1, 400, 0, False, "armybarbee backup"),
        ("goodfollower_{}", 890, 300, 67, True, "Cooking & lifestyle"),
        ("burner{:06d}", 0, 200, 0, False, "follow for follow"),
        ("viewer_{}", 55, 90, 8, True, "just watching"),
    ]

    for i in range(min(max_followers, 80)):
        tmpl, fol, fing, posts, avatar, bio = random.choice(patterns)
        username = tmpl.format(random.randint(1000, 99999))
        uid = hashlib.md5(username.encode()).hexdigest()[:16]
        mock_accounts.append({
            "username":        username,
            "uid":             uid,
            "follower_count":  fol + random.randint(-10, 10),
            "following_count": fing + random.randint(-5, 5),
            "post_count":      posts,
            "has_avatar":      avatar,
            "bio":             bio,
        })
        if i % 20 == 0 and i > 0:
            log.info(f"  Scraped {i} followers...")
        await asyncio.sleep(0.01)

    log.info(f"[MOCK] Scraped {len(mock_accounts)} accounts.")
    return mock_accounts


async def scrape_followers_live(target_username: str, max_followers: int = 500) -> list[dict]:
    """
    LIVE Playwright scraper — paginates TikTok follower list.
    Requires: pip install playwright playwright-stealth
              playwright install chromium

    NOTE: Switch to official TikTok API endpoints once developer
    app is approved — cleaner, more stable, legally defensible.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log.error("Playwright not installed. Run: pip install playwright && playwright install chromium")
        return []

    accounts = []
    url = f"https://www.tiktok.com/@{target_username}/followers"

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800}
        )
        page = await ctx.new_page()

        log.info(f"Navigating to {url}")
        await page.goto(url, wait_until="networkidle")
        await asyncio.sleep(random.uniform(2, 4))

        # Scroll and collect follower cards
        prev_count = 0
        stall_count = 0

        while len(accounts) < max_followers:
            # Try to extract follower items from the DOM
            cards = await page.query_selector_all('[data-e2e="followers-item"]')

            for card in cards[len(accounts):]:
                try:
                    username_el = await card.query_selector('[data-e2e="followers-item-username"]')
                    uid_attr    = await card.get_attribute("data-user-id")
                    username    = await username_el.inner_text() if username_el else ""

                    accounts.append({
                        "username":        username.strip().lstrip("@"),
                        "uid":             uid_attr or "",
                        "follower_count":  0,   # requires profile fetch — skip for speed
                        "following_count": 0,
                        "post_count":      0,
                        "has_avatar":      True,
                        "bio":             "",
                    })
                except Exception:
                    pass

            if len(accounts) == prev_count:
                stall_count += 1
                if stall_count > 5:
                    log.info("No new followers detected — end of list or rate limit.")
                    break
            else:
                stall_count = 0

            prev_count = len(accounts)
            log.info(f"Collected {len(accounts)} followers so far...")

            # Scroll down
            await page.evaluate("window.scrollBy(0, 800)")
            await asyncio.sleep(random.uniform(1.2, 2.8))

        await browser.close()

    log.info(f"Live scrape complete: {len(accounts)} accounts collected.")
    return accounts


# ── Main Pipeline ─────────────────────────────────────────────────────────────
async def run(target: str, max_followers: int, live_mode: bool):
    OUTPUT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    r = get_redis()

    # 1. Scrape
    log.info(f"=== AEGIS PHANTOM — Follower Audit ===")
    log.info(f"Target: @{target} | Max: {max_followers} | Mode: {'LIVE' if live_mode else 'MOCK'}")
    log.info("NO AUTO-BLOCK — manual review required before any action.")
    log.info("")

    if live_mode:
        accounts = await scrape_followers_live(target, max_followers)
    else:
        accounts = await scrape_followers_mock(target, max_followers)

    if not accounts:
        log.error("No accounts returned. Exiting.")
        return

    # 2. Score and classify
    log.info(f"\nScoring {len(accounts)} accounts...")
    results = []

    for acc in accounts:
        score, flags = score_account(acc, r)
        status = classify(score)

        if r and status in ("GOON", "WATCH"):
            vault_write(r, acc, score, flags, status)

        results.append({
            "username":   acc.get("username", ""),
            "uid":        acc.get("uid", ""),
            "score":      score,
            "status":     status,
            "flags":      ", ".join(flags) if flags else "—",
            "followers":  acc.get("follower_count", 0),
            "following":  acc.get("following_count", 0),
            "posts":      acc.get("post_count", 0),
            "bio":        acc.get("bio", "")[:80],
        })

    # 3. Stats
    df = pd.DataFrame(results)
    clean_ct = len(df[df.status == "CLEAN"])
    watch_ct = len(df[df.status == "WATCH"])
    goon_ct  = len(df[df.status == "GOON"])

    log.info("")
    log.info("=== AUDIT RESULTS ===")
    log.info(f"  Total scanned : {len(df)}")
    log.info(f"  ✅ CLEAN       : {clean_ct}")
    log.info(f"  ⚠️  WATCH       : {watch_ct}")
    log.info(f"  🔴 GOON        : {goon_ct}")
    log.info("")

    # 4. Export CSVs
    full_path  = OUTPUT_DIR / f"audit_full_{target}_{timestamp}.csv"
    goon_path  = OUTPUT_DIR / f"audit_GOONS_{target}_{timestamp}.csv"
    watch_path = OUTPUT_DIR / f"audit_WATCH_{target}_{timestamp}.csv"

    df.to_csv(full_path, index=False)
    df[df.status == "GOON"].to_csv(goon_path, index=False)
    df[df.status == "WATCH"].to_csv(watch_path, index=False)

    log.info(f"Full report  → {full_path}")
    log.info(f"Goon list    → {goon_path}   ({goon_ct} accounts — PENDING JESS REVIEW)")
    log.info(f"Watch list   → {watch_path}  ({watch_ct} accounts)")
    log.info("")
    log.info("⚠️  REMINDER: No blocks executed. Send goon CSV to Jess for manual review.")
    log.info("=== AUDIT COMPLETE ===")

    # 5. Print top goons
    top_goons = df[df.status == "GOON"].sort_values("score", ascending=False).head(10)
    if not top_goons.empty:
        log.info("\nTop flagged accounts:")
        for _, row in top_goons.iterrows():
            log.info(f"  @{row.username:<30} score={row.score:.2f}  flags={row.flags}")


# ── Entry Point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AEGIS PHANTOM — Follower Audit")
    parser.add_argument("target",         help="TikTok username to audit (without @)")
    parser.add_argument("--max",          type=int, default=500, help="Max followers to scrape")
    parser.add_argument("--live",         action="store_true",   help="Use live Playwright scraper (default: mock)")
    args = parser.parse_args()

    asyncio.run(run(args.target, args.max, args.live))
