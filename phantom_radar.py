"""
PHANTOM RADAR — AWG-9 Multi-Target Threat Scoring Engine
AEGIS PHANTOM | Cybergrid Solutions LLC
v5 — Live join cache + Redis vault
"""

import os, json, re, logging
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv(r'C:\Users\VernonDunbar\Documents\Aegis_Phantom\.env')

logging.basicConfig(level=logging.INFO, format="%(asctime)s [PHANTOM_RADAR] %(message)s")
log = logging.getLogger("phantom_radar")

# ─── Redis ────────────────────────────────────────────────────────────────────
r = None
try:
    import redis as redis_lib
    REDIS_URL = os.getenv("REDIS_URL","")
    if REDIS_URL:
        r = redis_lib.from_url(REDIS_URL, decode_responses=True)
        r.ping()
        log.info("Redis connection established")
except Exception as e:
    log.warning(f"Redis unavailable: {e}")
    r = None

# ─── Firestore ────────────────────────────────────────────────────────────────
db = None
def init_firestore():
    global db
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore as fs
        paths = [
            os.getenv("FIREBASE_CREDENTIALS",""),
            "serviceAccount.json",
            r"C:\Users\VernonDunbar\Documents\Aegis_Phantom\serviceAccount.json",
        ]
        if firebase_admin._apps:
            db = fs.client(); return
        for p in paths:
            if p and os.path.exists(p):
                firebase_admin.initialize_app(credentials.Certificate(p))
                db = fs.client()
                log.info(f"Firestore connected via {p}")
                return
        log.warning("No service account found")
    except Exception as e:
        log.warning(f"Firestore unavailable: {e}")
init_firestore()

# ─── Safe type helpers ────────────────────────────────────────────────────────
def _s(v, default=""):
    if isinstance(v, list): v = v[0] if v else default
    return str(v) if v is not None else default

def _f(v, default=0.0):
    if isinstance(v, list): v = v[0] if v else default
    try: return float(v)
    except: return default

def _i(v, default=0):
    if isinstance(v, list): v = v[0] if v else default
    try: return int(v)
    except: return default

def _b(v, default=False):
    if isinstance(v, list): v = v[0] if v else default
    return bool(v)

# ─── Constants ────────────────────────────────────────────────────────────────
THREAT_CONDITIONS = {
    "GREEN":  "Stable environment",
    "YELLOW": "Recon / staging activity",
    "ORANGE": "Coordinated swarm probability rising",
    "RED":    "Active attack underway",
    "BLACK":  "Platform destabilization event",
}

EE_PATTERNS  = [r"ee\d",r"reeree",r"snowwhitee",r"charliee",r"reneeregina",
                r"heven.?scent",r"jessessprout",r"nobodies.*cents",r"respectfully",r"nylah"]
MIL_PATTERNS = [r"usmcvet",r"tanker[a-z0-9]",r"iceman\d",r"infantry",
                r"veteran\d",r"combat[a-z]",r"1stcav",r"cavalrygal"]

KNOWN_C2 = {
    "its_ee4","respectfullyno123183","heven_scent","heven_scent4","ee2.0",
    "reeree","snowwhitee23","charliee_092","reneeregina","usmcvet2012",
    "tankerb29","iceman8386","jessessprout","liberal_hater6969",
    "nobodies_2cents","user7841180269021","marinabath1","nylah4ever0","deehappy6"
}

MOD_WHITELIST = {
    "let_me_be_the_one1","kansasflygirl","yoli1392","shorty8251",
    "creemiib","judiie93","verdu1105","autuman","vinnymin1963",
    "fluffypinksatanist","selenaperez26","trish.blindlife",
}

# ─── Live join cache — airlock feeds this in real time ────────────────────────
LIVE_JOIN_CACHE = {}

def update_live_cache(account: dict):
    username = _s(account.get("username","")).lower()
    if username and username not in MOD_WHITELIST:
        LIVE_JOIN_CACHE[username] = account
        log.info(f"Live join cached: @{username} (status:{_s(account.get('status','unknown'))})")

# ─── Memory vault seed ────────────────────────────────────────────────────────
MEMORY_VAULT = [
    {"username":"respectfullyno123183","status":"known_hostile","risk_score":"1.0","follows_c2_node":True,"c2_graph_degree":0,"account_age_days":45,"post_count":3,"military_avatar":True,"rejoins_last_hour":2},
    {"username":"heven_scent4","status":"known_hostile","risk_score":"0.9","follows_c2_node":True,"c2_graph_degree":1,"account_age_days":12,"post_count":0,"bio_empty":True,"rejoins_last_hour":1},
    {"username":"nylah4ever0","status":"known_hostile","risk_score":"0.9","c2_graph_degree":2,"account_age_days":8,"post_count":0,"bio_empty":True,"rejoins_last_hour":8},
    {"username":"deehappy6","status":"known_hostile","risk_score":"0.85","c2_graph_degree":1,"account_age_days":20,"post_count":2},
    {"username":"tankerb29","status":"known_hostile","risk_score":"0.85","follows_c2_node":True,"c2_graph_degree":1,"account_age_days":30,"post_count":0,"military_avatar":True},
    {"username":"iceman8386","status":"known_hostile","risk_score":"0.85","follows_c2_node":True,"c2_graph_degree":1,"account_age_days":25,"post_count":1,"military_avatar":True},
]

# ─── Scoring ──────────────────────────────────────────────────────────────────
def score_c2(a):
    u = (_s(a.get("username")) + " " + _s(a.get("display_name"))).lower()
    username = _s(a.get("username")).lower()
    if username in KNOWN_C2: return 100.0
    risk = _f(a.get("risk_score", 0))
    if risk >= 0.9: return 100.0
    if risk >= 0.8: base = 70.0
    elif risk >= 0.6: base = 50.0
    else: base = 0.0
    s  = base
    s += min(sum(1 for p in EE_PATTERNS  if re.search(p,u))*35, 70)
    s += min(sum(1 for p in MIL_PATTERNS if re.search(p,u))*25, 50)
    s += min(_i(a.get("known_hostile_followers",0))*10, 40)
    if _b(a.get("follows_c2_node")): s += 30
    status = _s(a.get("status","")).lower()
    if status in ("known_hostile","confirmed_hostile","c2_node"): s += 40
    elif status in ("suspected","flagged"): s += 20
    blocks = _i(a.get("block_count",0))
    if blocks > 5: s += 20
    elif blocks > 0: s += 10
    return min(s, 100.0)

def score_velocity(a):
    r1h = _i(a.get("rejoins_last_hour",0))
    r24 = _i(a.get("rejoins_last_24h",0))
    return 100 if r1h>=10 else 80 if r1h>=5 else 70 if r24>=20 else 50 if r1h>=2 else 30 if r24>=5 else 0

def score_proximity(a):
    d = _i(a.get("c2_graph_degree",99))
    return {0:100,1:85,2:55,3:25}.get(d,5)

def score_behavioral(a):
    age   = _i(a.get("account_age_days",365))
    posts = _i(a.get("post_count",100))
    s  = 60 if age<7 else 40 if age<30 else 20 if age<90 else 0
    s += 30 if posts==0 else 20 if posts<5 else 10 if posts<20 else 0
    if _b(a.get("military_avatar")): s += 20
    if _b(a.get("bio_empty")):       s += 10
    return min(s, 100.0)

def score_recon(a):
    s  = 40 if _b(a.get("no_chat_during_attacks")) else 0
    s += min(_i(a.get("joins_during_attack_windows",0))*20, 40)
    if _b(a.get("follows_spike_detected")): s += 30
    if _i(a.get("watch_only_sessions",0))>3: s += 20
    return min(s, 100.0)

def compute_threat_score(account):
    username = _s(account.get("username","unknown"))
    if username.lower() in MOD_WHITELIST:
        return {"username":username,"score":0,"tier":"GREEN","components":{},"scored_at":datetime.now(timezone.utc).isoformat(),"whitelisted":True}
    c2   = score_c2(account)
    vel  = score_velocity(account)
    prox = score_proximity(account)
    beh  = score_behavioral(account)
    rec  = score_recon(account)
    status = _s(account.get("status","")).lower()
    risk   = _f(account.get("risk_score", 0))
    vault_boost = 40 if status in ("known_hostile","confirmed_hostile","c2_node") and risk>=0.8 else 25 if status in ("known_hostile","confirmed_hostile","c2_node") else 15 if status in ("suspected","flagged") else 0
    score = round(min(c2*0.30+vel*0.20+prox*0.20+beh*0.15+rec*0.15+vault_boost, 100.0), 1)
    tier  = "RED" if score>=85 else "ORANGE" if score>=65 else "YELLOW" if score>=40 else "GREEN"
    return {
        "username": username,
        "score": score,
        "tier": tier,
        "risk_score": _f(account.get("risk_score",0)),
        "status": _s(account.get("status","")),
        "tags": _s(account.get("tags","")),
        "notes": _s(account.get("notes","")),
        "live_join": account.get("live_join", False),
        "components": {
            "c2_signature":  round(c2,1),
            "join_velocity": round(vel,1),
            "net_proximity": round(prox,1),
            "behavioral":    round(beh,1),
            "recon":         round(rec,1),
        },
        "scored_at": datetime.now(timezone.utc).isoformat(),
    }

# ─── Vault Loader ─────────────────────────────────────────────────────────────
def load_vault():
    if r:
        try:
            keys = r.keys("vault:*")
            vault_keys = [k for k in keys if not any(x in k for x in ["zeroday","snapshot","radar","threat","priority"])]
            if vault_keys:
                accounts = []
                for key in vault_keys:
                    try:
                        data = r.hgetall(key)
                        if data and data.get("username"):
                            accounts.append(data)
                    except Exception:
                        continue
                seen = set()
                unique = []
                for a in accounts:
                    u = _s(a.get("username","")).lower()
                    if u and u not in seen:
                        seen.add(u)
                        unique.append(a)
                log.info(f"Loaded {len(unique)} accounts from Redis vault")
                return unique
        except Exception as e:
            log.warning(f"Redis vault load failed: {e}")
    if db:
        try:
            accounts = [doc.to_dict()|{"id":doc.id} for doc in db.collection("vault_accounts").stream()]
            if accounts:
                log.info(f"Loaded {len(accounts)} accounts from Firestore")
                return accounts
        except Exception as e:
            log.warning(f"Firestore read failed: {e}")
    log.info(f"Using memory vault — {len(MEMORY_VAULT)} accounts")
    return MEMORY_VAULT

# ─── Scan ─────────────────────────────────────────────────────────────────────
def scan_vault(top_n=6):
    accounts = load_vault()

    # Merge live joins — add any new accounts not already in vault
    vault_usernames = {_s(a.get("username","")).lower() for a in accounts}
    for username, data in LIVE_JOIN_CACHE.items():
        if username not in vault_usernames:
            accounts.append(data)
            vault_usernames.add(username)
        else:
            # Update existing vault entry with live join data
            for a in accounts:
                if _s(a.get("username","")).lower() == username:
                    a.update({k: v for k, v in data.items() if v})
                    break

    log.info(f"Scoring {len(accounts)} accounts ({len(LIVE_JOIN_CACHE)} live joins merged)...")
    scored = [compute_threat_score(a) for a in accounts]
    scored = [s for s in scored if not s.get("whitelisted")]
    scored.sort(key=lambda x: x["score"], reverse=True)
    high = sum(1 for s in scored if s["score"]>=65)
    crit = sum(1 for s in scored if s["score"]>=85)
    cond = "RED" if crit>=3 or high>=10 else "ORANGE" if crit>=1 or high>=5 else "YELLOW" if high>=2 else "GREEN"
    result = {
        "threat_condition": cond,
        "condition_label": THREAT_CONDITIONS[cond],
        "vault_size": len(accounts),
        "live_joins": len(LIVE_JOIN_CACHE),
        "high_threats": high,
        "critical_threats": crit,
        "priority_queue": scored[:top_n],
        "scan_time": datetime.now(timezone.utc).isoformat(),
    }
    if r:
        try:
            r.setex("phantom:radar:latest",300,json.dumps(result))
            r.setex("phantom:priority_queue",300,json.dumps(scored[:top_n]))
            r.set("phantom:threat_condition",cond)
        except Exception as e:
            log.warning(f"Redis cache write failed: {e}")
    if db:
        try: db.collection("radar_snapshots").add(result)
        except: pass
    log.info(f"Scan complete — condition:{cond} | vault:{len(accounts)} | live:{len(LIVE_JOIN_CACHE)} | high:{high} | critical:{crit}")
    if scored:
        log.info(f"Top threat: {scored[0]['username']} — {scored[0]['score']} ({scored[0]['tier']})")
    return result

# ─── Flask API ────────────────────────────────────────────────────────────────
def create_app():
    from flask import Flask, jsonify, request
    app = Flask(__name__)

    @app.route("/health")
    def health():
        return jsonify({"status":"operational","service":"phantom_radar","redis":r is not None,"firestore":db is not None,"live_joins":len(LIVE_JOIN_CACHE)})

    @app.route("/radar/priority")
    def priority():
        n = int(request.args.get("n",6))
        return jsonify(scan_vault(n))

    @app.route("/radar/scan",methods=["POST"])
    def trigger_scan():
        n = int((request.json or {}).get("top_n",6))
        return jsonify(scan_vault(n))

    @app.route("/radar/score",methods=["POST"])
    def score_single():
        account = request.get_json()
        if not account: return jsonify({"error":"No data"}),400
        account["live_join"] = True
        update_live_cache(account)
        return jsonify(compute_threat_score(account))

    @app.route("/radar/condition")
    def condition():
        cond = "GREEN"
        if r:
            try: cond = r.get("phantom:threat_condition") or "GREEN"
            except: pass
        return jsonify({"condition":cond})

    @app.route("/vault/add",methods=["POST"])
    def add_to_vault():
        account = request.get_json()
        if not account or not account.get("username"):
            return jsonify({"error":"username required"}),400
        MEMORY_VAULT.append(account)
        if db:
            try: db.collection("vault_accounts").add(account)
            except: pass
        return jsonify({"added":True,"score":compute_threat_score(account)})

    return app

if __name__ == "__main__":
    import sys
    if "--scan" in sys.argv:
        print(json.dumps(scan_vault(),indent=2))
    else:
        app = create_app()
        port = int(os.getenv("PORT",8001))
        log.info(f"PHANTOM RADAR starting on port {port}")
        log.info(f"Redis: {'connected' if r else 'offline'} | Firestore: {'connected' if db else 'memory-only'}")
        app.run(host="0.0.0.0",port=port,debug=False)
