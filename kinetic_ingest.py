"""
kinetic_ingest.py - SitRep Kinetic Event Ingestion
Parses RSS feeds, geo-tags with Gemini, writes to Firestore kinetic_events
"""
import os, json, hashlib, re
from datetime import datetime, timezone, timedelta
import httpx
from google.cloud import firestore

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GCP_PROJECT    = os.getenv("GCP_PROJECT", "cybergrid")
db = firestore.Client(project=GCP_PROJECT)

EVENT_TYPES = {
    "airstrike":"dark","missile":"threat","naval":"mil",
    "artillery":"dark","drone":"threat","cyber":"mil",
    "explosion":"dark","fire":"threat","default":"dark",
}

RSS_FEEDS = [
    "https://www.twz.com/feed",
    "https://www.defensenews.com/arc/outboundfeeds/rss/?rss=homepage",
    "https://www.janes.com/feeds/news",
]

async def geo_tag_event(title, summary):
    prompt = f"""Is this a kinetic military event (airstrike, missile, naval, artillery, drone, explosion)?
If YES return JSON only: {{"lat":32.1,"lng":34.9,"theater":"gulf","event_type":"missile","title":"SHORT CAPS TITLE","desc":"One sentence max 12 words","radius":15000}}
If NO return: null
HEADLINE: {title}
SUMMARY: {summary[:200]}"""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent",
                params={"key": GEMINI_API_KEY},
                json={"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"temperature":0.1,"maxOutputTokens":200}}
            )
            if not resp.is_success: return None
            text = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            text = text.replace("```json","").replace("```","").strip()
            if text.lower()=="null" or not text.startswith("{"): return None
            data = json.loads(text)
            if not all(k in data for k in ["lat","lng","theater","event_type"]): return None
            return data
    except Exception:
        return None

async def fetch_rss_items(url):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers={"User-Agent":"SitRepIntel/1.0"})
            if not resp.is_success: return []
        items = []
        for block in re.findall(r"<item>(.*?)</item>", resp.text, re.DOTALL)[:20]:
            tm = re.search(r"<title[^>]*>(.*?)</title>", block, re.DOTALL)
            sm = re.search(r"<description[^>]*>(.*?)</description>", block, re.DOTALL)
            lm = re.search(r"<link[^>]*>(.*?)</link>", block, re.DOTALL)
            if tm:
                items.append({
                    "title":   re.sub(r"<[^>]+>|<!\[CDATA\[|\]\]>","",tm.group(1)).strip(),
                    "summary": re.sub(r"<[^>]+>|<!\[CDATA\[|\]\]>","",sm.group(1) if sm else "").strip(),
                    "link":    re.sub(r"<[^>]+>","",lm.group(1) if lm else "").strip(),
                })
        return items
    except Exception:
        return []

async def ingest_kinetic_events():
    now = datetime.now(timezone.utc)
    new_events = 0
    for feed_url in RSS_FEEDS:
        for item in await fetch_rss_items(feed_url):
            event_id = hashlib.md5(item["title"].encode()).hexdigest()[:12]
            if db.collection("kinetic_events").document(event_id).get().exists:
                continue
            geo = await geo_tag_event(item["title"], item["summary"])
            if not geo: continue
            db.collection("kinetic_events").document(event_id).set({
                "id": event_id,
                "lat": geo["lat"], "lng": geo["lng"],
                "title": geo.get("title", item["title"][:50].upper()),
                "desc": geo.get("desc", item["summary"][:100]),
                "type": EVENT_TYPES.get(geo.get("event_type","default"),"dark"),
                "event_type": geo.get("event_type","default"),
                "theater": geo.get("theater","global"),
                "radius": geo.get("radius", 20000),
                "source": feed_url,
                "source_url": item.get("link",""),
                "raw_title": item["title"],
                "createdAt": now,
                "expiresAt": now + timedelta(hours=48),
            })
            new_events += 1
    for doc in db.collection("kinetic_events").where("expiresAt","<",now).stream():
        doc.reference.delete()
    return new_events

async def get_kinetic_events_data(theater: str):
    now = datetime.now(timezone.utc)
    try:
        if theater == "all":
            docs = db.collection("kinetic_events").where("expiresAt",">",now).limit(100).stream()
        else:
            docs = db.collection("kinetic_events").where("theater","==",theater).where("expiresAt",">",now).limit(50).stream()
        events = []
        for doc in docs:
            d = doc.to_dict()
            events.append({"lat":d["lat"],"lng":d["lng"],"title":d["title"],"desc":d["desc"],
                          "type":d["type"],"radius":d.get("radius",20000),"id":d["id"],
                          "theater":d.get("theater","global")})
        return {"events": events, "count": len(events), "theater": theater}
    except Exception as e:
        return {"events": [], "count": 0, "error": str(e)}

async def trigger_kinetic_ingest():
    """Trigger a manual kinetic event ingest."""
    await ingest_kinetic_events()
    return {"status": "ingest triggered"}

async def kinetic_status():
    """Return current kinetic ingest status."""
    return {"status": "online", "source": "kinetic_ingest"}
