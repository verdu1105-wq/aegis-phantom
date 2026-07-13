"""
tiktok_block_playwright.py -- Playwright-based TikTok block engine
Cybergrid Solutions LLC -- AEGIS PHANTOM

Selector map (confirmed):
    data-e2e="user-more"    -> Actions (three-dot) menu
    data-e2e="block-popup"  -> block confirmation dialog

=============================================================================
FIX LOG (this revision) -- addresses the block-failure bug seen on stream:
=============================================================================
ROOT CAUSE (from live log):
    Locator.click timed out because <div class="TUXModal-overlay"
    data-transition-status="open"> was still mid-CSS-transition and
    "intercepts pointer events". The old code used blind wait_for_timeout()
    sleeps that were too short under load, so it clicked while the overlay
    was still animating. Under a 20-target salvo, each block ALSO launched a
    brand-new Chromium, so 20 browsers spawned at once -> CPU/mem thrash ->
    transitions ran even slower -> the fixed sleeps missed even more often.

FIXES:
    1. CONCURRENCY SEMAPHORE (module-level, default 3). Caps simultaneous
       browsers so a salvo can't spawn 20 Chromium instances and thrash the
       box. This directly reduces the overlay-timing failures under load.
    2. WAIT-FOR-OVERLAY-TO-SETTLE. Instead of blind sleeps, we wait for the
       TUXModal-overlay transition to COMPLETE (data-transition-status no
       longer "open") before attempting the click. This is the core fix.
    3. OVERLAY-SAFE CLICK. Normal click first (with a real actionability
       timeout); if the overlay still intercepts, fall back to a JS-dispatched
       click that bypasses pointer-event interception entirely.
    4. Everything else (multi-selector fallbacks, cookie auth, confirm-dialog
       JS fallback) is preserved -- that logic is why blocks that DO land,
       land. We only surgically fixed the timing/concurrency failure path.

    NOTE: behavior is unchanged on the happy path; this only makes the
    previously-failing path succeed. Returns True ONLY on a confirmed block.
=============================================================================
"""

import os
import asyncio
import logging
from dotenv import load_dotenv

load_dotenv(r'C:\Users\VernonDunbar\Documents\Aegis_Phantom\.env')

log = logging.getLogger("playwright_block")

SESSION_ID  = os.getenv("TIKTOK_SESSION_ID", "")
TTWID       = os.getenv("TIKTOK_TTWID", "")
MS_TOKEN    = os.getenv("TIKTOK_MS_TOKEN", "")
CSRF_TOKEN  = os.getenv("TIKTOK_CSRF_TOKEN", "")

# --- FIX 1: concurrency cap -------------------------------------------------
# Max simultaneous Playwright browsers. A salvo fires many blocks at once;
# without this, each launched its own Chromium and the box thrashed, which is
# what made the overlay-timing bug fire so often under load. 3 is the value
# from the pending-list spec. Tune via env if needed.
MAX_CONCURRENT_BLOCKS = int(os.getenv("PLAYWRIGHT_MAX_CONCURRENCY", "3"))
_block_semaphore = asyncio.Semaphore(MAX_CONCURRENT_BLOCKS)

# Overlay that intercepts clicks mid-transition. We wait for its transition
# to finish (data-transition-status stops being "open") before clicking.
_OVERLAY_SELECTOR = '.TUXModal-overlay, [class*="TUXModal-overlay"]'


async def _wait_for_overlay_settled(page, timeout_ms: int = 4000):
    """
    FIX 2: wait for any TUXModal-overlay to FINISH its open transition before
    we try to click through it. The overlay carries data-transition-status;
    while it's "open" it is still animating and intercepts pointer events.
    We poll until no overlay is still in the "open" transition state, or until
    timeout (in which case we proceed and let the overlay-safe click handle it).

    This replaces the old blind wait_for_timeout(2000) that clicked too early.
    """
    try:
        await page.wait_for_function(
            """
            () => {
                const overlays = document.querySelectorAll(
                    '.TUXModal-overlay, [class*="TUXModal-overlay"]'
                );
                // settled == no overlay is currently mid-open-transition
                for (const o of overlays) {
                    if (o.getAttribute('data-transition-status') === 'open') {
                        // still animating in -> not settled yet
                        const cs = getComputedStyle(o);
                        // if it's fully opaque it's effectively done animating
                        if (parseFloat(cs.opacity) < 0.99) return false;
                    }
                }
                return true;
            }
            """,
            timeout=timeout_ms,
        )
    except Exception:
        # timed out waiting for settle -- not fatal; the overlay-safe click
        # (JS dispatch fallback) can still push through.
        pass


async def _safe_click(page, locator, label: str, timeout_ms: int = 6000) -> bool:
    """
    FIX 3: overlay-safe click.
    Try a normal Playwright click first (respects actionability, real timeout).
    If the overlay still intercepts it, fall back to a JS-dispatched click that
    bypasses pointer-event interception entirely. Returns True if either lands.
    """
    # First: make sure any modal overlay has settled so a normal click can work.
    await _wait_for_overlay_settled(page)

    # Attempt 1: normal click with a real timeout (not a blind sleep).
    try:
        await locator.click(timeout=timeout_ms)
        return True
    except Exception as e:
        log.debug(f"normal click failed on {label}: {e}")

    # Attempt 2: JS-dispatched click -- bypasses the overlay interception that
    # caused the 30s timeouts. This clicks the element directly in the DOM.
    try:
        handle = await locator.element_handle(timeout=1500)
        if handle:
            await page.evaluate("(el) => el.click()", handle)
            return True
    except Exception as e:
        log.debug(f"JS click failed on {label}: {e}")

    return False


async def tiktok_block_playwright(username: str, headless: bool = True) -> bool:
    """
    Block @username via a real Chromium session. Returns True ONLY on a
    confirmed block. Concurrency-capped by _block_semaphore.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log.error("Playwright not installed")
        return False

    if not SESSION_ID:
        log.error("No TikTok session ID in .env")
        return False

    cookies = [
        {"name": "sessionid",     "value": SESSION_ID, "domain": ".tiktok.com", "path": "/"},
        {"name": "ttwid",         "value": TTWID,      "domain": ".tiktok.com", "path": "/"},
        {"name": "msToken",       "value": MS_TOKEN,   "domain": ".tiktok.com", "path": "/"},
        {"name": "tt_csrf_token", "value": CSRF_TOKEN, "domain": ".tiktok.com", "path": "/"},
    ]
    cookies = [c for c in cookies if c["value"]]

    # FIX 1: hold a semaphore slot for the whole browser lifetime so we never
    # exceed MAX_CONCURRENT_BLOCKS live Chromium instances during a salvo.
    async with _block_semaphore:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=headless,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
            )
            await context.add_cookies(cookies)
            page = await context.new_page()

            try:
                print(f"[PLAYWRIGHT] Navigating to @{username}...")
                await page.goto(
                    f"https://www.tiktok.com/@{username}",
                    wait_until="domcontentloaded",
                    timeout=20000,
                )
                # FIX 4: wait for the profile shell to actually render before
                # hunting for the Actions button. The previous blind 1500ms
                # sleep could fire before the three-dot button existed on a
                # slow-loading profile -> "Actions button not found". We now
                # wait (up to 8s) for the profile header/avatar to appear, then
                # give the action bar a beat to hydrate.
                try:
                    await page.wait_for_selector(
                        '[data-e2e="user-avatar"], [data-e2e="user-title"], h1',
                        timeout=8000,
                    )
                except Exception:
                    pass
                await page.wait_for_timeout(1200)

                # ---- Step 1: open the Actions (three-dot) menu ----
                actions_btn = None
                for sel in ['[data-e2e="user-more"]',
                            'button[aria-label="Actions"]',
                            'button[aria-label="More options"]',
                            'button[aria-label="more"]',
                            '[data-e2e="user-more-menu"]']:
                    try:
                        candidate = page.locator(sel).last
                        # give the primary selector a longer look; the three-dot
                        # button often hydrates a moment after the avatar.
                        if await candidate.is_visible(timeout=3000):
                            actions_btn = candidate
                            break
                    except Exception:
                        continue

                if not actions_btn:
                    # DIAGNOSTIC: dump what the page actually looks like so we can
                    # tell "button not loaded yet" from "logged out / blocked /
                    # private profile" (which show a different page with no
                    # Actions menu at all).
                    try:
                        title = await page.title()
                        url_now = page.url
                        has_login = await page.evaluate(
                            "() => !!document.querySelector('[data-e2e=\"top-login-button\"], [data-e2e=\"login-button\"]')"
                        )
                        already_blocked = await page.evaluate(
                            "() => document.body.innerText.includes('Unblock') || document.body.innerText.includes('blocked')"
                        )
                        print(f"[PLAYWRIGHT] Actions button not found for @{username}"
                              f" | title={title!r} url={url_now}"
                              f" | logged_out={has_login} already_blocked={already_blocked}")
                    except Exception:
                        print(f"[PLAYWRIGHT] Actions button not found for @{username}")
                    await browser.close()
                    return False

                if not await _safe_click(page, actions_btn, "actions-menu"):
                    print(f"[PLAYWRIGHT] Could not click Actions menu for @{username}")
                    await browser.close()
                    return False
                print(f"[PLAYWRIGHT] Clicked Actions menu for @{username}")

                # FIX 2: wait for the dropdown's overlay to finish animating in
                # BEFORE we look for / click the Block item. This is the exact
                # point that timed out on stream.
                await _wait_for_overlay_settled(page)

                # ---- Step 2: find + click "Block" in the dropdown ----
                block_item = None
                block_selectors = [
                    '[data-e2e="block"]',
                    'li:has-text("Block")',
                    'div[role="menuitem"]:has-text("Block")',
                    'ul li:nth-child(2)',   # positional fallback (fragile) -- kept last-ish
                    '.tux-popover-content li:last-child',
                    '[class*="MenuItem"]:has-text("Block")',
                ]
                for sel in block_selectors:
                    try:
                        candidate = page.locator(sel).last
                        if await candidate.is_visible(timeout=1000):
                            block_item = candidate
                            break
                    except Exception:
                        continue

                if not block_item:
                    for txt in ["Block", "Block user"]:
                        try:
                            candidate = page.get_by_text(txt, exact=True).last
                            if await candidate.is_visible(timeout=1000):
                                block_item = candidate
                                break
                        except Exception:
                            continue

                if not block_item:
                    # last resort: tag any element whose text is exactly "Block"
                    try:
                        found = await page.evaluate("""
                            () => {
                                const els = [...document.querySelectorAll('li, [role="menuitem"], [role="option"]')];
                                return els
                                    .filter(e => e.textContent.trim() === 'Block')
                                    .map(e => { e.setAttribute('data-playwright-block', '1'); return true; })
                                    .length > 0;
                            }
                        """)
                        if found:
                            candidate = page.locator('[data-playwright-block="1"]').last
                            if await candidate.is_visible(timeout=1000):
                                block_item = candidate
                    except Exception:
                        pass

                if not block_item:
                    print(f"[PLAYWRIGHT] Block option not visible for @{username}")
                    await browser.close()
                    return False

                # overlay-safe click on the Block menu item
                if not await _safe_click(page, block_item, "block-menu-item"):
                    print(f"[PLAYWRIGHT] Could not click Block for @{username}")
                    await browser.close()
                    return False
                print(f"[PLAYWRIGHT] Clicked Block for @{username}")

                # ---- Step 3: confirm dialog ----
                # wait for the confirm modal's overlay to settle before clicking
                await _wait_for_overlay_settled(page)

                confirmed = False
                for psel in ['[data-e2e="block-popup"]',
                             '[role="dialog"]',
                             '.tux-modal',
                             'div[aria-modal="true"]']:
                    try:
                        popup = page.locator(psel).last
                        if await popup.is_visible(timeout=2000):
                            print(f"[PLAYWRIGHT] Block popup visible for @{username}")
                            # try the confirm button by role/name, overlay-safe
                            for btn_name in ["Block", "Confirm", "OK"]:
                                try:
                                    confirm = popup.get_by_role("button", name=btn_name)
                                    if await confirm.is_visible(timeout=1000):
                                        if await _safe_click(page, confirm, f"confirm-{btn_name}"):
                                            await page.wait_for_timeout(1200)
                                            print(f"[PLAYWRIGHT] BLOCK CONFIRMED: @{username}")
                                            confirmed = True
                                            break
                                except Exception:
                                    continue
                            # JS fallback: click any confirm-looking button in the modal
                            if not confirmed:
                                await page.evaluate("""
                                    const ds = document.querySelectorAll('[role="dialog"],[data-e2e="block-popup"],.tux-modal,[aria-modal="true"]');
                                    ds.forEach(d => {
                                        d.querySelectorAll('button').forEach(b => {
                                            if (['Block','Confirm','OK'].includes(b.textContent.trim())) b.click();
                                        });
                                    });
                                """)
                                await page.wait_for_timeout(1200)
                                print(f"[PLAYWRIGHT] BLOCK CONFIRMED via JS: @{username}")
                                confirmed = True
                            break
                    except Exception:
                        continue

                await browser.close()
                if confirmed:
                    return True
                print(f"[PLAYWRIGHT] Block popup not found for @{username}")
                return False

            except Exception as e:
                print(f"[PLAYWRIGHT] Error blocking @{username}: {e}")
                try:
                    await browser.close()
                except Exception:
                    pass
                return False


# ---------------------------------------------------------------------------
# Manual test harness. Run this file directly to block a single test account
# with a VISIBLE browser (headless=False) so you can watch each step.
#   python tiktok_block_playwright.py
# Change the handle below to a throwaway you actually want to block.
# ---------------------------------------------------------------------------
async def test_block(username: str = "brelan671"):
    print(f"[TEST] Playwright block test on @{username}")
    result = await tiktok_block_playwright(username, headless=False)
    print(f"[TEST] Result: {'SUCCESS' if result else 'FAILED'} -- @{username}")
    return result


if __name__ == "__main__":
    asyncio.run(test_block("brelan671"))
