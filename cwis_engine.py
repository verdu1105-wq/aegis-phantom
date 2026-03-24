"""
AEGIS PHANTOM — CWIS ENGINE
Autonomous Close-In Weapon System
Modeled after U.S. Navy Phalanx CIWS / Aegis Combat System
"""

import asyncio
import aiohttp
import os
import json
import hashlib
from datetime import datetime, timedelta
from collections import deque, defaultdict
from dotenv import load_dotenv

load_dotenv(r"C:\Users\VernonDunbar\Documents\LiveGuardPro\.env")

# ─── CONFIG ───────────────────────────────────────────────────────────────────
CLOUD_URL        = "https://aegis-phantom-974184310088.us-east1.run.app"
ANTHROPIC_KEY    = os.getenv("ANTHROPIC_API_KEY", "")
TIKTOK_SIGN_KEY  = os.getenv("TIKTOK_SIGN_API_KEY", "")

# ─── CWIS THRESHOLDS ─────────────────────────────────────────────────────────
VAMPIRE_THRESHOLD       = 3    # known goons in this many seconds = VAMPIRE
VAMPIRE_WINDOW_SECS     = 30
BOT_FOLLOWER_MAX        = 50   # accounts with fewer followers = bot suspect
BOT_FOLLOWING_MIN       = 200  # accounts following more = bot suspect
HEAVY_CONTACT_THRESHOLD = 5    # bots in 15 seconds = heavy contact override
HEAVY_CONTACT_WINDOW    = 15
SCRIPT_REPEAT_THRESHOLD = 3    # same/similar comment 3x = script detected

# ─── THREAT LEVELS ───────────────────────────────────────────────────────────
THREAT_WATCH   = "WATCH"
THREAT_ALERT   = "ALERT"
THREAT_VAMPIRE = "VAMPIRE"

# ─── VULGARITY / HOSTILITY KEYWORDS ─────────────────────────────────────────
HOSTILE_KEYWORDS = [
    "fuck", "shit", "bitch", "cunt", "whore", "slut", "retard", "nigger",
    "kys", "kill yourself", "die", "faggot", "bastard", "asshole",
    # Misinformation patterns targeting veterans/hosts
    "traitor", "expired", "coward", "fake veteran", "thank you for nothing",
    "stolen valor", "liar", "fraud", "fake", "phony",
    # Political harassment
    "derek", "maga", "trump won", "fascist", "communist",
]

# ─── STATE ────────────────────────────────────────────────────────────────────
class CWISState:
    def __init__(self):
        self.join_times       = deque()          # (timestamp, username, is_goon)
        self.comment_hashes   = defaultdict(int) # hash -> count (script detection)
        self.flagged_accounts = {}               # username -> threat data
        self.blocked_accounts = set()            # already blocked
        self.evidence_log     = []               # full evidence trail
        self.threat_level     = THREAT_WATCH
        self.vampire_active   = False
        self.cwis_kills       = 0
        self.stream_anomalies = 0
        self.last_vampire     = None
        self.known_goons      = set()

state = CWISState()

# ─── LOAD GOON VAULT FROM CLOUD ──────────────────────────────────────────────
async def load_goon_vault():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{CLOUD_URL}/api/goons") as resp:
                data = await resp.json()
                goons = data.get("goons", [])
                state.known_goons = set(
                    (g["username"] if isinstance(g, dict) else g).lower()
                    for g in goons
                )
                print(f"🛡️ CWIS: {len(state.known_goons)} goons loaded from vault")
    except Exception as e:
        print(f"⚠️ Could not load goon vault: {e}")

# ─── AI SENTIMENT ANALYSIS ────────────────────────────────────────────────────
async def ai_analyze_comment(username: str, comment: str) -> dict:
    """Use Claude to analyze comment for hostility, misinfo, bot patterns."""
    if not ANTHROPIC_KEY:
        return {"hostile": False, "misinfo": False, "severity": "LOW", "reason": "No API key"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": ANTHROPIC_KEY,
                    "anthropic-version": "2023-06-01"
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 200,
                    "messages": [{
                        "role": "user",
                        "content": f"""Analyze this TikTok live comment for threat assessment. 
Comment by @{username}: "{comment}"

Reply ONLY in JSON:
{{"hostile": true/false, "misinfo": true/false, "severity": "LOW/MED/HIGH/CRITICAL", "reason": "brief reason", "bot_indicator": true/false}}

hostile = vulgarity or personal attacks toward the streamer
misinfo = false claims, stolen valor accusations, political disinfo
severity = LOW(mild), MED(moderate), HIGH(severe), CRITICAL(direct threat/doxxing)
bot_indicator = repetitive/scripted language, no personality"""
                    }]
                },
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                data = await resp.json()
                text = data["content"][0]["text"]
                return json.loads(text.replace("```json","").replace("```","").strip())
    except Exception as e:
        # Fall back to keyword detection
        return keyword_analyze(comment)

def keyword_analyze(comment: str) -> dict:
    """Fast local keyword analysis as fallback."""
    comment_lower = comment.lower()
    hostile = any(kw in comment_lower for kw in HOSTILE_KEYWORDS)
    severity = "HIGH" if hostile else "LOW"
    return {
        "hostile": hostile,
        "misinfo": any(kw in comment_lower for kw in ["traitor","expired","fake veteran","stolen valor"]),
        "severity": severity,
        "reason": "keyword match",
        "bot_indicator": False
    }

# ─── BOT SIGNATURE DETECTION ─────────────────────────────────────────────────
def is_bot_signature(username: str, followers: int = 0, following: int = 0) -> bool:
    """Detect bot account signatures."""
    indicators = 0
    if followers <= BOT_FOLLOWER_MAX:      indicators += 2
    if following >= BOT_FOLLOWING_MIN:     indicators += 1
    if followers == 0:                     indicators += 3  # zero followers = almost certainly bot
    import re
    if re.search(r'\d{6,}', username):     indicators += 2  # long number string
    if re.match(r'^user\d+', username):    indicators += 3  # default username
    if len(username) > 25:                 indicators += 1  # unusually long
    return indicators >= 3

# ─── SCRIPT DETECTION ────────────────────────────────────────────────────────
def detect_script(comment: str) -> bool:
    """Detect if comment is part of a coordinated script."""
    # Normalize and hash
    normalized = " ".join(comment.lower().split()[:8])  # first 8 words
    h = hashlib.md5(normalized.encode()).hexdigest()
    state.comment_hashes[h] += 1
    if state.comment_hashes[h] >= SCRIPT_REPEAT_THRESHOLD:
        print(f"📋 SCRIPT DETECTED: '{normalized[:40]}...' repeated {state.comment_hashes[h]}x")
        return True
    return False

# ─── VAMPIRE DETECTION ───────────────────────────────────────────────────────
def check_vampire_conditions() -> str:
    """Check if VAMPIRE conditions are met."""
    now = datetime.now()
    cutoff_30 = now - timedelta(seconds=VAMPIRE_WINDOW_SECS)
    cutoff_15 = now - timedelta(seconds=HEAVY_CONTACT_WINDOW)

    # Clean old entries
    while state.join_times and state.join_times[0][0] < cutoff_30:
        state.join_times.popleft()

    recent_30 = list(state.join_times)
    recent_15 = [j for j in recent_30 if j[0] >= cutoff_15]

    goons_30  = sum(1 for j in recent_30 if j[2])  # known goons in 30s
    bots_15   = sum(1 for j in recent_15 if j[3])  # bot suspects in 15s

    # Heavy contact override — kill bots first
    if bots_15 >= HEAVY_CONTACT_THRESHOLD:
        return THREAT_VAMPIRE

    # Standard VAMPIRE threshold
    if goons_30 >= VAMPIRE_THRESHOLD:
        return THREAT_VAMPIRE

    # ALERT threshold
    if goons_30 >= 1 or any(j[2] for j in recent_15):
        return THREAT_ALERT

    return THREAT_WATCH

# ─── AUTO BLOCK ──────────────────────────────────────────────────────────────
async def cwis_engage(username: str, reason: str, threat_data: dict):
    """CWIS fires — auto-block and log evidence."""
    if username in state.blocked_accounts:
        return

    state.blocked_accounts.add(username)
    state.cwis_kills += 1

    evidence = {
        "timestamp":   datetime.now().isoformat(),
        "username":    username,
        "reason":      reason,
        "threat_data": threat_data,
        "cwis_kill":   state.cwis_kills,
        "auto_blocked": True
    }
    state.evidence_log.append(evidence)

    print(f"🔫 CWIS KILL #{state.cwis_kills}: @{username} — {reason}")

    try:
        async with aiohttp.ClientSession() as session:
            # Auto-block via cloud
            await session.post(
                f"{CLOUD_URL}/api/cwis/block",
                json={
                    "username": username,
                    "reason":   reason,
                    "auto":     True,
                    "evidence": evidence
                },
                timeout=aiohttp.ClientTimeout(total=3)
            )
            # Log evidence
            await session.post(
                f"{CLOUD_URL}/api/cwis/evidence",
                json=evidence,
                timeout=aiohttp.ClientTimeout(total=3)
            )
    except Exception as e:
        print(f"⚠️ CWIS cloud report failed: {e}")
        # Save locally as backup
        with open(f"cwis_evidence_{datetime.now().strftime('%Y%m%d')}.jsonl", "a") as f:
            f.write(json.dumps(evidence) + "\n")

# ─── VAMPIRE ALARM ───────────────────────────────────────────────────────────
async def trigger_vampire(trigger_reason: str):
    """Broadcast VAMPIRE alert to all dashboards."""
    if state.vampire_active:
        return  # Already in VAMPIRE mode

    state.vampire_active = True
    state.last_vampire = datetime.now()
    print(f"\n{'🔴' * 20}")
    print(f"VAMPIRE VAMPIRE VAMPIRE")
    print(f"TRIGGER: {trigger_reason}")
    print(f"CWIS AUTONOMOUS ENGAGEMENT ACTIVE")
    print(f"{'🔴' * 20}\n")

    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                f"{CLOUD_URL}/intel",
                json={
                    "burner_id":  "CWIS_ENGINE",
                    "type":       "VAMPIRE",
                    "message":    f"BOT WAVE DETECTED — CWIS ENGAGED — {trigger_reason}",
                    "auto_block": True
                },
                timeout=aiohttp.ClientTimeout(total=3)
            )
    except Exception as e:
        print(f"⚠️ VAMPIRE broadcast failed: {e}")

async def clear_vampire():
    """Clear VAMPIRE state when wave subsides."""
    state.vampire_active = False
    state.threat_level   = THREAT_WATCH
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                f"{CLOUD_URL}/intel",
                json={
                    "burner_id": "CWIS_ENGINE",
                    "type":      "VAMPIRE_CLEAR",
                    "message":   f"Wave neutralized. {state.cwis_kills} accounts blocked. CWIS standing by."
                },
                timeout=aiohttp.ClientTimeout(total=3)
            )
    except:
        pass

# ─── MAIN PROCESSORS ─────────────────────────────────────────────────────────
async def process_join(username: str, followers: int = 0, following: int = 0):
    """Process a join event through CWIS."""
    is_goon   = username.lower() in state.known_goons
    is_bot    = is_bot_signature(username, followers, following)

    state.join_times.append((datetime.now(), username, is_goon, is_bot))

    threat_level = check_vampire_conditions()

    if threat_level == THREAT_VAMPIRE and not state.vampire_active:
        await trigger_vampire(f"@{username} — {'known goon' if is_goon else 'bot wave'}")

    if is_bot and state.vampire_active:
        await cwis_engage(username, "BOT_SIGNATURE", {
            "followers": followers, "following": following,
            "username_pattern": username
        })
    elif is_goon:
        print(f"🚨 ALERT: Known goon @{username} joined")
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(
                    f"{CLOUD_URL}/intel",
                    json={
                        "burner_id": "CWIS_ENGINE",
                        "type":      "GOON_JOIN",
                        "message":   f"Known goon @{username} joined stream"
                    },
                    timeout=aiohttp.ClientTimeout(total=3)
                )
        except:
            pass

async def process_comment(username: str, comment: str, followers: int = 0, following: int = 0):
    """Process a comment through CWIS AI analysis."""
    is_goon    = username.lower() in state.known_goons
    is_bot     = is_bot_signature(username, followers, following)
    is_script  = detect_script(comment)

    # Script detection = bot farm indicator = VAMPIRE
    if is_script and not state.vampire_active:
        await trigger_vampire(f"Script detected from @{username}: '{comment[:40]}'")

    # AI or keyword analysis
    analysis = await ai_analyze_comment(username, comment)

    if analysis.get("hostile") or analysis.get("misinfo"):
        severity = analysis.get("severity", "MED")
        reason   = analysis.get("reason", "hostile comment")

        print(f"⚔️ [{severity}] @{username}: {comment[:60]}")

        # Auto-capture account intel
        await cwis_engage(username, f"HOSTILE_{severity}", {
            "comment":  comment,
            "analysis": analysis,
            "followers": followers,
            "following": following
        })

        # Broadcast to dashboard
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(
                    f"{CLOUD_URL}/intel",
                    json={
                        "burner_id": "CWIS_ENGINE",
                        "type":      f"HOSTILE_{severity}",
                        "message":   f"@{username}: {comment[:80]} — {reason}"
                    },
                    timeout=aiohttp.ClientTimeout(total=3)
                )
        except:
            pass

    # Bot in VAMPIRE mode = auto-kill
    if is_bot and state.vampire_active:
        await cwis_engage(username, "BOT_COMMENT_PATTERN", {
            "comment": comment, "followers": followers
        })

# ─── STREAM ANOMALY DETECTION ────────────────────────────────────────────────
async def process_stream_anomaly(anomaly_type: str):
    """Detect stream glitching = mass report indicator."""
    state.stream_anomalies += 1
    print(f"📡 STREAM ANOMALY #{state.stream_anomalies}: {anomaly_type}")

    if state.stream_anomalies >= 3:
        print("⚠️ MASS REPORTING SUSPECTED — Stream instability detected")
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(
                    f"{CLOUD_URL}/intel",
                    json={
                        "burner_id": "CWIS_ENGINE",
                        "type":      "MASS_REPORT_SUSPECTED",
                        "message":   f"Stream anomaly #{state.stream_anomalies} — possible coordinated mass reporting in progress"
                    },
                    timeout=aiohttp.ClientTimeout(total=3)
                )
        except:
            pass

# ─── VAMPIRE MONITOR ─────────────────────────────────────────────────────────
async def vampire_monitor():
    """Continuously monitor threat levels and clear VAMPIRE when safe."""
    while True:
        await asyncio.sleep(10)
        if state.vampire_active and state.last_vampire:
            elapsed = (datetime.now() - state.last_vampire).seconds
            # Clear VAMPIRE after 60s of no new threats
            if elapsed > 60:
                recent = [j for j in state.join_times
                         if j[0] > datetime.now() - timedelta(seconds=30)]
                if not any(j[2] or j[3] for j in recent):
                    print("✅ VAMPIRE CLEAR — Wave neutralized")
                    await clear_vampire()

        # Reload goon vault every 5 minutes
        if hasattr(vampire_monitor, '_last_reload'):
            if (datetime.now() - vampire_monitor._last_reload).seconds > 300:
                await load_goon_vault()
                vampire_monitor._last_reload = datetime.now()
        else:
            vampire_monitor._last_reload = datetime.now()

# ─── STATUS REPORT ───────────────────────────────────────────────────────────
def print_status():
    print(f"""
╔══════════════════════════════════════╗
║      CWIS ENGINE STATUS              ║
╠══════════════════════════════════════╣
║  Threat Level: {state.threat_level:<22}║
║  VAMPIRE Active: {str(state.vampire_active):<20}║
║  CWIS Kills: {state.cwis_kills:<24}║
║  Goons in Vault: {len(state.known_goons):<20}║
║  Evidence Items: {len(state.evidence_log):<20}║
║  Stream Anomalies: {state.stream_anomalies:<18}║
╚══════════════════════════════════════╝""")

# ─── ENTRY POINT ─────────────────────────────────────────────────────────────
async def main():
    print("""
    ██████╗██╗    ██╗██╗███████╗
   ██╔════╝██║    ██║██║██╔════╝
   ██║     ██║ █╗ ██║██║███████╗
   ██║     ██║███╗██║██║╚════██║
   ╚██████╗╚███╔███╔╝██║███████║
    ╚═════╝ ╚══╝╚══╝ ╚═╝╚══════╝
    AEGIS PHANTOM — CWIS ENGINE
    AUTONOMOUS DEFENSE ONLINE
    """)
    await load_goon_vault()
    print_status()
    asyncio.create_task(vampire_monitor())
    print("⚡ CWIS STANDING BY — Awaiting radar feed...")

if __name__ == "__main__":
    asyncio.run(main())
