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
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import httpx
import redis as redis_lib
import jwt
from dotenv import load_dotenv
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

def verify_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except:
        return None

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
    return {
        "status": "online",
        "goon_vault": goon_count,
        "dashboards": len(manager.connections),
        "target": target,
        "mode": mode,
        "radar_locked": radar_locked,
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
async def set_target(request: Request, user: dict = Depends(get_current_user)):
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
async def get_goons(user: dict = Depends(get_current_user)):
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
    return {"status": "added", "username": username}

@app.delete("/api/goons/{username}")
async def remove_goon(username: str, user: dict = Depends(require_admin)):
    if r:
        r.srem("goons", username.lower())
    return {"status": "removed", "username": username}

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
                headers={"Content-Type": "application/json", "x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01"},
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
    is_goon = False
    if r and username:
        is_goon = r.sismember("goons", username.lower())
    await manager.broadcast({"type": "comment", "username": username, "comment": data.get("comment", ""), "is_goon": is_goon, "data": data})
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
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1000,
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
