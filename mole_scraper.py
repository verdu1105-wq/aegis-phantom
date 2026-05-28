"""
AEGIS PHANTOM — FOLLOWER LIST SCRAPER
mole_scraper.py

Scrapes TikTok follower/following lists from a target profile
Scores each account using burner detection algorithm
Auto-submits confirmed burners to AEGIS vault

Usage:
  python mole_scraper.py --target ellispine --list followers
  python mole_scraper.py --target ellispine --list following
  python mole_scraper.py --target ellispine --list both
  python mole_scraper.py --target ellispine --list followers --auto-vault
  python mole_scraper.py --test
"""

import asyncio
import os
import json
import httpx
import logging
import argparse
import time
import random
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── CONFIG ────────────────────────────────────────────────────────────────────
CLOUD_URL   = os.getenv("CLOUD_URL", "https://aegis-cwis-974184310088.us-east1.run.app")
AUTH_DATA   = {"username": "d4rkn8t", "password": "aegis2026d4rk"}
SESSION_ID  = os.getenv("TIKTOK_SESSION_ID", "")
OUTPUT_DIR  = Path("scraper_results")
OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("mole_scraper.log"),
        logging.StreamHandler()
    ]
)

# ── BURNER SCORING ALGORITHM ──────────────────────────────────────────────────
def score_account(username, following, followers, likes, verified=False):
    """
    Score an account for burner probability.
    Returns: (score 0-100, verdict, reasons)
    """
    score = 0
    reasons = []

    # Zero likes = strong burner indicator
    if likes == 0:
        score += 35
        reasons.append("ZERO LIKES")
    elif likes < 10:
        score += 20
        reasons.append(f"VERY LOW LIKES ({likes})")

    # High following / low followers ratio
    if followers == 0:
        score += 30
        reasons.append("ZERO FOLLOWERS")
    elif following > 0 and followers > 0:
        ratio = following / followers
        if ratio > 10:
            score += 30
            reasons.append(f"EXTREME RATIO {ratio:.0f}:1")
        elif ratio > 5:
            score += 20
            reasons.append(f"HIGH RATIO {ratio:.0f}:1")
        elif ratio > 3:
            score += 10
            reasons.append(f"ELEVATED RATIO {ratio:.0f}:1")

    # Very high following count with no content
    if following > 1000 and likes == 0:
        score += 15
        reasons.append("MASS FOLLOW / NO CONTENT")
    elif following > 500 and likes < 5:
        score += 10
        reasons.append("HIGH FOLLOW / MINIMAL CONTENT")

    # Username patterns
    import re
    u = username.lower()

    # EE network patterns
    if re.search(r'e{2,}', u):
        score += 20
        reasons.append("EE PATTERN IN USERNAME")
    if re.search(r'\.\.[a-z0-9]', u):
        score += 25
        reasons.append("DOUBLE DOT PATTERN")
    if re.match(r'^e[a-z]+\d+$', u):
        score += 15
        reasons.append("E+WORD+NUMBERS PATTERN")

    # Generic burner username patterns
    if re.match(r'^user\d+$', u):
        score += 20
        reasons.append("GENERIC USER+NUMBERS USERNAME")
    if re.match(r'^[a-z]+\d{6,}$', u):
        score += 15
        reasons.append("WORD+LONG NUMBERS USERNAME")
    if re.search(r'\d{8,}', u):
        score += 10
        reasons.append("LONG NUMBER STRING IN USERNAME")

    # Very low followers
    if followers < 10:
        score += 15
        reasons.append(f"VERY LOW FOLLOWERS ({followers})")
    elif followers < 50:
        score += 5
        reasons.append(f"LOW FOLLOWERS ({followers})")

    # Verified accounts are not burners
    if verified:
        score = 0
        reasons = ["VERIFIED ACCOUNT"]

    # Cap at 100
    score = min(score, 100)

    # Verdict
    if score >= 70:
        verdict = "CONFIRMED_BURNER"
    elif score >= 45:
        verdict = "PROBABLE_BURNER"
    elif score >= 25:
        verdict = "SUSPICIOUS"
    else:
        verdict = "LIKELY_LEGIT"

    return score, verdict, reasons


# ── AEGIS API ─────────────────────────────────────────────────────────────────
class AEGISClient:
    def __init__(self):
        self.token = None

    async def login(self):
        async with httpx.AsyncClient(timeout=10) as http:
            try:
                resp = await http.post(f"{CLOUD_URL}/api/auth/login", json=AUTH_DATA)
                resp.raise_for_status()
                self.token = resp.json().get("token")
                logging.info("AEGIS authenticated")
                return True
            except Exception as e:
                logging.error(f"AEGIS login failed: {e}")
                return False

    async def add_to_vault(self, username, reason="scraper"):
        if not self.token:
            return False
        async with httpx.AsyncClient(timeout=10) as http:
            try:
                resp = await http.post(
                    f"{CLOUD_URL}/api/goons",
                    json={"username": username, "reason": reason},
                    headers={"Authorization": f"Bearer {self.token}"}
                )
                return resp.status_code == 200
            except Exception:
                return False

    async def check_vault(self, username):
        """Check if username is already in vault."""
        if not self.token:
            return False
        async with httpx.AsyncClient(timeout=10) as http:
            try:
                resp = await http.get(
                    f"{CLOUD_URL}/api/goons",
                    headers={"Authorization": f"Bearer {self.token}"}
                )
                data = resp.json()
                goons = data.get("goons", [])
                return username.lower() in [g.lower() for g in goons]
            except Exception:
                return False

    async def submit_scraper_report(self, target, results):
        """Submit full scraper report to MOLE reports endpoint."""
        if not self.token:
            return
        burners = [r for r in results if r["verdict"] in ["CONFIRMED_BURNER", "PROBABLE_BURNER"]]
        usernames = [r["username"] for r in burners]
        report = {
            "agent":           AUTH_DATA["username"],
            "target_stream":   target,
            "trigger_keyword": "FOLLOWER_SCRAPE",
            "context":         f"Follower list scrape of @{target}. {len(results)} accounts analyzed. {len(burners)} burners identified.",
            "usernames":       usernames,
            "timestamp":       datetime.utcnow().isoformat()
        }
        async with httpx.AsyncClient(timeout=10) as http:
            try:
                await http.post(f"{CLOUD_URL}/api/mole/report", json=report)
                logging.info(f"Scraper report submitted — {len(burners)} burners")
            except Exception as e:
                logging.error(f"Report submission failed: {e}")


# ── TIKTOK SCRAPER ────────────────────────────────────────────────────────────
class TikTokScraper:
    def __init__(self):
        self.aegis = AEGISClient()

    async def scrape_with_playwright(self, target, list_type="followers", auto_vault=False):
        """
        Scrape follower/following list using Playwright browser automation.
        list_type: 'followers', 'following', or 'both'
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logging.error("Playwright not installed. Run: pip install playwright && playwright install chromium")
            return []

        results = []
        url_map = {
            "followers": f"https://www.tiktok.com/@{target}/followers",
            "following": f"https://www.tiktok.com/@{target}/following"
        }

        lists_to_scrape = ["followers", "following"] if list_type == "both" else [list_type]

        async with async_playwright() as p:
            # Launch browser — headless=False so you can see what's happening
            browser = await p.chromium.launch(
                headless=False,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
            )

            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="en-US"
            )

            # Inject session cookie for burner account
            if SESSION_ID:
                await context.add_cookies([{
                    "name": "sessionid",
                    "value": SESSION_ID,
                    "domain": ".tiktok.com",
                    "path": "/"
                }])
                logging.info("Session cookie injected")

            page = await context.new_page()

            # Hide automation markers
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
            """)

            for list_name in lists_to_scrape:
                logging.info(f"Scraping {list_name} of @{target}...")
                list_results = []

                try:
                    await page.goto(url_map[list_name], wait_until="networkidle", timeout=15000)
                    await asyncio.sleep(random.uniform(2, 4))

                    # Scroll to load accounts
                    accounts_found = set()
                    no_new_count = 0
                    scroll_count = 0
                    max_scrolls = 50  # Adjust for deeper scrapes

                    while scroll_count < max_scrolls and no_new_count < 5:
                        # Extract account data from page
                        accounts = await page.evaluate("""
                            () => {
                                const items = [];
                                // TikTok follower list items
                                const selectors = [
                                    '[data-e2e="followers-item"]',
                                    '[data-e2e="following-item"]',
                                    '.tiktok-1g04lal-DivUserListContainer > div',
                                    '[class*="UserCard"]',
                                    '[class*="user-item"]'
                                ];
                                
                                for (const sel of selectors) {
                                    const els = document.querySelectorAll(sel);
                                    if (els.length > 0) {
                                        els.forEach(el => {
                                            const usernameEl = el.querySelector('[data-e2e="user-card-nickname"]') ||
                                                               el.querySelector('[class*="nickname"]') ||
                                                               el.querySelector('a[href*="/@"]');
                                            const statsEls = el.querySelectorAll('[class*="count"]');
                                            
                                            let username = '';
                                            if (usernameEl) {
                                                const href = usernameEl.getAttribute('href') || '';
                                                username = href.replace('/@', '').split('?')[0] ||
                                                           usernameEl.textContent.replace('@','').trim();
                                            }
                                            
                                            if (username) {
                                                items.push({
                                                    username: username,
                                                    stats_text: el.innerText || ''
                                                });
                                            }
                                        });
                                        break;
                                    }
                                }
                                return items;
                            }
                        """)

                        new_found = 0
                        for acc in accounts:
                            uname = acc["username"].strip().lstrip("@")
                            if uname and uname not in accounts_found:
                                accounts_found.add(uname)
                                new_found += 1

                                # Parse stats from text
                                stats_text = acc.get("stats_text", "")
                                following, followers, likes = parse_stats_from_text(stats_text, uname)

                                score, verdict, reasons = score_account(uname, following, followers, likes)

                                result = {
                                    "username":  uname,
                                    "following": following,
                                    "followers": followers,
                                    "likes":     likes,
                                    "score":     score,
                                    "verdict":   verdict,
                                    "reasons":   reasons,
                                    "list_type": list_name,
                                    "target":    target,
                                    "scraped_at": datetime.utcnow().isoformat()
                                }
                                list_results.append(result)

                                log_color = "🔴" if verdict == "CONFIRMED_BURNER" else "🟡" if verdict == "PROBABLE_BURNER" else "⚪"
                                logging.info(f"{log_color} @{uname} — Score: {score} — {verdict}")

                        if new_found == 0:
                            no_new_count += 1
                        else:
                            no_new_count = 0

                        # Scroll down
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await asyncio.sleep(random.uniform(1.5, 3.0))
                        scroll_count += 1

                        logging.info(f"Scroll {scroll_count}/{max_scrolls} — {len(accounts_found)} accounts found")

                    logging.info(f"Completed {list_name} scrape: {len(list_results)} accounts")
                    results.extend(list_results)

                except Exception as e:
                    logging.error(f"Error scraping {list_name}: {e}")

            await browser.close()

        return results

    async def run(self, target, list_type, auto_vault):
        """Main scraper run."""
        logging.info(f"AEGIS MOLE SCRAPER starting — target: @{target} — list: {list_type}")

        # Login to AEGIS
        await self.aegis.login()

        # Scrape
        results = await self.scrape_with_playwright(target, list_type, auto_vault)

        if not results:
            logging.warning("No results found.")
            return

        # Sort by score
        results.sort(key=lambda x: x["score"], reverse=True)

        # Summary
        confirmed = [r for r in results if r["verdict"] == "CONFIRMED_BURNER"]
        probable  = [r for r in results if r["verdict"] == "PROBABLE_BURNER"]
        suspicious = [r for r in results if r["verdict"] == "SUSPICIOUS"]

        logging.info(f"\n{'='*50}")
        logging.info(f"SCRAPE COMPLETE: @{target}")
        logging.info(f"Total accounts: {len(results)}")
        logging.info(f"Confirmed burners: {len(confirmed)}")
        logging.info(f"Probable burners:  {len(probable)}")
        logging.info(f"Suspicious:        {len(suspicious)}")
        logging.info(f"{'='*50}")

        # Save results
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        outfile = OUTPUT_DIR / f"scrape_{target}_{list_type}_{timestamp}.json"
        with open(outfile, "w") as f:
            json.dump({
                "target":    target,
                "list_type": list_type,
                "scraped_at": timestamp,
                "summary": {
                    "total":     len(results),
                    "confirmed": len(confirmed),
                    "probable":  len(probable),
                    "suspicious": len(suspicious)
                },
                "results": results
            }, f, indent=2)
        logging.info(f"Results saved: {outfile}")

        # Auto-vault confirmed + probable burners
        if auto_vault:
            to_vault = confirmed + probable
            logging.info(f"Auto-vaulting {len(to_vault)} accounts...")
            vaulted = 0
            for acc in to_vault:
                success = await self.aegis.add_to_vault(
                    acc["username"],
                    reason=f"scraper:{acc['verdict']}:score{acc['score']}"
                )
                if success:
                    vaulted += 1
                    logging.info(f"  ☠ Vaulted @{acc['username']} (score: {acc['score']})")
                await asyncio.sleep(random.uniform(0.3, 0.8))  # Rate limit
            logging.info(f"Vaulted {vaulted}/{len(to_vault)} accounts")

        # Submit report to AEGIS
        await self.aegis.submit_scraper_report(target, results)

        # Print top threats
        print(f"\n{'='*60}")
        print(f"TOP THREATS FROM @{target} {list_type.upper()}")
        print(f"{'='*60}")
        for r in results[:20]:
            if r["score"] >= 25:
                print(f"{'🔴' if r['verdict']=='CONFIRMED_BURNER' else '🟡' if r['verdict']=='PROBABLE_BURNER' else '⚠️'} "
                      f"@{r['username']:<30} Score:{r['score']:>3} | {' | '.join(r['reasons'][:2])}")

        return results


def parse_stats_from_text(text, username):
    """Parse following/followers/likes from account text block."""
    import re
    nums = re.findall(r'[\d,]+(?:\.\d+)?[KkMm]?', text)

    def to_int(s):
        s = s.replace(',', '')
        if s.endswith(('K', 'k')):
            return int(float(s[:-1]) * 1000)
        if s.endswith(('M', 'm')):
            return int(float(s[:-1]) * 1000000)
        try:
            return int(s)
        except:
            return 0

    vals = [to_int(n) for n in nums[:3]]
    while len(vals) < 3:
        vals.append(0)
    return vals[0], vals[1], vals[2]


# ── CLI ────────────────────────────────────────────────────────────────────────
async def main():
    parser = argparse.ArgumentParser(description="AEGIS MOLE Follower Scraper")
    parser.add_argument("--target",     required=False, help="TikTok username to scrape")
    parser.add_argument("--list",       default="followers", choices=["followers","following","both"])
    parser.add_argument("--auto-vault", action="store_true", help="Auto-add burners to AEGIS vault")
    parser.add_argument("--test",       action="store_true", help="Test AEGIS connection")
    parser.add_argument("--score",      help="Score a single account: --score username,following,followers,likes")
    args = parser.parse_args()

    # Test mode
    if args.test:
        client = AEGISClient()
        success = await client.login()
        print("AEGIS connection: OK" if success else "AEGIS connection: FAILED")
        return

    # Score single account
    if args.score:
        parts = args.score.split(",")
        if len(parts) >= 4:
            uname = parts[0]
            following, followers, likes = int(parts[1]), int(parts[2]), int(parts[3])
            score, verdict, reasons = score_account(uname, following, followers, likes)
            print(f"\n@{uname}")
            print(f"Score:   {score}/100")
            print(f"Verdict: {verdict}")
            print(f"Reasons: {', '.join(reasons)}")
        return

    if not args.target:
        parser.print_help()
        return

    scraper = TikTokScraper()
    await scraper.run(
        target=args.target.lstrip("@"),
        list_type=args.list,
        auto_vault=args.auto_vault
    )


if __name__ == "__main__":
    asyncio.run(main())
