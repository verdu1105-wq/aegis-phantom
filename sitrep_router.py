from fastapi import APIRouter, BackgroundTasks
from sitrep_briefs import get_cached_briefs, generate_all_briefs, cache_briefs

sitrep_router = APIRouter(prefix="/api/sitrep", tags=["sitrep"])

@sitrep_router.get("/briefs")
def get_briefs():
    cached = get_cached_briefs()
    if cached:
        return {"briefs": cached, "cached": True, "count": len(cached)}
    try:
        briefs = generate_all_briefs()
        if briefs:
            cache_briefs(briefs)
        return {"briefs": briefs, "cached": False, "count": len(briefs)}
    except Exception as e:
        return {"error": str(e), "briefs": [], "count": 0}

@sitrep_router.post("/refresh")
def refresh_briefs():
    try:
        briefs = generate_all_briefs()
        if briefs:
            cache_briefs(briefs)
        return {"status": "ok", "count": len(briefs)}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@sitrep_router.get("/health")
def sitrep_health():
    cached = get_cached_briefs()
    return {"briefs_ready": cached is not None, "count": len(cached) if cached else 0}
