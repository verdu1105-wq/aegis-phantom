"""
AEGIS PHANTOM — AIRLOCK ENGINE v3
Unified local engine: TikTok monitor + CWIS + FastAPI + WebSocket
Based on the working airlock_engine.py architecture
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
CLOUD_URL    = "https://aegis-phantom-974184310088.us-east1.run.app"
CLOUD_TOKEN  = os.getenv("AEGIS_CLOUD_TOKEN", "")

WHITELIST = {
    "cavalryspice", "jescavalrygal", "jescavalrygal2.0",
    "verdu1105", "wakandan_sentinel03", "diavatalks",
    "kenneth.cupps4", "mistalina7", "d4rkn8t", "hotgirlmoney"
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
]

# GATOR NAMES — auto-block on join regardless of VAMPIRE state
GATOR_PATTERNS = [
    "gator", "gatorhr", "thehrgator", "bcgator",
    "devil.dog", "devildog", "devil_dog",
    "airborne_ruc", "airborne_rucka",
    "peteynola", "petey_nola", "petey.nola",
    "ee4", "_e.4", "its_ee4",
    "penic81",
    "armybarbee",
    "vipervet",
    "mandarose",
    "rusty.the.clown", "unclerustytheclown",
]

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
        for ws in self.connections:
            try:
                await ws.send_json(data)
            except:
                dead.append(ws)
        for ws in dead:
            self.connections.remove(ws)

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
        "dashboards": len(manager.connections)
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
    global cwis_kills
    if username in blocked_accounts or username in WHITELIST:
        return

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

    # ─── REAL TIKTOK BLOCK EXECUTION ─────────────────────────────────────────
    asyncio.create_task(_tiktok_block(username))

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

async def _tiktok_block(username: str):
    """Execute real TikTok block using mod session."""
    SESSION_ID = os.getenv("TIKTOK_SESSION_ID", "")
    if not SESSION_ID:
        print(f"⚠️ No TikTok session ID — block not executed for @{username}")
        return

    headers = {
        "Cookie": f"sessionid={SESSION_ID}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.tiktok.com/",
        "Origin": "https://www.tiktok.com",
    }

    try:
        # Step 1 — get user ID from username
        async with httpx.AsyncClient(timeout=5, headers=headers) as http:
            resp = await http.get(
                f"https://www.tiktok.com/api/user/detail/",
                params={"uniqueId": username, "aid": "1988"}
            )
            data = resp.json()
            user_id = data.get("userInfo", {}).get("user", {}).get("id", "")

            if not user_id:
                print(f"⚠️ Could not find user ID for @{username}")
                return

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
                print(f"✅ TIKTOK BLOCK CONFIRMED: @{username}")
            else:
                print(f"⚠️ TikTok block failed for @{username}: {resp.status_code}")

    except Exception as e:
        print(f"⚠️ TikTok block error for @{username}: {e}")

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

async def load_goon_vault():
    global goon_vault
    try:
        import redis as redis_lib
        r = redis_lib.from_url("redis://:LrQmHLtx7RjHAeGis26@redis-10919.c284.us-east1-2.gce.cloud.redislabs.com:10919")
        raw = r.smembers("goons")
        goon_vault = set(g.decode() for g in raw)
        print(f"🛡️ Vault loaded: {len(goon_vault)} goons from Redis Cloud")
    except Exception as e:
        print(f"⚠️ Vault load failed: {e}")

async def run_monitor():
    global threat_level, vampire_active

    while True:
        try:
            client = TikTokLiveClient(unique_id=f"@{TARGET}")

            @client.on(ConnectEvent)
            async def on_connect(event):
                print(f"✅ RADAR LOCKED: @{TARGET}")
                await manager.broadcast({
                    "type": "radar_online",
                    "data": {"target": TARGET, "timestamp": datetime.now().isoformat()}
                })

            @client.on(DisconnectEvent)
            async def on_disconnect(event):
                print(f"⚠️ DISCONNECTED: @{TARGET}")
                await manager.broadcast({"type": "radar_offline", "data": {"target": TARGET}})

            @client.on(JoinEvent)
            async def on_join(event):
                try:
                    username = event.user.unique_id
                    goon     = is_goon(username)
                    gator    = is_gator(username)
                    bot_sus  = False

                    join_times.append((time.time(), goon, bot_sus))

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

                    # Forward to cloud
                    asyncio.create_task(_forward_to_cloud(username, f"[JOIN] {username}", goon))

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
                        "type": "comment",
                        "username": username,
                        "comment":  comment,
                        "is_goon":  goon,
                        "hostile":  hostile,
                        "data": {
                            "username":  username,
                            "comment":   comment,
                            "action":    "BLOCK" if (goon or hostile) else "WATCH",
                            "is_threat": goon or hostile,
                            "timestamp": datetime.now().strftime("%H:%M:%S")
                        }
                    })

                    # Forward to cloud
                    asyncio.create_task(_forward_to_cloud(username, comment, goon))

                    if (goon or hostile) and vampire_active:
                        await execute_block(username, "HOSTILE_VAMPIRE" if hostile else "GOON_COMMENT")

                except Exception as e:
                    print(f"❌ Comment error: {e}")

            await client.start()

        except Exception as e:
            err = str(e).lower()
            if "offline" in err or "useroffline" in type(e).__name__.lower():
                print(f"🔄 @{TARGET} offline — retrying in 30s...")
                await asyncio.sleep(30)
            else:
                print(f"🔄 Reconnecting... {e}")
                await asyncio.sleep(10)

async def _forward_to_cloud(username: str, comment: str, is_goon: bool):
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
    AEGIS PHANTOM — AIRLOCK ENGINE v3
    TARGET: @{TARGET}
    """)
    await load_goon_vault()
    asyncio.create_task(run_monitor())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 AIRLOCK ENGINE starting on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)
