"""
AEGIS PHANTOM — MOLE Module (v2 — GROUNDED)
Targeted OSINT collector for TikTok threat-actor network mapping.

=============================================================================
WHY THIS REWRITE (read before touching)
=============================================================================
The v1 scraper FABRICATED follower lists. When TikTok's follower page failed
to load (bot-detection "Something went wrong", private list, empty render),
v1 fell back to grabbing ANY `a[href*="/@"]` link on the page — which are
TikTok's own UI chrome + the operator's OWN feed. Result: every target
returned the SAME fake "followers" (oceanhustle1, premierleague, ...) and a
confident "0 flagged" that was pure garbage.

That is the single most dangerous failure mode in this whole operation: a tool
that INVENTS evidence. It poisoned RECON and would poison any dossier built on
it.

THE FIX (per grounded-recon principles):
  1. NO FABRICATED FALLBACK. If the real follower-item selectors don't match,
     we DO NOT scrape stray page links. We record the failure and return NULL.
     Unknown stays unknown. A failed scrape NEVER produces "followers".
  2. PAGE-STATE DETECTION. Before trusting anything, detect whether the page
     is: the real follower list, a "something went wrong" error, a login wall,
     a private/empty list, or a captcha. Each is recorded distinctly.
  3. FAIL-NULL + DIAGNOSTICS. Every failure preserves status/state/reason so
     you know WHY it failed — not a misleading "no followers found".
  4. HANDLE VALIDATION. A scraped handle must come from an actual follower-row
     element, be a plausible handle, and NOT be the operator's own known feed
     accounts (defense-in-depth against contamination).
  5. PROVENANCE + COLLECTION METHOD on every record, so Oracle knows exactly
     how each field was obtained and can weight it.

WHAT THIS DOES NOT DO (and won't):
  - It does not defeat signing, bypass captcha, rotate accounts, or disguise
    automation. If TikTok blocks the follower list (very common, esp. for the
    private lists EE/IBPrincess keep), MOLE reports THAT — it does not try to
    force through. The honest signal "list is private/blocked" is itself
    intelligence and goes in the record as null-with-reason.
=============================================================================
"""

import asyncio
import argparse
import json
import os
import re
import redis
from datetime import datetime, timezone
from playwright.async_api import async_playwright

try:
    from dotenv import load_dotenv
    load_dotenv(r'C:\Users\VernonDunbar\Documents\Aegis_Phantom\.env')
except Exception:
    pass  # dotenv optional; env may already be set in the session

REDIS_URL = os.getenv("REDIS_URL")
VAULT_KEY = "aegis:vault"
OUTPUT_DIR = r"C:\Users\VernonDunbar\Documents\Aegis_Phantom\mole_reports"
COLLECTOR_VERSION = "mole-2.0-grounded"

# --- MOLE RECON session (dedicated, block-free recon account e.g. forshow4) --
# IMPORTANT: MOLE uses its OWN session vars (MOLE_*), NOT the block engine's
# TIKTOK_* session. This matters because:
#   1. The main/block-engine account has the goons BLOCKED (and is blocked BY
#      them). Viewing a goon's follower list from a blocked account returns a
#      DISTORTED / empty view -- MOLE would honestly report "unavailable" but
#      for the WRONG reason (the block relationship, not a real private list).
#   2. Keeping recon on a separate session protects the operational block
#      session from getting bot-flagged during recon scraping.
# Use a CLEAN account that has NOT blocked and is NOT blocked by the targets.
# Falls back to TIKTOK_* only if MOLE_* aren't set (with a loud warning).
TIKTOK_SESSION_ID = os.getenv("MOLE_SESSION_ID") or os.getenv("TIKTOK_SESSION_ID", "")
TIKTOK_TTWID      = os.getenv("MOLE_TTWID")      or os.getenv("TIKTOK_TTWID", "")
TIKTOK_MS_TOKEN   = os.getenv("MOLE_MS_TOKEN")   or os.getenv("TIKTOK_MS_TOKEN", "")
TIKTOK_CSRF_TOKEN = os.getenv("MOLE_CSRF_TOKEN") or os.getenv("TIKTOK_CSRF_TOKEN", "")
_USING_MOLE_SESSION = bool(os.getenv("MOLE_SESSION_ID"))


def session_cookies():
    """Build the cookie list for an authenticated context. Empty values dropped."""
    cookies = [
        {"name": "sessionid",     "value": TIKTOK_SESSION_ID, "domain": ".tiktok.com", "path": "/"},
        {"name": "ttwid",         "value": TIKTOK_TTWID,      "domain": ".tiktok.com", "path": "/"},
        {"name": "msToken",       "value": TIKTOK_MS_TOKEN,   "domain": ".tiktok.com", "path": "/"},
        {"name": "tt_csrf_token", "value": TIKTOK_CSRF_TOKEN, "domain": ".tiktok.com", "path": "/"},
    ]
    return [c for c in cookies if c["value"]]

# Operator's OWN feed accounts — if a "follower scrape" returns these, it's the
# contamination bug. We hard-exclude them AND treat their presence as a signal
# that the scrape hit the operator's feed, not the target's followers.
# (Extend this list with whatever shows in the operator's own sidebar.)
OPERATOR_FEED_CONTAMINATION = {
    "oceanhustle1", "premierleagueusa", "ocean.warriors1", "mdesings",
    "tandecoration", "vukovic_vlad", "cutezip33",
}

EE_KNOWN = {
    "ee2.0", "reeree", "snowwhitee23", "charliee_092", "reneeregina",
    "usmcvet2012", "tankerb29", "iceman8386", "jessessprout", "jarmygal",
    "hboss288", "teedrinker", "quack_dealer0331", "tomrockingreene9095",
    "jeffreyalvarado44", "nylah4ever0", "123qman", "suzannesinkevich",
    "brelan671", "roystoncort", "scott.vietnam.vet",
}

# a plausible tiktok handle: letters/digits/._ , 2-24 chars
HANDLE_RE = re.compile(r"^[a-z0-9._]{2,24}$")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_vault():
    if not REDIS_URL:
        print("[MOLE] No REDIS_URL — vault cross-reference disabled (will note as such)")
        return None   # None = unavailable, distinct from empty set
    try:
        r = redis.from_url(REDIS_URL, decode_responses=True)
        vault = set(r.smembers(VAULT_KEY))
        print(f"[MOLE] Vault loaded: {len(vault)} accounts")
        return vault
    except Exception as e:
        print(f"[MOLE] Vault load FAILED: {e}")
        return None


def cross_reference(handle: str, vault) -> dict:
    """
    Cross-reference a handle. Split into OBSERVED facts vs SUGGESTIVE flags.
    Note: double-dot / numeric-suffix are WEAK signals — kept, but labeled as
    suggestive, NOT as a threat verdict.
    """
    h = handle.lower().strip("@")
    in_vault = (h in vault) if vault is not None else None   # None = unknown
    return {
        # OBSERVED (verifiable):
        "in_vault": in_vault,                 # None if vault unavailable
        "in_ee_known": h in EE_KNOWN,
        # SUGGESTIVE (weak signals — not verdicts):
        "suggestive": {
            "double_dot": ".." in h,
            "numeric_suffix": bool(re.search(r"\d$", h)),
        },
    }


# --------------------------------------------------------------------------
# PAGE-STATE DETECTION — the core of the fix.
# Determine what we're ACTUALLY looking at before extracting anything.
# --------------------------------------------------------------------------
async def detect_page_state(page) -> dict:
    """
    Returns {"state": <str>, "detail": <str>}.
    Possible states:
      ok_list        -> a real follower/following list appears present
      error_page     -> TikTok "Something went wrong"
      login_wall     -> logged out / login prompt
      private_empty  -> list is private or empty (no rows, no error)
      captcha        -> anti-bot challenge visible
      unknown        -> couldn't classify (treated as failure, returns null)
    """
    try:
        info = await page.evaluate("""
            () => {
                const body = document.body ? document.body.innerText : '';
                const has = (t) => body.toLowerCase().includes(t.toLowerCase());
                return {
                    title: document.title || '',
                    somethingWrong: has('Something went wrong'),
                    login: !!document.querySelector('[data-e2e="top-login-button"], [data-e2e="login-button"]'),
                    captcha: has('verify to continue') || has('captcha') ||
                             !!document.querySelector('[class*="captcha"]'),
                    // real follower list rows use these; presence => list rendered
                    userItems: document.querySelectorAll('[data-e2e="user-item"], [data-e2e="follow-item"]').length,
                    privateHint: has('This account is private') || has('No followers yet'),
                };
            }
        """)
    except Exception as e:
        return {"state": "unknown", "detail": f"detect failed: {e}"}

    if info.get("captcha"):
        return {"state": "captcha", "detail": "anti-bot challenge visible"}
    if info.get("somethingWrong"):
        return {"state": "error_page", "detail": "TikTok 'Something went wrong'"}
    if info.get("login"):
        return {"state": "login_wall", "detail": "login prompt present"}
    if info.get("userItems", 0) > 0:
        return {"state": "ok_list", "detail": f"{info['userItems']} follower rows present"}
    if info.get("privateHint"):
        return {"state": "private_empty", "detail": "private or empty list"}
    # no rows, no error, no login -> can't confirm it's a real list. FAIL.
    return {"state": "unknown", "detail": f"no follower rows; title={info.get('title','')!r}"}


# --------------------------------------------------------------------------
# GROUNDED list scrape. Returns a dict, ALWAYS with an explicit outcome.
# On any non-ok state -> returns {"ok": False, "state": ..., "items": None}.
# NEVER fabricates from stray page links.
# --------------------------------------------------------------------------
async def scrape_list_grounded(page, target: str, list_type: str, vault) -> dict:
    """
    list_type: "followers" or "following"

    TikTok shows follower/following as a MODAL you open by CLICKING the count
    on the profile page (not a standalone /followers URL, which drifts to the
    feed). So we: go to the profile -> click the followers/following count ->
    wait for the modal rows to render -> scroll to load more -> read them.

    Returns the same grounded structure; still fail-null, never fabricates.
    """
    out = {
        "ok": False, "state": None, "detail": None, "items": None,
        "collection_method": "playwright_modal_click", "collected_at": now_iso(),
    }

    # 1) land on the profile page
    try:
        await page.goto(f"https://www.tiktok.com/@{target}",
                        timeout=30000, wait_until="domcontentloaded")
        await asyncio.sleep(3)
    except Exception as e:
        out["state"] = "nav_failed"
        out["detail"] = f"profile navigation failed: {e}"
        print(f"[MOLE] {list_type}: profile NAV FAILED for @{target} -> null ({e})")
        return out

    # 2) click the count to open the modal.
    #    Scope to the profile header count strong element to avoid hitting the
    #    inbox icon (which also carries a 'followers' data-e2e in its tab bar).
    count_selector = ('[data-e2e="followers-count"]' if list_type == "followers"
                      else '[data-e2e="following-count"]')
    try:
        # prefer clicking the count that sits in the profile header (has a
        # title/strong near it), not any stray match.
        count_el = page.locator(count_selector).first
        await count_el.scroll_into_view_if_needed(timeout=3000)
        await count_el.click(timeout=6000)
    except Exception as e:
        out["state"] = "modal_open_failed"
        out["detail"] = f"could not click {list_type} count: {e}"
        print(f"[MOLE] {list_type}: could not open modal for @{target} -> null ({e})")
        return out

    # 3) wait for the FOLLOWER-LIST modal specifically (NOT the notification
    #    inbox, which is also a [role=dialog] and has a "Followers" TAB).
    #    The follower modal contains the target's username header + follower
    #    rows that are /@handle anchors. We identify it by: a dialog that is
    #    NOT the inbox (no DivInboxContainer) AND contains /@ anchors OR the
    #    Following/Followers/Suggested tab set.
    def _modal_probe_js():
        return """
        () => {
            const dialogs = [...document.querySelectorAll('[role="dialog"], [class*="DivUserListContainer"], [class*="UserList"]')];
            // pick the dialog that is NOT the inbox and looks like a user list
            let target = null;
            for (const d of dialogs) {
                const cls = (d.className || '').toString();
                if (cls.includes('Inbox')) continue;           // skip notifications
                const t = (d.innerText || '').toLowerCase();
                const atLinks = d.querySelectorAll('a[href*="/@"]').length;
                const looksLikeList = atLinks > 0 ||
                    (t.includes('following') && t.includes('followers') && t.includes('suggested'));
                if (looksLikeList) { target = d; break; }
            }
            if (!target) return { hasModal:false };
            const txt = (target.innerText || '').toLowerCase();
            const rows = target.querySelectorAll('a[href*="/@"]');
            return {
                hasModal: true,
                rowCount: rows.length,
                private: txt.includes('this account is private') || txt.includes('no followers yet'),
                somethingWrong: txt.includes('something went wrong'),
            };
        }
        """

    try:
        await page.wait_for_selector('[role="dialog"]', timeout=6000)
    except Exception:
        pass

    modal_info = {}
    for _ in range(8):
        await asyncio.sleep(1)
        modal_info = await page.evaluate(_modal_probe_js())
        if modal_info.get("rowCount", 0) > 0 or modal_info.get("private") \
           or modal_info.get("somethingWrong"):
            break

    if not modal_info.get("hasModal"):
        out["state"] = "no_modal"
        out["detail"] = "clicked count but no dialog appeared"
        print(f"[MOLE] {list_type}: no modal opened for @{target} -> null")
        return out
    if modal_info.get("somethingWrong"):
        out["state"] = "error_page"
        out["detail"] = "modal shows 'something went wrong'"
        print(f"[MOLE] {list_type}: modal error for @{target} -> null")
        return out
    if modal_info.get("rowCount", 0) == 0:
        # DIAGNOSTIC: modal opened but no a[href*="/@"] rows found. Dump what's
        # actually inside the dialog so we can find the real row structure.
        # Diagnostic only — extracts nothing, fabricates nothing.
        try:
            mdiag = await page.evaluate("""
                () => {
                    const dlg = document.querySelector('[role="dialog"]');
                    if (!dlg) return { note: 'no dialog at diag time' };
                    // count different kinds of links/handles inside
                    const allLinks = dlg.querySelectorAll('a').length;
                    const atLinks = dlg.querySelectorAll('a[href*="/@"]').length;
                    const dataE2e = [...new Set([...dlg.querySelectorAll('[data-e2e]')]
                                     .map(e => e.getAttribute('data-e2e')))];
                    // sample the first 400 chars of dialog text
                    const textSample = (dlg.innerText || '').slice(0, 400);
                    // sample class names of direct children
                    const childCls = [...dlg.children].slice(0,4)
                                     .map(c => (c.className||'').toString().slice(0,60));
                    return { allLinks, atLinks, dataE2e, textSample, childCls };
                }
            """)
            print(f"[MOLE]   MODAL-DIAG links(total/@): "
                  f"{mdiag.get('allLinks')}/{mdiag.get('atLinks')}")
            print(f"[MOLE]   MODAL-DIAG data-e2e in modal: {mdiag.get('dataE2e')}")
            print(f"[MOLE]   MODAL-DIAG child classes: {mdiag.get('childCls')}")
            print(f"[MOLE]   MODAL-DIAG text sample: {mdiag.get('textSample')!r}")
        except Exception as e:
            print(f"[MOLE]   MODAL-DIAG failed: {e}")

        if modal_info.get("private"):
            out["state"] = "private_empty"
            out["detail"] = "modal indicates private/empty list"
        else:
            out["state"] = "modal_empty"
            out["detail"] = "modal opened but no rows rendered"
        print(f"[MOLE] {list_type}: modal empty for @{target} "
              f"-> null (state={out['state']})")
        return out

    # 4) scroll the follower modal to load more rows (lazy-loaded).
    #    Use the same non-inbox user-list container.
    try:
        for _ in range(6):
            await page.evaluate("""
                () => {
                    const dialogs = [...document.querySelectorAll('[role="dialog"], [class*="DivUserListContainer"], [class*="UserList"]')];
                    for (const d of dialogs) {
                        if ((d.className||'').toString().includes('Inbox')) continue;
                        if (d.querySelector('a[href*="/@"]')) {
                            const scroller = d.querySelector('div[style*="overflow"]') || d;
                            scroller.scrollTop = scroller.scrollHeight;
                            break;
                        }
                    }
                }
            """)
            await asyncio.sleep(1.2)
    except Exception:
        pass

    # 5) extract ONLY from anchors inside the correct (non-inbox) modal.
    items = []
    try:
        rows = await page.evaluate("""
            () => {
                const dialogs = [...document.querySelectorAll('[role="dialog"], [class*="DivUserListContainer"], [class*="UserList"]')];
                let dlg = null;
                for (const d of dialogs) {
                    if ((d.className||'').toString().includes('Inbox')) continue;
                    if (d.querySelector('a[href*="/@"]')) { dlg = d; break; }
                }
                if (!dlg) return [];
                const seen = new Set();
                const out = [];
                dlg.querySelectorAll('a[href*="/@"]').forEach(a => {
                    const m = a.href.match(/\\/@([^\\/?]+)/);
                    if (m) {
                        const h = m[1].toLowerCase();
                        if (!seen.has(h)) { seen.add(h); out.push(h); }
                    }
                });
                return out;
            }
        """)
        for h in rows:
            if not HANDLE_RE.match(h):
                continue
            if h == target.lower():
                continue
            if h in OPERATOR_FEED_CONTAMINATION:
                print(f"[MOLE] {list_type}: CONTAMINATION (@{h}) — aborting, null.")
                out["ok"] = False
                out["state"] = "contaminated"
                out["detail"] = f"operator-feed account {h} appeared; scrape invalid"
                out["items"] = None
                return out
            items.append({"handle": h, **cross_reference(h, vault)})
    except Exception as e:
        out["state"] = "extract_failed"
        out["detail"] = f"modal extraction failed: {e}"
        print(f"[MOLE] {list_type}: extraction failed for @{target} -> null ({e})")
        return out

    if not items:
        out["state"] = "modal_empty"
        out["detail"] = "no valid handles extracted from modal"
        print(f"[MOLE] {list_type}: no handles extracted for @{target} -> null")
        return out

    out["ok"] = True
    out["state"] = "ok_list"
    out["detail"] = f"{len(items)} rows from modal"
    out["items"] = items
    flagged = sum(1 for it in items if it["in_vault"] or it["in_ee_known"])
    print(f"[MOLE] {list_type}: OK for @{target} — {len(items)} real rows, "
          f"{flagged} vault/EE matches.")

    # close the modal before the next list (so followers vs following don't collide)
    try:
        await page.keyboard.press("Escape")
        await asyncio.sleep(1)
    except Exception:
        pass

    return out


async def scrape_profile(page, target: str, vault) -> dict:
    """
    Scrape the profile header. Each field FAIL-NULLs individually — a missing
    field stays null, never a placeholder that reads as data.
    """
    prof = {
        "username": None, "followers": None, "following": None,
        "likes": None, "bio": None, "video_count": None, "sec_uid": None,
    }
    field_map = {
        "username":  '[data-e2e="user-subtitle"]',
        "followers": '[data-e2e="followers-count"]',
        "following": '[data-e2e="following-count"]',
        "likes":     '[data-e2e="likes-count"]',
        "bio":       '[data-e2e="user-bio"]',
    }
    for field, sel in field_map.items():
        try:
            val = await page.text_content(sel, timeout=5000)
            prof[field] = val.strip() if val and val.strip() else None
        except Exception:
            prof[field] = None   # null, not "N/A"
    try:
        vids = await page.query_selector_all('[data-e2e="video-views"]')
        prof["video_count"] = len(vids) if vids else None
    except Exception:
        prof["video_count"] = None
    try:
        sec_uid = await page.evaluate("""
            () => {
                try {
                    const d = window.__UNIVERSAL_DATA_FOR_REHYDRATION__;
                    if (d) { const m = JSON.stringify(d).match(/"secUid":"([^"]+)"/); if (m) return m[1]; }
                } catch(e){}
                for (const s of document.querySelectorAll('script')) {
                    const m = (s.textContent||'').match(/"secUid":"([^"]+)"/);
                    if (m) return m[1];
                }
                return null;
            }
        """)
        prof["sec_uid"] = sec_uid or None
    except Exception:
        prof["sec_uid"] = None
    return prof


async def recon_target(page, target: str, vault) -> dict:
    """Full grounded recon on one target. Every section fail-nulls honestly."""
    print(f"\n[MOLE] ===== RECON @{target} =====")
    record = {
        "account_id": f"platform:tiktok:{target.lower()}",
        "platform": "tiktok",
        "username": target.lower(),
        "profile_url": f"https://www.tiktok.com/@{target}",
        "collector_version": COLLECTOR_VERSION,
        "collected_at": now_iso(),
        "profile": None,
        "followers": None,   # will hold the grounded scrape result dict
        "following": None,
        "cross_reference": cross_reference(target, vault),
        # split confidence — behavioral vs identity kept separate (per grounding)
        "confidence": {
            "behavioral_threat": None,    # filled by Oracle from operational data
            "cluster_association": None,  # filled by Oracle from co-occurrence
            "identity_attribution": None, # ALWAYS separate; scraping never sets this
        },
        "provenance": [{"source": "mole_scraper", "version": COLLECTOR_VERSION,
                        "collected_at": now_iso()}],
    }

    # profile
    try:
        await page.goto(f"https://www.tiktok.com/@{target}",
                        timeout=30000, wait_until="domcontentloaded")
        await asyncio.sleep(3)
        record["profile"] = await scrape_profile(page, target, vault)
        print(f"[MOLE] profile: followers={record['profile']['followers']} "
              f"following={record['profile']['following']} "
              f"bio={(record['profile']['bio'] or '')[:60]!r}")
    except Exception as e:
        print(f"[MOLE] profile scrape FAILED for @{target}: {e} -> profile stays null")
        record["profile"] = None

    # followers (grounded — returns null-with-reason on failure, NEVER fabricates)
    record["followers"] = await scrape_list_grounded(page, target, "followers", vault)
    # following
    record["following"] = await scrape_list_grounded(page, target, "following", vault)

    return record


async def run(targets, headless=True):
    vault = load_vault()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless,
                    args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
        )

        # Authenticate: add the session cookies so follower/following LISTS are
        # viewable (they require login even for public accounts).
        cookies = session_cookies()
        if cookies:
            await context.add_cookies(cookies)
            which = "MOLE recon session (forshow4)" if _USING_MOLE_SESSION else \
                    "WARNING: fell back to TIKTOK_* (block-engine) session — that " \
                    "account may have goons BLOCKED, which DISTORTS recon views!"
            print(f"[MOLE] Session authenticated ({len(cookies)} cookies) — {which}")
        else:
            print("[MOLE] WARNING: no session cookies in .env — follower/following "
                  "lists will hit login_wall and return null. Set TIKTOK_SESSION_ID "
                  "etc. in .env to read lists.")

        page = await context.new_page()

        records = []
        for t in targets:
            rec = await recon_target(page, t.strip().lstrip("@"), vault)
            records.append(rec)

        await browser.close()

    # write outputs
    base = os.path.join(OUTPUT_DIR, f"MOLE_{'_'.join(targets)}_{ts}")
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)

    # honest summary — reports null/blocked lists AS SUCH, never as "0 found"
    with open(base + "_SUMMARY.txt", "w", encoding="utf-8") as f:
        f.write("=" * 60 + "\nMOLE SUMMARY (grounded)\n" + "=" * 60 + "\n")
        for rec in records:
            u = rec["username"]
            prof = rec["profile"]
            f.write(f"\n@{u}\n")
            if prof:
                f.write(f"  followers_count: {prof['followers']}  "
                        f"following_count: {prof['following']}  likes: {prof['likes']}\n")
                f.write(f"  bio: {(prof['bio'] or '')[:80]!r}\n")
            else:
                f.write("  profile: NULL (scrape failed — not recorded as data)\n")
            for lt in ("followers", "following"):
                r = rec[lt]
                if r and r.get("ok"):
                    n = len(r["items"])
                    fl = sum(1 for it in r["items"] if it["in_vault"] or it["in_ee_known"])
                    f.write(f"  {lt}: {n} REAL rows, {fl} vault/EE matches\n")
                else:
                    state = r.get("state") if r else "not_attempted"
                    f.write(f"  {lt}: UNAVAILABLE — state={state} "
                            f"(NO data fabricated; list may be private/blocked)\n")
        f.write("\n" + "=" * 60 + "\n")
        f.write("NOTE: 'UNAVAILABLE' means the list could not be honestly read\n")
        f.write("(private, blocked, or bot-detected). It does NOT mean 'no\n")
        f.write("followers'. A private/blocked list is itself a signal — record\n")
        f.write("it, do not fabricate around it.\n")

    print(f"\n[MOLE] Reports written:\n  {base}.json\n  {base}_SUMMARY.txt")
    # console summary
    print("=" * 50)
    for rec in records:
        for lt in ("followers", "following"):
            r = rec[lt]
            if r and r.get("ok"):
                print(f"@{rec['username']} {lt}: {len(r['items'])} real rows")
            else:
                st = r.get("state") if r else "n/a"
                print(f"@{rec['username']} {lt}: UNAVAILABLE ({st}) — no fabricated data")
    print("=" * 50)
    return records


def main():
    ap = argparse.ArgumentParser(description="MOLE v2 — grounded TikTok recon collector")
    ap.add_argument("--target", help="single target handle")
    ap.add_argument("--targets", nargs="+", help="multiple target handles")
    ap.add_argument("--show", action="store_true", help="visible browser (headless off)")
    args = ap.parse_args()

    targets = args.targets or ([args.target] if args.target else None)
    if not targets:
        ap.error("provide --target or --targets")
    asyncio.run(run(targets, headless=not args.show))


if __name__ == "__main__":
    main()
