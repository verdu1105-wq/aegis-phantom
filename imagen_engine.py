"""
SitRep Imagen Engine v5 — Vertex AI Imagen 2
Uses imagen-3.0-generate-001 which is available on cybergrid project
"""

import os
import base64
import vertexai
from vertexai.preview.vision_models import ImageGenerationModel

GCP_PROJECT  = os.getenv("GCP_PROJECT", "cybergrid")
GCP_LOCATION = os.getenv("GCP_LOCATION", "us-central1")

_model = None

def get_model():
    global _model
    if _model is None:
        print(f"[Imagen] Init Vertex AI project={GCP_PROJECT} location={GCP_LOCATION}")
        vertexai.init(project=GCP_PROJECT, location=GCP_LOCATION)
        _model = ImageGenerationModel.from_pretrained("imagen-3.0-generate-001")
        print(f"[Imagen] Model ready: imagen-3.0-generate-001")
    return _model

CATEGORY_PALETTES = {
    "cyber":     "electric blue, deep black, crimson red, cold white terminal glow",
    "military":  "olive drab, steel grey, glowing amber, smoke and dust haze",
    "political": "cold navy blue, dark granite grey, muted gold, marble and shadow",
    "economic":  "rich amber, deep black, burnished gold, oil-slick sheen on water",
}

def build_prompt(title: str, hook: str, category: str) -> str:
    palette = CATEGORY_PALETTES.get(category.lower(), "high contrast monochromatic")
    return (
        f"Photorealistic editorial intelligence image. "
        f"Subject: {title}. Scene: {hook}. "
        f"Style: cinematic chiaroscuro lighting, heavy shadows, dramatic highlights, "
        f"film grain texture, classified intelligence briefing aesthetic. "
        f"Color palette: {palette}. "
        f"No visible text. No national flags. No human faces. "
        f"No watermarks. Landscape 16:9."
    )

def generate_brief_image(title: str, hook: str, category: str) -> str:
    try:
        model  = get_model()
        prompt = build_prompt(title, hook, category)
        print(f"[Imagen] Generating [{category}]: {title[:50]}...")

        response = model.generate_images(
            prompt=prompt,
            number_of_images=1,
            aspect_ratio="16:9",
        )

        if response.images:
            img = response.images[0]
            # Try all known byte extraction methods
            for attr in ['_image_bytes', 'image_bytes', 'data', '_data']:
                val = getattr(img, attr, None)
                if val:
                    if isinstance(val, str):
                        print(f"[Imagen] Got base64 via {attr}: {len(val)} chars")
                        return val
                    b64 = base64.b64encode(val).decode("utf-8")
                    print(f"[Imagen] Got bytes via {attr}: {len(b64)} chars")
                    return b64
            if hasattr(img, '_as_base64_string'):
                b64 = img._as_base64_string()
                print(f"[Imagen] Got via _as_base64_string: {len(b64)} chars")
                return b64
            print(f"[Imagen] Image object attrs: {[a for a in dir(img) if not a.startswith('__')]}")
            return ""
        else:
            print(f"[Imagen] Empty response — no images")
            return ""

    except Exception as e:
        import traceback
        print(f"[Imagen] Exception: {type(e).__name__}: {e}")
        print(traceback.format_exc())
        return ""


