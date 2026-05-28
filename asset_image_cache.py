"""
SitRep Asset Image Cache — Firestore metadata + GCS image storage
"""
import os, base64, hashlib, time, uuid
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

cache_router = APIRouter(prefix="/api/sitrep/asset-cache", tags=["asset-cache"])

GCS_BUCKET = "aegis-forensics"
COLLECTION = "sitrep_asset_images"

_db = None
_gcs = None

def get_db():
    global _db
    if _db is None:
        from google.cloud import firestore
        _db = firestore.Client(project="cybergrid")
    return _db

def get_gcs():
    global _gcs
    if _gcs is None:
        from google.cloud import storage
        _gcs = storage.Client(project="cybergrid")
    return _gcs

def make_cache_key(label: str, asset_type: str) -> str:
    raw = f"{label.lower().strip()}_{asset_type.lower().strip()}"
    return hashlib.md5(raw.encode()).hexdigest()

class CacheStoreRequest(BaseModel):
    label: str
    asset_type: str = "asset"
    side: str = "unknown"
    theater: str = ""
    prompt: str = ""
    image_data: str = ""
    score: float = 1.0

@cache_router.get("/check")
async def check_cache(label: str, asset_type: str):
    try:
        db = get_db()
        key = make_cache_key(label, asset_type)
        doc = db.collection(COLLECTION).document(key).get()
        if doc.exists:
            data = doc.to_dict()
            gcs_url = data.get("gcs_url", "")
            # Fetch image from GCS and return as base64
            if gcs_url:
                try:
                    gcs = get_gcs()
                    bucket = gcs.bucket(GCS_BUCKET)
                    blob = bucket.blob(data.get("gcs_path", ""))
                    img_bytes = blob.download_as_bytes()
                    b64 = base64.b64encode(img_bytes).decode("utf-8")
                    image_data = f"data:image/png;base64,{b64}"
                    print(f"[AssetCache] HIT+GCS: {label}")
                    return JSONResponse({"hit": True, "image": image_data, "label": data.get("label"), "score": data.get("score", 1.0)})
                except Exception as e:
                    print(f"[AssetCache] GCS fetch error: {e}")
            print(f"[AssetCache] HIT (no GCS): {label}")
            return JSONResponse({"hit": True, "image": None, "label": data.get("label")})
        return JSONResponse({"hit": False, "cache_key": key})
    except Exception as e:
        return JSONResponse({"hit": False, "error": str(e)})

@cache_router.post("/store")
async def store_cache(req: CacheStoreRequest):
    try:
        db = get_db()
        gcs = get_gcs()
        key = make_cache_key(req.label, req.asset_type)

        # Extract base64 and upload to GCS
        b64_data = req.image_data.replace("data:image/png;base64,", "").replace("data:image/jpeg;base64,", "")
        img_bytes = base64.b64decode(b64_data)
        gcs_path = f"sitrep_assets/{key}.png"
        bucket = gcs.bucket(GCS_BUCKET)
        blob = bucket.blob(gcs_path)
        blob.upload_from_string(img_bytes, content_type="image/png")
        gcs_url = f"https://storage.googleapis.com/{GCS_BUCKET}/{gcs_path}"

        # Store metadata in Firestore
        doc_data = {
            "cache_key": key,
            "label": req.label,
            "asset_type": req.asset_type,
            "side": req.side,
            "theater": req.theater,
            "prompt": req.prompt[:500],
            "gcs_path": gcs_path,
            "gcs_url": gcs_url,
            "score": req.score,
            "generated_at": int(time.time()),
            "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        db.collection(COLLECTION).document(key).set(doc_data)
        print(f"[AssetCache] STORED: {req.label} → GCS {gcs_path}")
        return JSONResponse({"stored": True, "cache_key": key, "gcs_url": gcs_url})
    except Exception as e:
        import traceback
        print(f"[AssetCache] store error: {e}\n{traceback.format_exc()}")
        return JSONResponse({"stored": False, "error": str(e)}, status_code=500)

@cache_router.get("/library")
async def get_library():
    try:
        db = get_db()
        docs = db.collection(COLLECTION).order_by(
            "generated_at", direction="DESCENDING"
        ).limit(100).stream()
        results = []
        for doc in docs:
            d = doc.to_dict()
            results.append({
                "cache_key": d.get("cache_key"),
                "label": d.get("label"),
                "asset_type": d.get("asset_type"),
                "side": d.get("side"),
                "theater": d.get("theater"),
                "score": d.get("score"),
                "gcs_url": d.get("gcs_url"),
                "generated_at_iso": d.get("generated_at_iso"),
            })
        return JSONResponse({"count": len(results), "library": results})
    except Exception as e:
        return JSONResponse({"count": 0, "error": str(e)}, status_code=500)

@cache_router.get("/fetch/{cache_key}")
async def fetch_by_key(cache_key: str):
    try:
        db = get_db()
        doc = db.collection(COLLECTION).document(cache_key).get()
        if doc.exists:
            data = doc.to_dict()
            return JSONResponse({
                "found": True,
                "label": data.get("label"),
                "gcs_url": data.get("gcs_url"),
                "prompt": data.get("prompt"),
                "score": data.get("score"),
                "generated_at_iso": data.get("generated_at_iso"),
            })
        return JSONResponse({"found": False}, status_code=404)
    except Exception as e:
        return JSONResponse({"found": False, "error": str(e)}, status_code=500)
