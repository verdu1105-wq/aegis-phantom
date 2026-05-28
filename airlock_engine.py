"""
AEGIS PHANTOM — AIRLOCK ENGINE v3.1
Unified local engine: TikTok monitor + CWIS + FastAPI + WebSocket
Based on the working airlock_engine.py architecture

PATCH NOTES v3.1:
- PATCH 1: Client instance guard — prevents dual Room ID chasing
- PATCH 2: GhostLockDetector Room ID validation — detects redirect attacks
- PATCH 3: execute_block retry logic — no more silent block failures
- PATCH 4: _forward_to_cloud already async via create_task (verified)
- PATCH 5: Exponential backoff — prevents TikTok ghost-banning AEGIS
"""

from TikTokLive import TikTokLiveClient
from TikTokLive.events import CommentEvent, JoinEvent, ConnectEvent, DisconnectEvent
from TikTokLive.client.web.web_settings import WebDefaults
import os, json, asyncio, time, hashlib
from dotenv import load_dotenv
from datetime import datetime
from collections import deque, defaultdict
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import uvicorn

load_dotenv(r"C:\Users\VernonDunbar\Documents\Aegis_Phantom\.env")

# ─── CONFIG ───────────────────────────────────────────────────────────────────
WebDefaults.tiktok_sign_api_key = os.getenv("TIKTOK_SIGN_API_KEY")

TARGET       = os.getenv("TIKTOK_TARGET", "essayons123")
CLOUD_URL    = "https://aegis-cwis-974184310088.us-east1.run.app"
CLOUD_TOKEN  = os.getenv("AEGIS_CLOUD_TOKEN", "")

WHITELIST = {
    # ── CORE TEAM ──────────────────────────────────────────
    "cavalryspice", "jescavalrygal", "jescavalrygal2.0",
    "1jarmygal", "2jarmygal", "jarmygal",
    "verdu1105", "wakandan_sentinel03", "diavatalks",
    "kenneth.cupps4", "mistalina7", "d4rkn8t", "hotgirlmoney",
    # ── MODS ───────────────────────────────────────────────
    "wasntme328", "shorty8251", "brownsugardoll20",
    "christophermicken3", "infernalfreakshow",
    "let_me_be_the_one1",
    # ── CONFIRMED FRIENDLIES ───────────────────────────────
    "yoli1392", "selenaperez26", "lindabentzel",
    "troysupertramp0", "lizzette.28", "creemiib",
    "robbyg973", "laur0627", "yellowrose0627",
    "fluffypinksatanist", "cattychic7", "phoenix.rising.backup2",
    "megsgirl247", "roamingcaptainroman", "d4rkn8t",
    "bbflays13", "forshow4", "viper.vet",
}

# ─── CWIS STATE ───────────────────────────────────────────────────────────────
join_times       = deque()
comment_hashes   = defaultdict(int)
blocked_accounts = set()
cwis_kills       = 0
vampire_active   = False
threat_level     = "WATCH"
stream_anomalies = 0
goon_vault       = set()
last_cwis_kill   = 0  # timestamp of last kill for 30s cooldown

# HOSTILE = direct attacks ON JESS only, not general political comments
HOSTILE_KEYWORDS = [
    "kys", "kill yourself",
    "fake veteran", "stolen valor", "expired soldier",
    "you should die", "hope you die",
    "shut up jess", "shut up jes",
    "go live", "get off live",
    "stupid bitch", "dumb bitch", "ugly bitch",
    # ── EE SIGNATURE PHRASES ───────────────────────────────
    "bitch & moan show", "bitch and moan show",
    "oppressed professional", "grifter",
    "we the people are sick",
    "today on the bitch",
]

# GATOR NAMES — auto-block on join regardless of VAMPIRE state
GATOR_PATTERNS = [
    # ── GATOR NETWORK ──────────────────────────────────────
    "gator", "gatorhr", "thehrgator", "bcgator",
    # ── DEVIL DOG / CLARENCE PATTON ────────────────────────
    "devil.dog", "devildog", "devil_dog", "devildogforlife",
    # ── AIRBORNE / MILITARY IMPERSONATORS ──────────────────
    "airborne_ruc", "airborne_rucka", "airborne_rucca",
    # ── EE ALIASES ─────────────────────────────────────────
    "peteynola", "petey_nola", "petey.nola",
    "ee4", "_e.4", "its_ee4",
    "penic81", "armybarbee", "mandarose",
    "rusty.the.clown", "unclerustytheclown",
    # ── MBW BOT CELL ───────────────────────────────────────
    "mbw",
    # ── CONFIRMED HOSTILE NODES ────────────────────────────
    "sheiladempseyzale", "respectfullyno",
    "ibprincess56", "militarybratt",
    "scottchambless", "narcissisticexpert",
    "marinabath1", "15mins.offameucanhave",
    # ── TONIGHT'S CONFIRMED HOSTILE NODES ──────────────────
    "wesley87866", "nylah4ever0", "user4137704327525",
]

# EE NETWORK SIGNATURES — Erin Rice network patterns
EE_PATTERNS = [
    "heven_scent",        # Known alias
    "respectfullyno",     # EE 2.0 account
    "e648811",            # Backup account
    "pumpkeen",           # Double-E trailing
    "josieee",            # Triple-E trailing
    "elizabether",        # Misspelled Elizabeth
    "deaconlee",          # Known EE network
    "ee2.0",              # Direct EE 2.0 label
    "ee_2.0",
    "mbw",                # MBW bot cell identifier
    "15mins.offame",      # EE catchphrase account
    "dragonfliesrfree",   # Confirmed EE account
    "militarybratt",      # Confirmed EE account
    "t..bone",            # Double dot EE pattern
    "ibprincess56",       # Multi-account operator
]

def is_ee_network(username: str) -> bool:
    import re
    u = username.lower()
    # Check known EE patterns list
    if any(p in u for p in EE_PATTERNS):
        return True
    # Double dot pattern: elizabeth..whatever
    if re.search(r"\.{2,}", u):
        return True
    # Trailing double/triple E
    if re.search(r"e{2,}$", u):
        return True
    # E+numbers only
    if re.match(r"^e\d+$", u):
        return True
    return False

# ─── GHOST LOCK DETECTOR ──────────────────────────────────────────────────────
# PATCH 2: Added active_room_id tracking and validate_room_id method
import threading

GHOST_LOCK_THRESHOLD = 3
GHOST_LOCK_WINDOW_SEC = 300

class GhostLockDetector:
    def __init__(self):
        self.reconnect_log = []
        self.lock_state = None
        self.lock_acquired_at = None
        self.ghost_lock_alerts = 0
        self._lock = threading.Lock()
        self.active_room_id = None          # PATCH 2 — tracks valid Room ID

    def set_lock(self, handle: str, room_id: str = None):   # PATCH 2 — room_id param added
        with self._lock:
            self.lock_state = handle
            self.lock_acquired_at = time.time()
            if room_id:
                self.active_room_id = room_id
                print(f"[GHOST LOCK] 🔒 Lock set: @{handle} | Room ID: {room_id}")
            else:
                print(f"[GHOST LOCK] 🔒 Lock set: @{handle}")

    def validate_room_id(self, room_id: str) -> bool:       # PATCH 2 — detects redirect attacks
        """Returns False if Room ID doesn't match locked room — potential redirect attack."""
        with self._lock:
            if self.active_room_id and room_id != self.active_room_id:
                print(f"[GHOST LOCK] 🚨 ROOM ID MISMATCH — Expected: {self.active_room_id} | Got: {room_id}")
                return False
            return True

    def on_disconnect(self) -> bool:
        with self._lock:
            now = time.time()
            self.reconnect_log.append(now)
            self.reconnect_log = [t for t in self.reconnect_log if now - t <= GHOST_LOCK_WINDOW_SEC]
            if len(self.reconnect_log) >= GHOST_LOCK_THRESHOLD:
                self.ghost_lock_alerts += 1
                print(f"[GHOST LOCK] ⚠️ EXPLOIT DETECTED — {len(self.reconnect_log)} reconnects in {GHOST_LOCK_WINDOW_SEC}s")
                return True
        return False

    def on_reconnect(self) -> str | None:
        with self._lock:
            if self.lock_state:
                print(f"[GHOST LOCK] 🔄 Re-acquiring lock: @{self.lock_state}")
            return self.lock_state

    def get_status(self) -> dict:
        with self._lock:
            return {
                "locked_handle": self.lock_state,
                "active_room_id": self.active_room_id,
                "reconnects_in_window": len(self.reconnect_log),
                "ghost_lock_alerts": self.ghost_lock_alerts,
                "lock_age_sec": int(time.time() - self.lock_acquired_at) if self.lock_acquired_at else None
            }

ghost_lock = GhostLockDetector()

# ─── FASTAPI APP ──────────────────────────────────────────────────────────────
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ─── CONNECTION MANAGER ───────────────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.connections = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)
        print(f"🔌 Dashboard connected. Total: {len(self.connections)}")

    def disconnect(self, ws: WebSocket):
        if ws in self.connections:
            self.connections.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in list(self.connections):
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            try: self.connections.remove(ws)
            except: pass

manager = ConnectionManager()

# ─── ROUTES ───────────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {"status": "AEGIS-AIRLOCK-ACTIVE", "target": TARGET, "goons": len(goon_vault)}

@app.get("/health")
async def health():
    return {
        "status": "online",
        "target": TARGET,
        "threat_level": threat_level,
        "vampire": vampire_active,
        "cwis_kills": cwis_kills,
        "goon_vault": len(goon_vault),
        "dashboards": len(manager.connections),
        "ghost_lock": ghost_lock.get_status()   # PATCH 2 — exposes Room ID in health endpoint
    }

@app.get("/api/goons")
async def get_goons():
    return {"goons": sorted(list(goon_vault)), "count": len(goon_vault)}

@app.post("/api/goons")
async def add_goon(request: Request):
    data = await request.json()
    username = data.get("username","").lower().strip().lstrip("@")
    if username and username not in WHITELIST:
        goon_vault.add(username)
        # Also sync to cloud
        asyncio.create_task(sync_goon_to_cloud(username))
        await manager.broadcast({"type": "goon_added", "username": username})
        print(f"💀 GOON ADDED: @{username}")
    return {"status": "added", "username": username}

@app.post("/api/block")
async def block_user(request: Request):
    data = await request.json()
    username = data.get("username","").lstrip("@")
    reason   = data.get("reason", "manual")
    await execute_block(username, reason)
    return {"status": "blocked", "username": username}

@app.post("/api/mode")
async def set_mode(request: Request):
    data = await request.json()
    mode = data.get("mode", "WATCH")
    await manager.broadcast({"type": "mode_change", "mode": mode})
    return {"status": "ok", "mode": mode}

# WebSocket endpoints matching old dashboard
@app.websocket("/ws/dashboard/{creator}")
async def ws_dashboard(ws: WebSocket, creator: str):
    await manager.connect(ws)
    try:
        while True:
            await asyncio.sleep(30)
            await ws.send_json({"type": "ping"})
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except:
        manager.disconnect(ws)

@app.websocket("/ws/live/{creator}")
async def ws_live(ws: WebSocket, creator: str):
    await manager.connect(ws)
    try:
        while True:
            await asyncio.sleep(30)
            await ws.send_json({"type": "ping"})
    except:
        manager.disconnect(ws)

# Also support /ws/dashboard for new command center
@app.websocket("/ws/dashboard")
async def ws_dashboard_plain(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            await asyncio.sleep(30)
            await ws.send_json({"type": "ping"})
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except:
        manager.disconnect(ws)

# ─── CWIS LOGIC ───────────────────────────────────────────────────────────────
def is_goon(username: str) -> bool:
    u = username.lower()
    if u in goon_vault: return True
    if u in WHITELIST:  return False
    # Check gator patterns — auto-block regardless of VAMPIRE
    return any(pattern in u for pattern in GATOR_PATTERNS)

def is_gator(username: str) -> bool:
    """Gator family — block on sight, no VAMPIRE required."""
    u = username.lower()
    return any(pattern in u for pattern in GATOR_PATTERNS)

def is_hostile(comment: str) -> bool:
    c = comment.lower()
    return any(kw in c for kw in HOSTILE_KEYWORDS)

# ─── VOICE TRIGGER — Jess calls out a username, CWIS locks on ────────────────
VOICE_TRIGGERS = [
    "eyes on",
]

async def check_voice_trigger(username: str, comment: str):
    """If Jess calls out a username in chat, CWIS locks on that account."""
    comment_lower = comment.lower()

    # Check if Jess is calling someone out
    triggered = any(trigger in comment_lower for trigger in VOICE_TRIGGERS)
    if not triggered:
        return

    # Extract @mentions from the comment
    import re
    mentions = re.findall(r'@(\w[\w.]*)', comment)

    for target in mentions:
        if target.lower() not in WHITELIST and target.lower() != username.lower():
            print(f"🎯 VOICE LOCK: Jess called out @{target} — tracking")
            await manager.broadcast({
                "type": "intel",
                "intel_type": "VOICE_LOCK",
                "burner_id": "CWIS",
                "message": f"🎯 VOICE LOCK: @{target} called out by Jess — hunting"
            })
            # Add to goon vault immediately
            goon_vault.add(target.lower())
            # Execute block
            await execute_block(target, "VOICE_LOCK")

def detect_script(comment: str) -> bool:
    normalized = " ".join(comment.lower().split()[:8])
    h = hashlib.md5(normalized.encode()).hexdigest()
    comment_hashes[h] += 1
    return comment_hashes[h] >= 3

def check_vampire() -> str:
    global vampire_active, threat_level
    now = time.time()
    cutoff = now - 30
    while join_times and join_times[0][0] < cutoff:
        join_times.popleft()
    goons_30 = sum(1 for j in join_times if j[1])
    bots_15  = sum(1 for j in join_times if j[0] > now-15 and j[2])
    if bots_15 >= 5 or goons_30 >= 3:
        return "VAMPIRE"
    if goons_30 >= 1:
        return "ALERT"
    return "WATCH"

async def execute_block(username: str, reason: str = "auto"):
    # PATCH 3: Added retry logic and explicit error logging — no more silent failures
    global cwis_kills
    u_lower = username.lower()
    # Whitelist check FIRST — never block friendlies
    if u_lower in WHITELIST or username in WHITELIST:
        print(f"✅ WHITELIST PROTECTED: @{username} — block cancelled")
        return
    # Already blocked this session
    if username in blocked_accounts:
        return
    # Check Redis whitelist (live vouches from dashboard)
    try:
        if r and r.sismember("whitelist", u_lower):
            WHITELIST.add(u_lower)
            print(f"✅ REDIS WHITELIST PROTECTED: @{username}")
            return
    except: pass
    # Cooldown — don't re-block same account within 120s
    global last_cwis_kill
    import time as _time
    now = _time.time()
    cooldown_key = f"aegis:cooldown:{u_lower}"
    try:
        if r and r.exists(cooldown_key):
            return
        if r: r.setex(cooldown_key, 120, "1")
    except: pass

    blocked_accounts.add(username)
    cwis_kills += 1
    print(f"🔫 CWIS KILL #{cwis_kills}: @{username} — {reason}")

    evidence = {
        "timestamp": datetime.now().isoformat(),
        "username":  username,
        "reason":    reason,
        "kill":      cwis_kills
    }

    await manager.broadcast({
        "type":     "cwis_block",
        "username": username,
        "reason":   reason,
        "kills":    cwis_kills
    })

    # ─── REAL TIKTOK BLOCK EXECUTION — PATCH 3: retry wrapper ────────────────
    asyncio.create_task(_tiktok_block_with_retry(username))

    # Sync to cloud evidence log
    try:
        async with httpx.AsyncClient(timeout=3) as http:
            await http.post(f"{CLOUD_URL}/api/cwis/block", json={
                "username": username,
                "reason":   reason,
                "evidence": evidence
            })
    except:
        pass

async def _tiktok_block_with_retry(username: str):
    # PATCH 3: Two-attempt retry with explicit error logging
    for attempt in range(1, 3):
        success = await _tiktok_block(username, attempt)
        if success:
            return
        if attempt < 2:
            print(f"⚠️ Block attempt {attempt} failed for @{username} — retrying in 3s...")
            await asyncio.sleep(3)
    # Both attempts failed — log to dashboard
    print(f"❌ BLOCK FAILED BOTH ATTEMPTS: @{username} — logging as evidence")
    await manager.broadcast({
        "type": "block_failed",
        "username": username,
        "reason": "Both block attempts failed — TikTok API degraded or session invalid"
    })

async def _tiktok_block(username: str, attempt: int = 1) -> bool:
    """Execute real TikTok block using mod session. Returns True on success."""
    SESSION_ID = os.getenv("TIKTOK_SESSION_ID", "")
    if not SESSION_ID:
        print(f"⚠️ No TikTok session ID — block not executed for @{username}")
        return False

    headers = {
        "Cookie": f"sessionid={SESSION_ID}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.tiktok.com/",
        "Origin": "https://www.tiktok.com",
    }

    try:
        # Step 1 — get user ID from username
        async with httpx.AsyncClient(timeout=10, headers=headers) as http:
            resp = await http.get(
                f"https://www.tiktok.com/api/user/detail/",
                params={"uniqueId": username, "aid": "1988"}
            )
            data = resp.json()
            user_id = data.get("userInfo", {}).get("user", {}).get("id", "")

            if not user_id:
                print(f"⚠️ Could not find user ID for @{username} (attempt {attempt})")
                return False

        # Step 2 — execute block
        async with httpx.AsyncClient(timeout=5, headers=headers) as http:
            resp = await http.post(
                "https://www.tiktok.com/api/commit/follow/user/",
                params={
                    "user_id": user_id,
                    "type": 3,  # 3 = block
                    "aid": "1988",
                    "channel": "tiktok_web",
                }
            )
            if resp.status_code == 200:
                print(f"✅ TIKTOK BLOCK CONFIRMED: @{username} (attempt {attempt})")
                return True
            else:
                print(f"⚠️ TikTok block failed for @{username} (attempt {attempt}): HTTP {resp.status_code} — {resp.text[:100]}")
                return False

    except httpx.ConnectTimeout:
        print(f"⚠️ Block UNCONFIRMED (timeout) for @{username} (attempt {attempt}) — block may have landed")
        try:
            await manager.broadcast({
                "type":     "block_unconfirmed",
                "username": username,
                "reason":   f"Attempt {attempt}: ConnectTimeout — block may have landed"
            })
        except: pass
        return True

    except Exception as e:
        print(f"⚠️ TikTok block error for @{username} (attempt {attempt}): {type(e).__name__}: {str(e)}")
        try:
            await manager.broadcast({
                "type":     "block_failed",
                "username": username,
                "reason":   f"Attempt {attempt}: {type(e).__name__}: {str(e)[:80]}"
            })
        except: pass
        return False

async def trigger_vampire(reason: str):
    global vampire_active, threat_level
    vampire_active = True
    threat_level   = "VAMPIRE"
    print(f"\n{'🔴'*20}\nVAMPIRE VAMPIRE VAMPIRE\n{reason}\n{'🔴'*20}\n")
    await manager.broadcast({
        "type":    "intel",
        "intel_type": "VAMPIRE",
        "burner_id": "CWIS",
        "message": f"BOT WAVE — CWIS ENGAGED — {reason}"
    })

async def sync_goon_to_cloud(username: str):
    try:
        async with httpx.AsyncClient(timeout=3) as http:
            await http.post(f"{CLOUD_URL}/api/goons", json={
                "username": username,
                "added_by": "AIRLOCK_LOCAL"
            })
    except:
        pass

async def health_checkin():
    """Ping Cloud Run every 60s — keeps Pi-top badge ONLINE in dashboard."""
    await asyncio.sleep(5)  # brief delay to let startup finish
    while True:
        try:
            async with httpx.AsyncClient(timeout=3) as http:
                await http.post(f"{CLOUD_URL}/api/pitop/checkin", json={
                    "status":  "online",
                    "target":  TARGET,
                    "goons":   len(goon_vault),
                    "kills":   cwis_kills,
                    "vampire": vampire_active,
                    "ts":      datetime.now().isoformat()
                }, headers={"Authorization": f"Bearer {CLOUD_TOKEN}"})
        except:
            pass
        await asyncio.sleep(60)

async def load_goon_vault():
    global goon_vault
    try:
        import redis as redis_lib
        r = redis_lib.from_url("redis://:Ae!1G3PhA04we2g90@redis-10919.c284.us-east1-2.gce.cloud.redislabs.com:10919")
        raw = r.smembers("goons")
        goon_vault = set(g.decode() for g in raw)
        print(f"🛡️ Vault loaded: {len(goon_vault)} goons from Redis Cloud")
    except Exception as e:
        print(f"⚠️ Vault load failed: {e}")

async def run_monitor():
    global threat_level, vampire_active
    active_client = None  # PATCH 1 — single client instance guard

    while True:
        try:
            # PATCH 1 — kill previous client before creating new one
            if active_client is not None:
                try:
                    await active_client.disconnect()
                except:
                    pass
                active_client = None
                await asyncio.sleep(2)  # Brief pause to ensure clean teardown

            client = TikTokLiveClient(unique_id=f"@{TARGET}")
            active_client = client  # PATCH 1 — track active instance

            @client.on(ConnectEvent)
            async def on_connect(event):
                print(f"✅ RADAR LOCKED: @{TARGET}")
                # PATCH 2 — capture and validate Room ID on connect
                room_id = str(getattr(event, 'room_id', '') or '')
                ghost_lock.set_lock(TARGET, room_id)
                handle = ghost_lock.on_reconnect()
                await manager.broadcast({
                    "type": "radar_online",
                    "data": {"target": TARGET, "room_id": room_id, "timestamp": datetime.now().isoformat()}
                })

            @client.on(DisconnectEvent)
            async def on_disconnect(event):
                print(f"⚠️ DISCONNECTED: @{TARGET}")
                is_exploit = ghost_lock.on_disconnect()
                if is_exploit:
                    await manager.broadcast({
                        "type": "intel",
                        "intel_type": "GHOST_LOCK_ALERT",
                        "burner_id": "CWIS",
                        "message": f"⚠️ GHOST LOCK EXPLOIT DETECTED — {ghost_lock.get_status()['reconnects_in_window']} reconnects in 5min window"
                    })
                await manager.broadcast({"type": "radar_offline", "data": {"target": TARGET}})

            @client.on(JoinEvent)
            async def on_join(event):
                try:
                    username = event.user.unique_id
                    goon     = is_goon(username)
                    gator    = is_gator(username)
                    ee_flag  = is_ee_network(username)
                    if ee_flag and not goon:
                        print(f"🔴 EE SIGNATURE DETECTED: @{username}")
                        goon = True  # Treat as goon
                    bot_sus  = False

                    join_times.append((time.time(), goon, bot_sus))

                    # Add this after the ghost_lock status check — inside on_join
                    # after the existing join_times.append line:

                    # GHOST LOCK JOIN CAPTURE — log accounts joining during active exploit
                    ghost_status = ghost_lock.get_status()
                    if ghost_status["reconnects_in_window"] >= GHOST_LOCK_THRESHOLD:
                        print(f"🔍 GHOST LOCK JOIN CAPTURED: @{username} — joined during active exploit window")
                        await manager.broadcast({
                        "type": "intel",
                        "intel_type": "GHOST_LOCK_JOIN",
                        "burner_id": "CWIS",
                        "message": f"🔍 GHOST LOCK JOIN: @{username} joined during {ghost_status['reconnects_in_window']}-reconnect exploit window"
                             })
                    # Auto-add to goon vault for investigation
                    if username.lower() not in WHITELIST:
                        goon_vault.add(username.lower())
                        asyncio.create_task(sync_goon_to_cloud(username.lower()))

                    

                    # GATOR ON SIGHT — block immediately, no VAMPIRE needed
                    if gator:
                        print(f"🐊 GATOR SIGHTING: @{username} — AUTO-BLOCK")
                        await execute_block(username, "GATOR_SIGHTING")
                        await manager.broadcast({
                            "type": "intel",
                            "intel_type": "GATOR_SIGHTING",
                            "burner_id": "CWIS",
                            "message": f"🐊 GATOR SIGHTING: @{username} — auto-blocked"
                        })
                        return

                    level = check_vampire()
                    if level == "VAMPIRE" and not vampire_active:
                        await trigger_vampire(f"@{username} triggered wave detection")

                    print(f"{'🚨' if goon else '👤'} JOIN: @{username}")

                    await manager.broadcast({
                        "type": "join",
                        "data": {
                            "username":  username,
                            "action":    "BLOCK" if goon else "WATCH",
                            "is_threat": goon,
                            "timestamp": datetime.now().strftime("%H:%M:%S")
                        }
                    })

                    # Forward to cloud — already non-blocking via create_task
                    asyncio.create_task(_forward_to_cloud(username, f"[JOIN] {username}", goon))

                    # ── PHANTOM RADAR — live join scoring ──────────────────
                    asyncio.create_task(_radar_score_join(username, goon))

                    if goon and vampire_active:
                        await execute_block(username, "GOON_JOIN_VAMPIRE")

                except Exception as e:
                    print(f"❌ Join error: {e}")

            @client.on(CommentEvent)
            async def on_comment(event):
                try:
                    username = event.user.unique_id
                    comment  = event.comment
                    goon     = is_goon(username)
                    hostile  = is_hostile(comment)
                    script   = detect_script(comment)

                    # VOICE TRIGGER — Jess calls out a username
                    if username.lower() in WHITELIST or username.lower() == TARGET.lower():
                        await check_voice_trigger(username, comment)

                    if script and not vampire_active:
                        await trigger_vampire(f"Script detected: '{comment[:40]}'")

                    print(f"{'🚨' if goon else '📡'} [{username}]: {comment[:60]}")

                    await manager.broadcast({
                        "type":      "comment",
                        "username":  username,
                        "comment":   comment,
                        "is_goon":   goon,
                        "hostile":   hostile,
                        "action":    "BLOCK" if (goon or hostile) else "WATCH",
                        "is_threat": goon or hostile,
                        "timestamp": datetime.now().strftime("%H:%M:%S")
                    })

                    # Forward to cloud — non-blocking via create_task
                    asyncio.create_task(_forward_to_cloud(username, comment, goon))

                    if (goon or hostile) and vampire_active:
                        await execute_block(username, "HOSTILE_VAMPIRE" if hostile else "GOON_COMMENT")

                except Exception as e:
                    print(f"❌ Comment error: {e}")

            await client.start()

        except Exception as e:
            err = str(e).lower()
            # PATCH 5 — Exponential backoff prevents TikTok ghost-banning AEGIS
            # Scales with how many reconnects have already fired in the window
            reconnect_delays = [5, 10, 20, 40, 60, 60, 60]
            reconnects_so_far = ghost_lock.get_status()["reconnects_in_window"]
            delay_index = min(reconnects_so_far // 5, len(reconnect_delays) - 1)
            wait = reconnect_delays[delay_index]

            if "offline" in err or "useroffline" in type(e).__name__.lower():
                print(f"🔄 @{TARGET} offline — retrying in 30s...")
                await asyncio.sleep(30)
            else:
                print(f"🔄 Reconnecting in {wait}s... {e}")
                await asyncio.sleep(wait)


async def _radar_score_join(username: str, is_goon: bool):
    """Send live join to Phantom Radar for real-time scoring."""
    try:
        async with httpx.AsyncClient(timeout=1.0) as http:
            await http.post("http://127.0.0.1:8001/radar/score", json={
                "username": username,
                "status": "known_hostile" if is_goon else "unknown",
                "risk_score": 0.9 if is_goon else 0.1,
                "rejoins_last_hour": 1,
            })
    except:
        pass

async def _forward_to_cloud(username: str, comment: str, is_goon: bool):
    # PATCH 4 — confirmed non-blocking (called via create_task throughout)
    try:
        async with httpx.AsyncClient(timeout=3) as http:
            await http.post(f"{CLOUD_URL}/intercept", json={
                "username": username,
                "comment":  comment,
                "userId":   "",
                "is_goon":  is_goon
            })
    except:
        pass

# ─── STARTUP ──────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    print(f"""
    ██████╗ ██╗  ██╗ █████╗ ███╗   ██╗████████╗ ██████╗ ███╗   ███╗
   ██╔════╝ ██║  ██║██╔══██╗████╗  ██║╚══██╔══╝██╔═══██╗████╗ ████║
   ██║  ███╗███████║███████║██╔██╗ ██║   ██║   ██║   ██║██╔████╔██║
   ██║   ██║██╔══██║██╔══██║██║╚██╗██║   ██║   ██║   ██║██║╚██╔╝██║
   ╚██████╔╝██║  ██║██║  ██║██║ ╚████║   ██║   ╚██████╔╝██║ ╚═╝ ██║
    ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝   ╚═╝    ╚═════╝ ╚═╝     ╚═╝
    AEGIS PHANTOM — AIRLOCK ENGINE v3.1
    TARGET: @{TARGET}
    PATCHES: Client Guard | Room ID Validation | Block Retry | Exponential Backoff
    """)
    await load_goon_vault()
    asyncio.create_task(health_checkin())
    asyncio.create_task(run_monitor())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 AIRLOCK ENGINE v3.1 starting on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)
