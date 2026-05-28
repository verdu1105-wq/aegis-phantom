"""
SitRep Background Generator Router - Vertex AI version
"""
import os, base64
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

bg_router = APIRouter(prefix="/api/sitrep", tags=["backgrounds"])

BG_PRESETS = {
    "phantom": "Glowing jolly roger skull and crossbones, dark military aesthetic, neon red and amber glow pulsing from within the skull, deep black background, scanline texture overlay, classified intelligence operation aesthetic, dramatic chiaroscuro lighting, smoke and shadow, cinematic, no text, no watermarks, 16:9 landscape",
    "cyber_command": "Futuristic cyber operations command center, multiple holographic screens, electric blue and green code streams, dark room with dramatic lighting, no faces, silhouetted operators, 16:9 landscape",
    "theater_ops": "Military operations theater map, glowing tactical overlay, topographic lines, strategic markers, amber and red glow, dark classified briefing room, cinematic lighting, no text, no flags, 16:9 landscape",
    "war_room": "Underground war room bunker, dramatic red emergency lighting, tactical displays, heavy shadows, military intelligence aesthetic, no faces, silhouetted figures, 16:9 landscape",
    "deep_space": "Earth from low orbit at night, city lights below, surveillance satellite in foreground, dark space backdrop, dramatic lighting, cinematic intelligence operation aesthetic, 16:9 landscape, photorealistic",
    "hormuz": "Strait of Hormuz at dusk, military vessels in silhouette, oil tankers, dramatic orange and red sky, cinematic chiaroscuro, no text, 16:9 landscape",
}

class BgRequest(BaseModel):
    preset: str = "phantom"
    custom_prompt: str = ""
    category: str = "military"

@bg_router.post("/generate-bg")
async def generate_background(req: BgRequest):
    try:
        from imagen_engine import get_model
        model = get_model()
        prompt = BG_PRESETS.get(req.preset, BG_PRESETS["phantom"]) if req.preset != "custom" else req.custom_prompt
        print(f"[BG Generator] preset={req.preset}")
        response = model.generate_images(
            prompt=prompt,
            number_of_images=1,
            aspect_ratio="16:9",
        )
        if response.images:
            img = response.images[0]
            b64 = None
            for attr in ["_image_bytes", "image_bytes", "data", "_data"]:
                val = getattr(img, attr, None)
                if val:
                    if isinstance(val, str):
                        b64 = val
                    else:
                        b64 = base64.b64encode(val).decode("utf-8")
                    break
            if not b64 and hasattr(img, "_as_base64_string"):
                b64 = img._as_base64_string()
            if b64:
                data_url = "data:image/png;base64," + b64
                return JSONResponse({"status": "ok", "image": data_url, "preset": req.preset})
        return JSONResponse({"status": "error", "error": "No image generated"}, status_code=500)
    except Exception as e:
        print(f"[BG Generator] Error: {e}")
        return JSONResponse({"status": "error", "error": str(e)}, status_code=500)

@bg_router.get("/generate-bg/presets")
async def list_presets():
    return JSONResponse({"presets": [{"key": k, "label": k.upper()} for k in BG_PRESETS]})
