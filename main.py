"""
AEGIS PHANTOM - SECURE CLOUD RUN BACKEND v5
JWT Authentication + Role-Based Access Control
+ Radar Lock (/api/target) - Redis-based target handoff
+ Honeypot Trap (/admin-onboarding) - Fake Linktree with IP capture
"""

import os, json, asyncio, hashlib, secrets, re
from datetime import datetime, timedelta
from typing import Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Response
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import httpx
import redis as redis_lib
import jwt
from dotenv import load_dotenv
from creator_mode import creator_router
from zeroday_monitor import zeroday_router
from youtube_monitor import yt_router 
from deep6 import deep6_router, increment_and_check 
from sitrep_router import sitrep_router
from oracle_router import oracle_router
from publish_router import publish_router
from bg_generator_router import bg_router
from stripe_router import stripe_router
from asset_image_cache import cache_router
from marketing_agent import marketing_router
from service_engineer import svc_router
from tts_router import tts_router
from theater_router import theater_router
from token_refresh_router import router as token_refresh_router
from video_intel_router import router as video_router

load_dotenv()
# --- CONFIG ---
JWT_SECRET     = os.getenv("JWT_SECRET", secrets.token_hex(32))
JWT_EXPIRY_MIN = int(os.getenv("JWT_EXPIRY_MIN", "480"))
REDIS_URL      = os.getenv("REDIS_URL", "redis://localhost:6379")
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
TIKTOK_SESSION = os.getenv("TIKTOK_SESSION_ID", "")

# --- USERS ---
USERS = {
    "vern": {
        "password_hash": hashlib.sha256("aegis2026vern".encode()).hexdigest(),
        "role": "GLOBAL_ADMIN",
        "display": "VERN"
    },
    "jess": {
        "password_hash": hashlib.sha256("cavalry2026".encode()).hexdigest(),
        "role": "MOD",
        "display": "JESS"
    },
    "d4rkn8t": {
        "password_hash": hashlib.sha256("aegis2026d4rk".encode()).hexdigest(),
        "role": "MOD",
        "display": "D4RKN8T"
    },
    "mistalina": {
        "password_hash": hashlib.sha256("aegis2026mist".encode()).hexdigest(),
        "role": "MOD",
        "display": "MISTALINA"
    },
}
try:
    r = redis_lib.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=5)
    r.ping()
    count = r.scard("goons")
    print(f"Redis connected - goons count: {count}")
except Exception as e:
    print(f"Redis unavailable: {e}")
    r = None
app = FastAPI(title="AEGIS PHANTOM Secure API v5")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(zeroday_router)
app.include_router(cache_router)
app.include_router(marketing_router)
app.include_router(svc_router)
app.include_router(stripe_router)
app.include_router(yt_router)
app.include_router(deep6_router)
app.include_router(sitrep_router)
app.include_router(oracle_router)
app.include_router(publish_router)
app.include_router(bg_router)
app.include_router(tts_router)
app.include_router(theater_router)
app.include_router(token_refresh_router)
app.include_router(video_router)
security = HTTPBearer(auto_error=False)
# --- JWT HELPERS ---
def create_token(username: str, role: str, display: str) -> str:
    payload = {
        "sub": username,
        "role": role,
        "display": display,
        "exp": datetime.utcnow() + timedelta(minutes=JWT_EXPIRY_MIN),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

@app.get("/api/rss/fetch")
async def rss_fetch(url: str):
    """CORS-safe RSS proxy for SitRep frontend with og:image extraction."""
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "User-Agent": "SitRep/2.0 (sitrep.media; intelligence aggregator)",
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
            })
            xml = resp.text

            # Extract article links and fetch og:image for each
            import xml.etree.ElementTree as ET
            import re

            try:
                root = ET.fromstring(xml)
                items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
                for item in items[:8]:
                    # Get article link
                    link_el = item.find("link")
                    link = link_el.text if link_el is not None else None
                    if not link:
                        continue
                    # Skip if image already in enclosure or media
                    if item.find("enclosure") is not None:
                        continue
                    # Fetch og:image from article page
                    try:
                        art = await client.get(link.strip(), timeout=3, follow_redirects=True)
                        og = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\'](https?://[^"\']+)["\']', art.text)
                        if not og:
                            og = re.search(r'<meta[^>]+content=["\'](https?://[^"\']+\.(?:jpg|jpeg|png|webp))["\'][^>]+property=["\']og:image["\']', art.text)
                        if og:
                            # Inject image as enclosure into RSS item
                            enc = ET.SubElement(item, "enclosure")
                            enc.set("url", og.group(1))
                            enc.set("type", "image/jpeg")
                    except Exception:
                        pass
            except Exception:
                pass

            return Response(content=ET.tostring(root, encoding='unicode') if 'root' in dir() else xml.encode(),
                          media_type="application/xml")
    except Exception as e:
        return Response(content=f"<error>{e}</error>", media_type="application/xml")

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = verify_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return payload

def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "GLOBAL_ADMIN":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

def get_optional_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Optional[dict]:
    if not credentials:
        return None
    return verify_token(credentials.credentials)

# --- CONNECTION MANAGER ---
# -- REDIS PUB/SUB LISTENER ----------------------------------------------------
_pubsub_started = False

async def redis_pubsub_listener():
    if not r:
        return
    try:
        import redis as redis_lib
        ps = redis_lib.from_url(os.getenv("REDIS_URL",""), decode_responses=True, socket_connect_timeout=5)
        pubsub = ps.pubsub()
        pubsub.subscribe("aegis:feed")
        print("Redis pub/sub listener active on aegis:feed")
        loop = asyncio.get_event_loop()
        while True:
            msg = await loop.run_in_executor(None, pubsub.get_message, True, 0.1)
            if msg and msg.get("type") == "message":
                try:
                    await manager.broadcast(json.loads(msg["data"]))
                except Exception as e:
                    pass
            await asyncio.sleep(0.05)
    except Exception as e:
        print(f"Pub/sub error: {e}")

class ConnectionManager:
    def __init__(self):
        self.connections: list[tuple[WebSocket, dict]] = []

    async def connect(self, ws: WebSocket, user: dict):
        await ws.accept()
        self.connections.append((ws, user))

    def disconnect(self, ws: WebSocket):
        self.connections = [(w, u) for w, u in self.connections if w != ws]

    async def broadcast(self, data: dict):
        dead = []
        for ws, user in self.connections:
            try:
                await ws.send_json(data)
            except:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    def get_online_users(self) -> list:
        return [{"display": u.get("display"), "role": u.get("role")} for _, u in self.connections]

manager = ConnectionManager()

# --- ROUTES ---

@app.get("/")
async def root():
    return {"status": "AEGIS-PHANTOM-v5", "time": datetime.utcnow().isoformat()}

@app.get("/health")
async def health():
    goon_count = 0
    target = None
    mode = "WATCH"
    radar_locked = False
    if r:
        try:
            goon_count = r.scard("goons")
            target = r.get("aegis:target")
            mode = r.get("aegis:mode") or "WATCH"
            radar_locked = r.get("aegis:radar_locked") == "1"
        except Exception as ex:
            print(f"Health Redis error: {ex}")
    pi_online = False
    pi_last_checkin = None
    if r:
        try:
            pi_online = r.get("aegis:pi_online") == "1"
            pi_last_checkin = r.get("aegis:pi_last_checkin")
        except:
            pass
    return {
        "status": "online",
        "goon_vault": goon_count,
        "dashboards": len(manager.connections),
        "target": target,
        "mode": mode,
        "radar_locked": radar_locked,
        "pi_online": pi_online,
        "pi_last_checkin": pi_last_checkin,
        "time": datetime.utcnow().isoformat()
    }

# --- AUTH ---
@app.post("/api/auth/login")
async def login(request: Request):
    data = await request.json()
    username = data.get("username", "").lower().strip()
    password = data.get("password", "")
    if username not in USERS:
        await asyncio.sleep(1)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    user = USERS[username]
    pw_hash = hashlib.sha256(password.encode()).hexdigest()
    if pw_hash != user["password_hash"]:
        await asyncio.sleep(1)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_token(username, user["role"], user["display"])
    return {"token": token, "display": user["display"], "role": user["role"], "expires_in": JWT_EXPIRY_MIN * 60}

@app.post("/api/auth/refresh")
async def refresh_token(user: dict = Depends(get_current_user)):
    token = create_token(user["sub"], user["role"], user["display"])
    return {"token": token, "expires_in": JWT_EXPIRY_MIN * 60}

@app.get("/api/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return {"username": user["sub"], "role": user["role"], "display": user["display"]}

# --- TARGET / RADAR LOCK ---
@app.post("/api/target")
async def set_target(request: Request):
    data = await request.json()
    target = data.get("target", "").strip().lstrip("@")
    if not target:
        raise HTTPException(status_code=400, detail="Target required")
    if r:
        r.set("aegis:target", target)
        r.set("aegis:radar_locked", "1")
    await manager.broadcast({"type": "radar_online", "target": target, "by": user["display"]})
    return {"status": "LOCKED", "target": target}

@app.delete("/api/target")
async def clear_target(user: dict = Depends(require_admin)):
    if r:
        r.delete("aegis:target")
        r.set("aegis:radar_locked", "0")
    await manager.broadcast({"type": "radar_offline"})
    return {"status": "cleared"}

@app.get("/api/target")
async def get_target(user: dict = Depends(get_current_user)):
    target = None
    locked = False
    if r:
        target = r.get("aegis:target")
        locked = r.get("aegis:radar_locked") == "1"
    return {"target": target, "locked": locked}

# --- MODE ---
@app.post("/api/mode")
async def set_mode(request: Request, user: dict = Depends(get_current_user)):
    data = await request.json()
    mode = data.get("mode", "WATCH").upper()
    if r:
        r.set("aegis:mode", mode)
    await manager.broadcast({"type": "mode_change", "mode": mode, "by": user["display"]})
    return {"status": "ok", "mode": mode}

# --- GOONS ---
@app.get("/api/goons")
async def get_goons():
    if not r:
        return {"goons": [], "count": 0}
    goons = sorted(list(r.smembers("goons")))
    return {"goons": goons, "count": len(goons)}

@app.post("/api/goons")
async def add_goon(request: Request, user: dict = Depends(get_current_user)):
    data = await request.json()
    username = data.get("username", "").lower().strip().lstrip("@")
    if not username:
        raise HTTPException(status_code=400, detail="Username required")
    if r:
        r.sadd("goons", username)
    await manager.broadcast({"type": "goon_added", "username": username, "added_by": user["display"]})
    # Deep6 -- trigger pattern analysis every 5 uploads
    if increment_and_check():
        pass  # deep6 auto-trigger removed
    return {"status": "added", "username": username}

@app.delete("/api/goons/{username}")
async def remove_goon(username: str, user: dict = Depends(require_admin)):
    if r:
        r.srem("goons", username.lower())
    return {"status": "removed", "username": username}

@app.get("/api/vault/count")
async def vault_count(user: dict = Depends(get_current_user)):
    """Returns live vault + goon counts from Redis."""
    if not r:
        return {"count": 0, "goons": 0, "vault": 0}
    goons = r.scard("goons") or 0
    vault = r.scard("vault:index") or 0
    total = max(goons, vault)
    return {"count": total, "goons": goons, "vault": vault}

# --- WHITELIST ---
@app.get("/api/whitelist")
async def get_whitelist(user: dict = Depends(require_admin)):
    if not r:
        return {"whitelist": []}
    wl = sorted(list(r.smembers("whitelist")))
    return {"whitelist": wl}

@app.post("/api/whitelist")
async def add_whitelist(request: Request, user: dict = Depends(require_admin)):
    data = await request.json()
    username = data.get("username", "").lower().strip().lstrip("@")
    if not username:
        raise HTTPException(status_code=400, detail="Username required")
    if r:
        r.sadd("whitelist", username)
    return {"status": "added", "username": username}

# --- BLOCK ---
@app.post("/api/block")
async def block_user(request: Request, user: dict = Depends(get_current_user)):
    data = await request.json()
    username = data.get("username", "").lstrip("@")
    reason = data.get("reason", "manual")
    if not username:
        raise HTTPException(status_code=400, detail="Username required")
    if r:
        r.sadd("goons", username.lower())
        r.lpush("block_log", json.dumps({
            "username": username, "reason": reason,
            "by": user["display"], "time": datetime.utcnow().isoformat()
        }))
    await manager.broadcast({
        "type": "cwis_block", "username": username, "reason": reason,
        "by": user["display"], "kills": r.llen("block_log") if r else 0
    })
    asyncio.create_task(_execute_tiktok_block(username))
    return {"status": "blocked", "username": username}

async def _execute_tiktok_block(username: str):
    if not TIKTOK_SESSION:
        return
    headers = {
        "Cookie": f"sessionid={TIKTOK_SESSION}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.tiktok.com/",
    }
    try:
        async with httpx.AsyncClient(timeout=5, headers=headers) as http:
            resp = await http.get("https://www.tiktok.com/api/user/detail/", params={"uniqueId": username, "aid": "1988"})
            user_id = resp.json().get("userInfo", {}).get("user", {}).get("id", "")
            if not user_id:
                return
            await http.post("https://www.tiktok.com/api/commit/follow/user/", params={"user_id": user_id, "type": 3, "aid": "1988"})
    except Exception as e:
        print(f"TikTok block error: {e}")

# --- VISION API PROXY ---
@app.post("/api/vision/scan")
async def vision_scan(request: Request, user: dict = Depends(get_current_user)):
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=503, detail="Vision API not configured")
    data = await request.json()
    image_b64 = data.get("image_b64", "")
    media_type = data.get("media_type", "image/jpeg")
    if not image_b64:
        raise HTTPException(status_code=400, detail="Image data required")
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.post(
                "https://api.anthropic.com/v1/messages",
                headers={"Content-Type": "application/json", "x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "anthropic-beta": "messages-2023-12-15"},
                json={"model": "claude-haiku-4-5-20251001", "max_tokens": 500, "messages": [{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                    {"type": "text", "text": "Extract all TikTok usernames from this screenshot. Look for usernames shown after @ symbols AND usernames displayed on profile pages without @. Also look for display names next to username handles. Return ONLY the usernames one per line without the @ symbol. If none found return none."}
                ]}]}
            )
            result = resp.json()
            print(f"VISION DEBUG: {result}")
            text = result.get("content", [{}])[0].get("text", "") if result.get("content") else ""
            usernames = [u.strip().replace("@","").lower() for u in text.strip().split("\n") if u.strip() and u.strip().lower() != "none"]
            return {"usernames": usernames, "raw": text if text else str(result)}
    except Exception as e:
        return {"usernames": [], "raw": f"ERROR: {type(e).__name__}: {str(e)}"}
# --- INTEL / INTERCEPT ---
@app.post("/intercept")
async def intercept(request: Request):
    data = await request.json()
    username = data.get("username", "")
    event_id = data.get("event_id", "")
    is_goon = False
    if r and username:
        is_goon = r.sismember("goons", username.lower())
    # Dedup -- skip if we've seen this event_id in last 10s
    if event_id and r:
        dedup_key = f"aegis:dedup:{event_id}"
        if r.exists(dedup_key):
            return {"status": "duplicate"}
        r.setex(dedup_key, 10, "1")
    payload = {
        "type":      data.get("type", "comment"),
        "username":  username,
        "comment":   data.get("comment", ""),
        "is_goon":   is_goon,
        "hostile":   data.get("hostile", False),
        "action":    "BLOCK" if is_goon else "WATCH",
        "is_threat": is_goon,
        "timestamp": datetime.utcnow().strftime("%H:%M:%S"),
        "event_id":  event_id
    }
    await manager.broadcast(payload)
    if r:
        try: r.publish("aegis:feed", json.dumps(payload))
        except: pass
    return {"status": "ok"}

@app.post("/intel")
async def intel(request: Request):
    data = await request.json()
    intel_type = data.get("type", "")
    await manager.broadcast({"type": "intel", "intel_type": intel_type, **data})
    return {"status": "ok"}

@app.post("/api/cwis/block")
async def cwis_block(request: Request):
    data = await request.json()
    username = data.get("username", "")
    if r and username:
        r.sadd("goons", username.lower())
    await manager.broadcast({"type": "cwis_block", "username": username, "reason": data.get("reason", "CWIS"), "kills": data.get("evidence", {}).get("kill", 0)})
    return {"status": "ok"}

# --- WEBSOCKET ---
@app.websocket("/ws/dashboard")
async def ws_dashboard(ws: WebSocket, token: str = None):
    if not token:
        await ws.close(code=4001)
        return
    payload = verify_token(token)
    if not payload:
        await ws.close(code=4001)
        return
    global _pubsub_started
    if not _pubsub_started:
        _pubsub_started = True
        asyncio.create_task(redis_pubsub_listener())
        print("Redis pub/sub listener started on first WS connect")
    await manager.connect(ws, payload)
    try:
        await ws.send_json({"type": "online_users", "users": manager.get_online_users()})
        await manager.broadcast({"type": "user_joined", "display": payload["display"], "role": payload["role"]})
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=30)
            except asyncio.TimeoutError:
                await ws.send_json({"type": "ping"})
    except WebSocketDisconnect:
        manager.disconnect(ws)
        await manager.broadcast({"type": "user_left", "display": payload["display"]})
    except Exception:
        manager.disconnect(ws)

@app.websocket("/ws/dashboard/{creator}")
async def ws_dashboard_legacy(ws: WebSocket, creator: str, token: str = None):
    await ws_dashboard(ws, token)

@app.websocket("/ws/live/{creator}")
async def ws_live(ws: WebSocket, creator: str, token: str = None):
    await ws_dashboard(ws, token)

# --- EVIDENCE ---
@app.get("/api/evidence")
async def get_evidence(user: dict = Depends(require_admin)):
    if not r:
        return {"blocks": [], "count": 0}
    raw = r.lrange("block_log", 0, -1)
    blocks = [json.loads(b) for b in raw]
    return {"blocks": blocks, "count": len(blocks)}

# --- FOLLOWERS ---
@app.post("/api/followers/import")
async def import_followers(request: Request, user: dict = Depends(require_admin)):
    data = await request.json()
    followers = data.get("followers", [])
    if r:
        for f in followers:
            r.sadd("whitelist", f.lower().lstrip("@"))
    return {"status": "imported", "count": len(followers)}

# --- HONEYPOT TRAP ---
LINKTREE_HTML = (
    "<!DOCTYPE html><html lang='en'><head>"
    "<meta charset='UTF-8'>"
    "<meta name='viewport' content='width=device-width, initial-scale=1.0'>"
    "<title>Jess Cavalry | Links</title>"
    "<style>"
    "*{margin:0;padding:0;box-sizing:border-box}"
    "body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;background:#1a2210;min-height:100vh;padding:2rem 1rem 3rem}"
    ".wrap{max-width:480px;margin:0 auto}"
    ".camo{height:6px;width:100%;background:repeating-linear-gradient(90deg,#3d5229 0,#3d5229 20px,#2a3a1a 20px,#2a3a1a 40px,#4a3820 40px,#4a3820 60px,#1a2210 60px,#1a2210 80px);margin-bottom:2rem;border-radius:3px}"
    ".avatar{width:88px;height:88px;border-radius:50%;margin:0 auto 1rem;background:#2d3b1e;border:3px solid #5a6e3a;display:flex;align-items:center;justify-content:center}"
    ".avatar svg{width:60px;height:60px;fill:#8a9e6a}"
    ".name{text-align:center;font-size:18px;font-weight:600;color:#e8eddf;margin-bottom:4px}"
    ".handle{text-align:center;font-size:13px;color:#8a9e6a;margin-bottom:6px}"
    ".badge{text-align:center;margin-bottom:8px}"
    ".badge span{display:inline-block;background:#3d5229;color:#8fc95a;font-size:10px;padding:2px 8px;border-radius:10px;border:1px solid #5a7a35}"
    ".bio{text-align:center;font-size:13px;color:#b5bfaa;margin-bottom:1.5rem;line-height:1.5}"
    ".btn{display:block;width:100%;padding:14px 20px;margin-bottom:12px;border-radius:8px;text-align:center;text-decoration:none;font-size:14px;font-weight:500}"
    ".p{background:#3d5229;color:#d4e0c0;border:1px solid #5a6e3a}"
    ".s{background:#2a3a1a;color:#b5c99a;border:1px solid #445530}"
    ".a{background:#4a3820;color:#e0c89a;border:1px solid #6e5530}"
    ".footer{text-align:center;margin-top:2rem;font-size:11px;color:#5a6e3a}"
    "</style></head><body>"
    "<div class='wrap'>"
    "<div class='camo'></div>"
    "<div class='avatar'>"
    "<svg viewBox='0 0 100 120' xmlns='http://www.w3.org/2000/svg'>"
    "<ellipse cx='50' cy='30' rx='22' ry='22'/>"
    "<path d='M15 120 Q15 70 50 65 Q85 70 85 120Z'/>"
    "<rect x='30' y='8' width='40' height='12' rx='4' fill='#6a7e4a'/>"
    "<rect x='28' y='14' width='44' height='6' rx='3' fill='#5a6e3a'/>"
    "</svg></div>"
    "<div class='name'>Jess Cavalry</div>"
    "<div class='handle'>@JesArmygal</div>"
    "<div class='badge'><span>U.S. Army Veteran</span></div>"
    "<div class='bio'>Veteran. Patriot. Truth teller.<br>70k+ strong - back and unsilenced.</div>"
    "<a class='btn p' href='https://www.tiktok.com/@cavalryspice'>TikTok - @cavalryspice</a>"
    "<a class='btn p' href='https://1stcavalrygal.substack.com/'>Substack - 1stcavalrygal</a>"
    "<a class='btn a' href='https://www.tiktok.com/@cavalryspice'>Merch Store - Coming Soon</a>"
    "<a class='btn s' href='https://www.tiktok.com/@cavalryspice'>Original Account - @JesArmygal (70k)</a>"
    "<a class='btn s' href='https://www.tiktok.com/@cavalryspice'>Support the Mission</a>"
    "<div class='footer'>linktree</div>"
    "</div></body></html>"
)

@app.get("/admin-onboarding")
async def honeypot_trap(request: Request):
    client_ip = request.headers.get("x-forwarded-for", request.client.host)
    if "," in client_ip:
        client_ip = client_ip.split(",")[0].strip()
    user_agent = request.headers.get("user-agent", "unknown")
    referer = request.headers.get("referer", "direct")
    timestamp = datetime.utcnow().isoformat()

    geo = {}
    try:
        async with httpx.AsyncClient(timeout=5) as http:
            resp = await http.get(f"http://ip-api.com/json/{client_ip}")
            geo = resp.json()
    except:
        pass

    trap_data = {
        "event": "HONEYPOT_TRIGGERED",
        "ip": client_ip,
        "user_agent": user_agent,
        "referer": referer,
        "timestamp": timestamp,
        "severity": "CRITICAL",
        "city": geo.get("city", "unknown"),
        "region": geo.get("regionName", "unknown"),
        "country": geo.get("country", "unknown"),
        "isp": geo.get("isp", "unknown"),
        "lat": geo.get("lat", 0),
        "lon": geo.get("lon", 0),
    }

    if r:
        r.lpush("honeypot_log", json.dumps(trap_data))
        r.ltrim("honeypot_log", 0, 999)

    await manager.broadcast({
        "type": "intel",
        "intel_type": "HONEYPOT_TRIGGERED",
        "message": f"TRAP: {client_ip} - {geo.get('city','?')}, {geo.get('regionName','?')} - {user_agent[:50]}",
        "data": trap_data
    })

    print(f"HONEYPOT: {client_ip} | {geo.get('city')}, {geo.get('regionName')} | {geo.get('isp')}")
    return HTMLResponse(content=LINKTREE_HTML)

@app.get("/api/honeypot/logs")
async def get_honeypot_logs(user: dict = Depends(require_admin)):
    if not r:
        return {"hits": [], "count": 0}
    raw = r.lrange("honeypot_log", 0, 49)
    hits = [json.loads(h) for h in raw]
    return {"hits": hits, "count": len(hits)}

# AEGIS SENTRY - AI Intelligence Proxy
@app.post("/api/intelligence")
async def intelligence_proxy(request: Request):
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=503, detail="AI engine not configured")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body")
    prompt = body.get("prompt", "").strip()
    do_stream = body.get("stream", False)
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt required")
    if len(prompt) > 8000:
        raise HTTPException(status_code=400, detail="prompt too long")

    headers = {
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01"
    }
    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 2048,
        "stream": do_stream,
        "messages": [{"role": "user", "content": prompt}]
    }

    if do_stream:
        from fastapi.responses import StreamingResponse
        async def stream_gen():
            async with httpx.AsyncClient(timeout=60) as client:
                async with client.stream(
                    "POST",
                    "https://api.anthropic.com/v1/messages",
                    json=payload,
                    headers=headers
                ) as resp:
                    async for chunk in resp.aiter_bytes():
                        yield chunk
        return StreamingResponse(
            stream_gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )
    else:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                json=payload,
                headers=headers
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail="AI engine error")
            return JSONResponse(content=resp.json())


# -- WIKIPEDIA IMAGE PROXY -----------------------------------------------------
ASSET_WIKI_MAP = {
    # Military hardware
    "f35":          "F-35_Lightning_II",
    "su35":         "Sukhoi_Su-35",
    "shahed":       "Shahed_136",
    "carrier":      "Gerald_R._Ford-class_aircraft_carrier",
    "burke":        "Arleigh_Burke-class_destroyer",
    "irgc":         "Seraj-class_speedboat",
    "tanker":       "Suezmax",
    "iskander":     "9K720_Iskander",
    "himars":       "M142_HIMARS",
    "drone":        "Shahed_136",
    "missile":      "9K720_Iskander",
    "navy":         "United_States_Navy",
    "destroyer":    "Arleigh_Burke-class_destroyer",
    "submarine":    "Virginia-class_submarine",
    "tank":         "M1_Abrams",
    "leopard":      "Leopard_2",
    "apache":       "Boeing_AH-64_Apache",
    "patriot":      "MIM-104_Patriot",
    "radar":        "AN/TPY-2",
    "stealth":      "F-35_Lightning_II",
    "aircraft":     "F-35_Lightning_II",
    "jet":          "F-35_Lightning_II",
    # Cyber / tech
    "malware":      "Malware",
    "ransomware":   "Ransomware",
    "cyberav3ngers":"CyberAv3ngers",
    "cisa":         "Cybersecurity_and_Infrastructure_Security_Agency",
    "cve":          "Common_Vulnerabilities_and_Exposures",
    "hack":         "Cyberattack",
    "cyber":        "Cyberwarfare",
    "teams":        "Microsoft_Teams",
    "microsoft":    "Microsoft",
    "supply":       "Supply_chain_attack",
    "network":      "Computer_network",
    "router":       "Router_(computing)",
    "phishing":     "Phishing",
    "zero":         "Zero-day_(computing)",
    "botnet":       "Botnet",
    # Geopolitical
    "iran":         "Iran",
    "russia":       "Russia",
    "china":        "China",
    "ukraine":      "Ukraine",
    "israel":       "Israel",
    "taiwan":       "Taiwan",
    "nato":         "NATO",
    "houthi":       "Houthis",
    "irgc":         "Islamic_Revolutionary_Guard_Corps",
    "hormuz":       "Strait_of_Hormuz",
    "gulf":         "Persian_Gulf",
    "korea":        "North_Korea",
    # Economic
    "oil":          "Petroleum",
    "crude":        "Petroleum",
    "brent":        "Brent_Crude",
    "opec":         "OPEC",
    "sanctions":    "Economic_sanctions",
    "treasury":     "United_States_Department_of_the_Treasury",
    "ofac":         "Office_of_Foreign_Assets_Control",
    "lng":          "Liquefied_natural_gas",
    "pipeline":     "Pipeline_transport",
    # Political
    "senate":       "United_States_Senate",
    "congress":     "United_States_Congress",
    "pentagon":     "The_Pentagon",
    "cia":          "Central_Intelligence_Agency",
    "fbi":          "Federal_Bureau_of_Investigation",
    "nsa":          "National_Security_Agency",
    "ceasefire":    "Ceasefire",
    "trump":        "Donald_Trump",
    "biden":        "Joe_Biden",
    # Fallbacks by category
    "military":     "United_States_Armed_Forces",
    "political":    "United_States_Capitol",
    "economic":     "New_York_Stock_Exchange",
    "vbss":         "Visit,_board,_search_and_seizure",
}

# -- BRIEF IMAGE KEY EXTRACTOR -------------------------------------------------
def extract_image_key(title: str, category: str) -> str:
    """Pick the best ASSET_WIKI_MAP key from a brief title."""
    title_lower = title.lower()
    # Check each map key against the title words
    for key in ASSET_WIKI_MAP:
        if key in title_lower:
            return key
    # Category fallback
    return category




@app.get("/api/image/proxy")
async def proxy_image_url(url: str):
    """Proxy any image URL through Cloud Run -- bypasses CORS for the browser."""
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; SitRep/1.0)",
                "Referer": "https://en.wikipedia.org/"
            })
            if resp.status_code != 200:
                return JSONResponse(status_code=resp.status_code, content={"error": "Image fetch failed"})
            from fastapi.responses import Response
            return Response(
                content=resp.content,
                media_type=resp.headers.get("content-type", "image/jpeg"),
                headers={"Cache-Control": "public, max-age=86400", "Access-Control-Allow-Origin": "*"}
            )
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/image/{asset_key}")
async def get_asset_image(asset_key: str):
    """Proxy Wikipedia REST API to get asset images -- bypasses browser CORS."""
    article = ASSET_WIKI_MAP.get(asset_key.lower(), asset_key.replace("-", "_"))
    wiki_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{article}"
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            resp = await client.get(
                wiki_url,
                headers={"User-Agent": "SitRep/1.0 (sitrep.media; intelligence platform)"}
            )
            if resp.status_code != 200:
                return JSONResponse(status_code=404, content={"error": f"Article not found: {article}"})
            data = resp.json()
            img_url = None
            if "originalimage" in data:
                img_url = data["originalimage"]["source"]
            elif "thumbnail" in data:
                img_url = data["thumbnail"]["source"]
            if not img_url:
                return JSONResponse(status_code=404, content={"error": "No image in article"})
            return {
                "url": img_url,
                "title": data.get("title", article),
                "description": data.get("description", ""),
                "extract": data.get("extract", "")[:300],
                "wiki_url": f"https://en.wikipedia.org/wiki/{article}"
            }
    except httpx.TimeoutException:
        return JSONResponse(status_code=504, content={"error": "Wikipedia timeout"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

# -- IMAGEN DEBUG ENDPOINT -----------------------------------------------------

@app.get("/api/generate/image")
async def generate_image_proxy(prompt: str, style: str = "cinematic", seed: int = 0):
    """
    Server-side Pollinations proxy -- bypasses browser rate limits.
    Calls Pollinations from Cloud Run IP, returns image as base64 PNG.
    Results cached in Redis for 1 hour to avoid duplicate generation.
    """
    import hashlib, base64, random

    # Build cache key
    cache_key = f"imgcache:{hashlib.md5((prompt+style).encode()).hexdigest()[:16]}"

    # Check Redis cache first
    try:
        cached = r.get(cache_key)
        if cached:
            return JSONResponse({"success": True, "image": cached, "cached": True})
    except Exception:
        pass

    # Style suffix map
    style_map = {
        "cinematic": "cinematic lighting, film grain, chiaroscuro, photorealistic",
        "dramatic":  "dramatic storm lighting, high contrast, powerful dark atmosphere",
        "editorial": "editorial photography, authoritative, dark background, dramatic shadows",
        "tactical":  "dark tactical aesthetic, infrared lighting, surveillance, industrial",
        "lego":      "LEGO minifigure style, photorealistic LEGO plastic bricks, highly detailed LEGO set",
        "cartoon":   "animated cartoon style, bold outlines, vibrant colors, Pixar quality",
        "anime":     "anime style illustration, dramatic lighting, Studio Ghibli quality",
        "pixel":     "pixel art style, retro 16-bit aesthetic, vibrant colors",
    }
    style_tag = style_map.get(style, style_map["cinematic"])
    full_prompt = f"{prompt}, {style_tag}, no watermark"
    actual_seed = seed if seed else random.randint(1, 999999)

    url = (
        f"https://image.pollinations.ai/prompt/{httpx.URL(full_prompt).params}"
        f"?width=1344&height=756&seed={actual_seed}&nologo=true&enhance=true"
    )
    # Build URL properly
    import urllib.parse
    encoded_prompt = urllib.parse.quote(full_prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1344&height=756&seed={actual_seed}&nologo=true&enhance=true"

    try:
        async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "SitRep/2.0 (sitrep.media)"})
            if resp.status_code != 200:
                return JSONResponse({"success": False, "error": f"Pollinations returned {resp.status_code}"})
            # Encode as base64
            b64 = base64.b64encode(resp.content).decode("utf-8")
            data_url = f"data:image/jpeg;base64,{b64}"
            # Cache for 1 hour
            try:
                r.setex(cache_key, 3600, data_url)
            except Exception:
                pass
            return JSONResponse({
                "success": True,
                "image": data_url,
                "cached": False,
                "seed": actual_seed
            })
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# --- DEEP6 INTEL ENGINE -------------------------------------------------------

@app.post("/api/health/checkin")
async def pi_checkin(request: Request):
    """Pi-top health checkin -- called every 60s by airlock_engine."""
    data = await request.json()
    if r:
        r.set("aegis:pi_online", "1")
        r.set("aegis:pi_last_checkin", datetime.utcnow().isoformat())
        r.expire("aegis:pi_online", 120)  # Expires in 2min if no checkin
    target = data.get("target", "")
    kills  = data.get("kills", 0)
    goons  = data.get("goons", 0)
    if r:
        r.set("aegis:pi_online", "1")
        r.set("aegis:pi_last_checkin", datetime.utcnow().isoformat())
        r.expire("aegis:pi_online", 120)
        if target: r.set("aegis:target", target)
    await manager.broadcast({
        "type":      "pitop_online",
        "pi_online": True,
        "target":    target,
        "kills":     kills,
        "goons":     goons,
        "timestamp": datetime.utcnow().isoformat()
    })
    return {"status": "ok"}

@app.get("/api/health/pi")
async def pi_health():
    """Check Pi-top status."""
    if not r:
        return {"pi_online": False}
    online = r.get("aegis:pi_online") == "1"
    last = r.get("aegis:pi_last_checkin")
    return {"pi_online": online, "last_checkin": last}

@app.post("/api/target/set")
async def set_target_dynamic(request: Request, user: dict = Depends(get_current_user)):
    """Dynamic target switch -- broadcasts to Pi-top via Redis pub/sub."""
    data = await request.json()
    target = data.get("target", "").strip().lstrip("@").lower()
    if not target:
        raise HTTPException(status_code=400, detail="Target required")
    if r:
        r.set("aegis:target", target)
        r.set("aegis:radar_locked", "1")
        r.publish("aegis:target_switch", target)  # Pi-top subscribes to this
    await manager.broadcast({"type": "radar_locked", "data": {"target": target}, "by": user["display"]})
    return {"status": "ok", "target": target}

@app.get("/api/deep6/profile/{username}")
async def deep6_profile(username: str, user: dict = Depends(get_current_user)):
    """Get Deep6 intel profile for a threat actor."""
    if not r:
        return {"error": "Redis unavailable"}
    u = username.lstrip("@").lower()
    profile = r.get(f"deep6:profile:{u}")
    if not profile:
        return {"error": "no profile found", "username": u}
    return json.loads(profile)


# -- MOLE STATION ENDPOINTS ---------------------------------------------------
@app.post("/api/mole/deploy")
async def mole_deploy(request: Request, user: dict = Depends(get_current_user)):
    data = await request.json()
    if not r: return {"error": "Redis unavailable"}
    mission = {
        "agent":       data.get("agent",""),
        "target":      data.get("target",""),
        "keywords":    data.get("keywords", ["jess","cavalry","jarmygal"]),
        "operator":    data.get("operator", user.get("display","SYSTEM")),
        "status":      "ACTIVE",
        "deployed_at": datetime.utcnow().isoformat()
    }
    r.hset(f"mole:mission:{mission['agent']}", mapping={
        k: json.dumps(v) if isinstance(v, list) else v for k,v in mission.items()
    })
    r.sadd("mole:active_missions", mission["agent"])
    await manager.broadcast({"type":"mole_deployed","mission":mission})
    return {"status":"deployed","mission":mission}

@app.get("/api/mole/missions")
async def mole_missions(user: dict = Depends(get_current_user)):
    if not r: return {"missions":[]}
    agents = r.smembers("mole:active_missions") or set()
    missions = []
    for a in agents:
        m = r.hgetall(f"mole:mission:{a}")
        if m:
            if "keywords" in m:
                try: m["keywords"] = json.loads(m["keywords"])
                except: pass
            missions.append(m)
    return {"missions": missions, "count": len(missions)}

@app.post("/api/mole/recall")
async def mole_recall(request: Request, user: dict = Depends(get_current_user)):
    data = await request.json()
    agent = data.get("agent","")
    if r:
        r.srem("mole:active_missions", agent)
        r.hset(f"mole:mission:{agent}", "status", "RECALLED")
    return {"status":"recalled","agent":agent}

@app.post("/api/mole/report")
async def mole_report(request: Request):
    """Receive intel report from MOLE agent."""
    data = await request.json()
    if not r: return {"error":"Redis unavailable"}
    report = {
        "agent":           data.get("agent",""),
        "target_stream":   data.get("target_stream",""),
        "trigger_keyword": data.get("trigger_keyword",""),
        "context":         data.get("context",""),
        "usernames":       json.dumps(data.get("usernames",[])),
        "timestamp":       datetime.utcnow().isoformat()
    }
    r.lpush("mole:reports", json.dumps(report))
    r.ltrim("mole:reports", 0, 499)
    for u in data.get("usernames",[]):
        r.sadd("goons", u.lower())
    await manager.broadcast({"type":"mole_report","report":report})
    return {"status":"received","usernames_added":len(data.get("usernames",[]))}

@app.get("/api/mole/reports")
async def mole_reports_get(agent: str = None, user: dict = Depends(get_current_user)):
    if not r: return {"reports":[]}
    raw = r.lrange("mole:reports", 0, 99)
    reports = []
    for rr in raw:
        try:
            rep = json.loads(rr)
            if "usernames" in rep:
                try: rep["usernames"] = json.loads(rep["usernames"])
                except: pass
            if agent and rep.get("agent") != agent:
                continue
            reports.append(rep)
        except: pass
    return {"reports":reports,"count":len(reports)}

@app.post("/api/mole/pool/add")
async def mole_pool_add(request: Request, user: dict = Depends(get_current_user)):
    data = await request.json()
    handle = data.get("handle","").lstrip("@").lower()
    if r: r.sadd("mole:burner_pool", handle)
    return {"status":"added","handle":handle}

@app.get("/api/mole/pool")
async def mole_pool(user: dict = Depends(get_current_user)):
    if not r: return {"pool":[]}
    pool = list(r.smembers("mole:burner_pool") or set())
    return {"pool":pool,"count":len(pool)}

# -- SPECTOR RECON ENDPOINTS ---------------------------------------------------
@app.get("/api/spector/profile")
async def spector_profile(username: str, user: dict = Depends(get_current_user)):
    """Full SPECTOR intelligence profile for a username."""
    if not r:
        return {"error": "Redis unavailable"}
    u = username.lstrip("@").lower()
    in_vault   = r.sismember("goons", u)
    in_wl      = r.sismember("whitelist", u)
    # Confirmed burners
    burner_keys = r.smembers(f"spector:burners:{u}") or set()
    burners = []
    for bk in burner_keys:
        b = r.hgetall(f"spector:burner:{u}:{bk}")
        if b: burners.append(b)
    # Session history
    sessions = []
    raw_sessions = r.lrange(f"spector:sessions:{u}", 0, 49)
    for s in raw_sessions:
        try: sessions.append(json.loads(s))
        except: pass
    # Block history
    blocks = []
    raw_blocks = r.lrange(f"spector:blocks:{u}", 0, 19)
    for b in raw_blocks:
        try: blocks.append(json.loads(b))
        except: pass
    # Deep6 profile
    d6 = r.get(f"deep6:profile:{u}")
    deep6 = json.loads(d6) if d6 else {}
    return {
        "username":          u,
        "in_vault":          in_vault,
        "in_whitelist":      in_wl,
        "confirmed_burners": burners,
        "sessions":          sessions,
        "blocks":            blocks,
        "deep6":             deep6,
        "threat_score":      deep6.get("threat_score", 0)
    }

@app.post("/api/spector/link_burner")
async def spector_link_burner(request: Request, user: dict = Depends(get_current_user)):
    """Link a burner account to a primary threat actor."""
    data    = await request.json()
    primary = data.get("primary","").lstrip("@").lower()
    burner  = data.get("burner","").lstrip("@").lower()
    operator = data.get("operator", user.get("display","SYSTEM"))
    if not primary or not burner:
        return {"error": "primary and burner required"}
    if r:
        r.sadd(f"spector:burners:{primary}", burner)
        r.hset(f"spector:burner:{primary}:{burner}", mapping={
            "username":  burner,
            "linked_by": operator,
            "linked_at": datetime.utcnow().isoformat(),
            "reason":    data.get("reason","manual")
        })
        r.sadd("goons", burner)  # auto-add burner to vault
    return {"status": "linked", "primary": primary, "burner": burner}

@app.post("/api/spector/log_session")
async def spector_log_session(request: Request):
    """Log a session event for SPECTOR profile building."""
    data     = await request.json()
    username = data.get("username","").lower()
    if not username or not r: return {"status":"ok"}
    r.lpush(f"spector:sessions:{username}", json.dumps({
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "event":     data.get("event","JOIN"),
        "target":    data.get("target",""),
        "blocked":   data.get("blocked", False)
    }))
    r.ltrim(f"spector:sessions:{username}", 0, 99)
    return {"status": "logged"}

@app.get("/api/deep6/timeline")
async def deep6_timeline(limit: int = 100, user: dict = Depends(get_current_user)):
    """Get recent stream events from Deep6 timeline."""
    if not r:
        return {"events": []}
    target = r.get("aegis:target") or "jarmygal"
    from datetime import date
    key = f"deep6:timeline:{target}:{date.today().strftime('%Y%m%d')}"
    events = r.lrange(key, 0, limit - 1)
    return {"events": [json.loads(e) for e in events], "target": target, "count": len(events)}

@app.get("/api/deep6/threats")
async def deep6_threats(user: dict = Depends(get_current_user)):
    """Get all active threat profiles ranked by activity."""
    if not r:
        return {"threats": [], "total": 0}
    threats = r.smembers("deep6:active_threats") or set()
    profiles = []
    for u in list(threats)[:100]:
        p = r.get(f"deep6:profile:{u}")
        if p:
            try:
                profiles.append(json.loads(p))
            except:
                pass
    profiles.sort(key=lambda x: x.get("join_count", 0) + x.get("comment_count", 0), reverse=True)
    return {"threats": profiles[:50], "total": len(threats)}

@app.post("/api/deep6/evidence/{username}")
async def deep6_evidence(username: str, user: dict = Depends(get_current_user)):
    """Generate a Deep6 evidence package for a threat actor."""
    if not r:
        return {"error": "Redis unavailable"}
    u = username.lstrip("@").lower()
    profile_data = r.get(f"deep6:profile:{u}")
    if not profile_data:
        return {"error": "no profile found", "username": u}
    p = json.loads(profile_data)
    package = {
        "case_reference": f"DEEP6-{u.upper()}-{datetime.utcnow().strftime('%Y%m%d')}",
        "generated": datetime.utcnow().isoformat(),
        "prepared_by": "AEGIS PHANTOM Deep6 Intel Engine -- Cybergrid Solutions LLC",
        "subject": u,
        "threat_level": p.get("threat_level", "UNKNOWN"),
        "first_seen": p.get("first_seen"),
        "last_seen": p.get("last_seen"),
        "streams_targeted": p.get("streams", []),
        "total_joins": p.get("join_count", 0),
        "total_comments": p.get("comment_count", 0),
        "total_blocks": p.get("block_count", 0),
        "comment_samples": p.get("comments", [])[-10:],
        "event_timeline": p.get("events", [])[-20:],
        "platform": "TikTok",
        "submitted_to": "TikTok Trust & Safety",
        "notes": f"This profile was built automatically by AEGIS PHANTOM monitoring {p.get('streams', [])}. All data reflects live stream activity."
    }
    # Store evidence package in Redis
    if r:
        r.setex(f"deep6:evidence:{u}", 86400 * 7, json.dumps(package))
    return package

@app.post("/api/deep6/log")
async def deep6_log_event(request: Request):
    """Receive Deep6 log events from Pi-top airlock engine."""
    data = await request.json()
    if not r:
        return {"status": "no redis"}
    username = data.get("username", "").lower()
    event_type = data.get("event_type", "")
    comment = data.get("comment", "")
    is_goon = data.get("is_goon", False)
    threat_level = data.get("threat_level", "WATCH")
    target = data.get("target_stream", r.get("aegis:target") or "jarmygal")

    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "event_type": event_type,
        "username": username,
        "comment": comment,
        "is_goon": is_goon,
        "threat_level": threat_level,
        "target_stream": target
    }

    from datetime import date
    session_key = f"deep6:timeline:{target}:{date.today().strftime('%Y%m%d')}"
    r.lpush(session_key, json.dumps(entry))
    r.ltrim(session_key, 0, 999)
    r.expire(session_key, 86400 * 7)

    if is_goon or threat_level in ("VAMPIRE", "GATOR", "HOSTILE", "GOON"):
        profile_key = f"deep6:profile:{username}"
        existing = r.get(profile_key)
        profile = json.loads(existing) if existing else {
            "username": username, "first_seen": datetime.utcnow().isoformat(),
            "streams": [], "comments": [], "events": [],
            "threat_level": threat_level, "block_count": 0,
            "join_count": 0, "comment_count": 0
        }
        if event_type == "JOIN":
            profile["join_count"] += 1
            if target not in profile["streams"]:
                profile["streams"].append(target)
        elif event_type == "COMMENT" and comment:
            profile["comment_count"] += 1
            profile["comments"].append({"text": comment, "timestamp": datetime.utcnow().isoformat(), "stream": target})
            profile["comments"] = profile["comments"][-50:]
        elif event_type == "BLOCK":
            profile["block_count"] += 1
        profile["last_seen"] = datetime.utcnow().isoformat()
        profile["threat_level"] = threat_level
        profile["events"].append({"type": event_type, "ts": datetime.utcnow().isoformat()})
        profile["events"] = profile["events"][-100:]
        r.set(profile_key, json.dumps(profile))
        r.expire(profile_key, 86400 * 30)
        r.sadd("deep6:active_threats", username)

    return {"status": "logged"}

@app.get("/api/whitelist/check/{username}")
async def check_whitelist(username: str):
    """Check if a username is whitelisted -- called by Pi-top before blocking."""
    if not r:
        return {"whitelisted": False}
    u = username.lstrip("@").lower()
    whitelisted = r.sismember("whitelist", u)
    return {"whitelisted": bool(whitelisted), "username": u}

@app.get("/api/debug/imagen")
async def test_imagen():
    try:
        from imagen_engine import generate_brief_image
        result = generate_brief_image(
            "Microsoft Teams exploited in social engineering attack",
            "Hackers impersonate IT helpdesk on Teams to steal credentials.",
            "cyber"
        )
        return {
            "success": bool(result),
            "length": len(result) if result else 0,
            "preview": result[:100] if result else "EMPTY -- no images returned"
        }
    except Exception as e:
        import traceback
        return {"success": False, "error": str(e), "type": type(e).__name__, "trace": traceback.format_exc()}
@app.get("/api/sitrep/imagen")
async def sitrep_imagen(prompt: str = "tactical intelligence briefing image"):
    try:
        from imagen_engine import generate_brief_image
        from fastapi.responses import Response
        import base64
        result = generate_brief_image(prompt, prompt, "military")
        if result:
            raw_bytes = base64.b64decode(result)
            return Response(content=raw_bytes, media_type="image/png")

        return {"success": False, "error": "No image generated"}
    except Exception as e:
        import traceback
        return {"success": False, "error": str(e), "trace": traceback.format_exc()}
    


@app.post("/api/vault/batch-ingest")
async def vault_batch_ingest(request: Request, user: dict = Depends(get_current_user)):
    """Batch ingest threat accounts into vault from dashboard."""
    if not r:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    data = await request.json()
    accounts = data.get("accounts", [])
    case_id = data.get("case_id", "MANUAL_INGEST")
    if not accounts:
        return JSONResponse({"error": "no accounts provided"}, status_code=400)
    pipe = r.pipeline()
    total = 0
    ts = datetime.utcnow().isoformat()
    for acct in accounts:
        username = acct.get("username", "").strip().lstrip("@").lower()
        if not username:
            continue
        mapping = {
            "username":  username,
            "network":   acct.get("network", "UNKNOWN"),
            "category":  acct.get("category", "uncategorized"),
            "tier":      acct.get("tier", "unknown"),
            "role":      acct.get("role", "unclassified"),
            "case":      case_id,
            "ingested":  ts,
            "source":    "dashboard_ingest",
            "operator":  user.get("display", "SYSTEM"),
        }
        pipe.hset(f"vault:{username}", mapping=mapping)
        pipe.sadd("vault:index", username)
        pipe.sadd("goons", username)
        pipe.sadd(f"vault:network:{mapping['network']}", username)
        pipe.sadd(f"vault:category:{mapping['category']}", username)
        pipe.sadd(f"vault:tier:{mapping['tier']}", username)
        total += 1
    pipe.execute()
    print(f"[VAULT INGEST] {total} accounts by {user.get('display')} -- case {case_id}")
    await manager.broadcast({
        "type": "VAULT_INGEST",
        "count": total,
        "case": case_id,
        "operator": user.get("display"),
        "timestamp": ts
    })
    return {"status": "ok", "ingested": total}

# -- SitRep Pipeline Routes -----------------------------------------------------
from sitrep_scheduler import run_scheduled_cycle, run_breaking_check, generate_scheduled_brief, CATEGORIES
from sitrep_video_renderer import render_video, generate_caption
from sitrep_post_agent import dispatch_post, approve_and_post
import asyncio, tempfile, json as _json

async def process_render_queue():
    raw = r.rpop("sitrep:render_queue")
    if not raw:
        return {"status": "queue_empty"}
    brief = _json.loads(raw)
    brief_id = brief.get("brief_id", "UNKNOWN")
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        video_path = tmp.name
    try:
        success = render_video(brief, video_path)
        if not success:
            return {"brief_id": brief_id, "error": "render_failed"}
        caption = generate_caption(brief)
        return await dispatch_post(brief, video_path, caption)
    finally:
        try:
            os.unlink(video_path)
        except Exception:
            pass

@app.post("/api/sitrep/scheduled-cycle")
async def sitrep_scheduled_cycle():
    results = await run_scheduled_cycle()
    asyncio.create_task(process_render_queue())
    return {"status": "cycle_complete", "briefs": results}

@app.post("/api/sitrep/breaking-check")
async def sitrep_breaking_check():
    result = await run_breaking_check()
    if result.get("status") == "breaking_queued":
        asyncio.create_task(process_render_queue())
    return result

@app.post("/api/sitrep/generate/{category}")
async def sitrep_generate_single(category: str):
    cat = next((c for c in CATEGORIES if c["key"] == category), None)
    if not cat:
        return {"error": f"Unknown category: {category}"}
    brief = await generate_scheduled_brief(cat)
    if not brief:
        return {"error": "Brief generation failed"}
    r.lpush("sitrep:render_queue", _json.dumps(brief))
    asyncio.create_task(process_render_queue())
    return {"status": "queued", "brief_id": brief.get("brief_id"), "headline": brief.get("headline")}

@app.post("/api/sitrep/render-next")
async def sitrep_render_next():
    return await process_render_queue()

@app.get("/api/sitrep/queue-status")
async def sitrep_queue_status():
    queue_depth = r.llen("sitrep:render_queue")
    pending_keys = r.keys("sitrep:pending:*")
    posted_keys = r.keys("sitrep:posted:*")
    recent = []
    for key in list(posted_keys)[-5:]:
        raw = r.get(key)
        if raw:
            recent.append(_json.loads(raw))
    return {"queue_depth": queue_depth, "pending_approval": len(pending_keys), "recent_posted": recent}

@app.post("/api/sitrep/approve/{brief_id}")
async def sitrep_approve(brief_id: str):
    return await approve_and_post(brief_id)

@app.get("/api/sitrep/pending")
async def sitrep_pending_list():
    keys = r.keys("sitrep:pending:*")
    pending = []
    for key in keys:
        raw = r.get(key)
        if raw:
            data = _json.loads(raw)
            brief = data.get("brief", {})
            pending.append({
                "brief_id": brief.get("brief_id"),
                "category": brief.get("category"),
                "headline": brief.get("headline"),
                "video_url": data.get("video_url"),
            })
    return {"pending": pending}

# --- RECON ENDPOINTS ----------------------------------------------------------

@app.post("/api/recon/alert")
async def recon_alert(request: Request):
    """Receives alerts from recon bot running on Pi-top."""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not r:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    alert = {
        "timestamp": datetime.utcnow().isoformat(),
        "type": data.get("type", "UNKNOWN"),
        "handle": data.get("handle", ""),
        "network": data.get("network", "EE"),
        "detail": data.get("detail", ""),
        "rate": data.get("rate", 0),
        "severity": data.get("severity", "LOW"),
    }

    # Store in Redis
    r.lpush("recon:alerts", json.dumps(alert))
    r.ltrim("recon:alerts", 0, 499)

    # Update attack rate counter
    if alert["type"] == "ACCOUNT_ACTIVATED":
        r.incr("recon:activation_count")
        r.expire("recon:activation_count", 3600)

    # Broadcast to dashboard
    msg = json.dumps({
        "type": "recon_alert",
        "alert": alert
    })
    for ws in list(connected_clients):
        try:
            await ws.send_text(msg)
        except Exception:
            connected_clients.discard(ws)

    return {"status": "received", "alert": alert}


@app.get("/api/recon/alerts")
async def get_recon_alerts(user: dict = Depends(get_current_user)):
    """Returns recent recon alerts."""
    if not r:
        return {"alerts": [], "count": 0, "activation_rate": 0}
    raw = r.lrange("recon:alerts", 0, 99)
    alerts = [json.loads(a) for a in raw]
    rate = int(r.get("recon:activation_count") or 0)
    return {"alerts": alerts, "count": len(alerts), "activation_rate": rate}


@app.get("/api/recon/rate")
async def get_attack_rate(user: dict = Depends(get_current_user)):
    """Returns current EE network activation rate ? accounts per hour."""
    if not r:
        return {"rate": 0, "threat_level": "GREEN"}
    rate = int(r.get("recon:activation_count") or 0)
    if rate >= 50:
        threat = "RED"
    elif rate >= 20:
        threat = "ORANGE"
    elif rate >= 5:
        threat = "YELLOW"
    else:
        threat = "GREEN"
    return {"rate": rate, "threat_level": threat}


@app.post("/api/recon/reset")
async def reset_recon(user: dict = Depends(require_admin)):
    """Resets the activation counter."""
    if r:
        r.delete("recon:activation_count")
    return {"status": "reset"}


@app.post("/api/comment/score")
async def score_comment(request: Request):
    """Receives comment from comment agent, scores threat level via Claude API."""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    username = data.get("username", "")
    comment = data.get("comment", "")
    if not username or not comment:
        raise HTTPException(status_code=400, detail="username and comment required")

    # Check vault first ? known goon = instant 10
    if r and r.sismember("goons", username.lower()):
        score = 10
        label = "KNOWN_GOON"
    else:
        # Score via Claude API
        if not ANTHROPIC_API_KEY:
            score = 5
            label = "UNSCORED"
        else:
            prompt = f"""You are a cybersecurity threat analyst monitoring a TikTok livestream for a US Army veteran being targeted by a coordinated harassment network.

Score this comment 1-10 for threat level:
- 1-3: Friendly/neutral ? regular viewer, supportive comment
- 4-5: Ambiguous ? political but not targeted harassment  
- 6-7: Suspicious ? baiting, provocative, or pattern-matched to known attack phrases
- 8-9: Hostile ? direct harassment, stolen valor narrative, coordinated attack language
- 10: Critical ? sexual harassment, identity impersonation, prohibited content

Known attack phrases: "stolen valor", "grifter", "fake veteran", "oppressed professional", "operation other side of the wall", "we the people are sick of you"

Username: @{username}
Comment: {comment}

Respond with ONLY a JSON object: {{"score": N, "label": "LABEL", "reason": "brief reason"}}"""

            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.post(
                        "https://api.anthropic.com/v1/messages",
                        json={
                            "model": "claude-sonnet-4-6",
                            "max_tokens": 100,
                            "messages": [{"role": "user", "content": prompt}]
                        },
                        headers={
                            "Content-Type": "application/json",
                            "x-api-key": ANTHROPIC_API_KEY,
                            "anthropic-version": "2023-06-01"
                        }
                    )
                    result = resp.json()
                    text = result["content"][0]["text"].strip()
                    parsed = json.loads(text)
                    score = parsed.get("score", 5)
                    label = parsed.get("label", "UNKNOWN")
            except Exception:
                score = 5
                label = "UNSCORED"

    # Store if threat score >= 6
    if score >= 6 and r:
        threat_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "username": username,
            "comment": comment,
            "score": score,
            "label": label
        }
        r.lpush("comment:threats", json.dumps(threat_entry))
        r.ltrim("comment:threats", 0, 299)

        # Broadcast to dashboard if score >= 6
        msg = json.dumps({
            "type": "comment_threat",
            "data": threat_entry
        })
        for ws in list(connected_clients):
            try:
                await ws.send_text(msg)
            except Exception:
                connected_clients.discard(ws)

    return {"username": username, "score": score, "label": label}


@app.get("/api/comment/threats")
async def get_comment_threats(user: dict = Depends(get_current_user)):
    """Returns recent high-threat comments."""
    if not r:
        return {"threats": [], "count": 0}
    raw = r.lrange("comment:threats", 0, 99)
    threats = [json.loads(t) for t in raw]
    return {"threats": threats, "count": len(threats)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))















# Deploy timestamp: 2026-05-22 23:56:13

# Deploy: 2026-05-23 10:29:43
# 05/25/2026 11:38:13
