"""
SitRep Imagen Engine - DEBUG VERSION
Raises exceptions instead of swallowing them
"""

import os
import base64
from google import genai
from google.genai import types

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

_client = None

def get_client():
    global _client
    if _client is None:
        if not GOOGLE_API_KEY:
            raise ValueError("GOOGLE_API_KEY not set")
        _client = genai.Client(api_key=GOOGLE_API_KEY)
    return _client

CATEGORY_PALETTES = {
    "cyber":     "electric blue, deep black, crimson red",
    "military":  "olive drab, steel grey, glowing amber",
    "political": "cold navy blue, dark granite grey, muted gold",
    "economic":  "rich amber, deep black, burnished gold",
}

def generate_brief_image(title: str, hook: str, category: str) -> str:
    # NO try/except — let errors bubble up for debugging
    client = get_client()
    palette = CATEGORY_PALETTES.get(category.lower(), "high contrast monochromatic")
    prompt = (
        f"Photorealistic editorial intelligence image. "
        f"Subject: {title}. Scene: {hook}. "
        f"Style: cinematic chiaroscuro lighting, heavy shadows, dramatic highlights, "
        f"film grain texture, classified intelligence briefing aesthetic. "
        f"Color palette: {palette}. "
        f"No visible text, no legible writing, no national flags, "
        f"no recognizable human faces. No watermarks. Landscape 16:9."
    )
    response = client.models.generate_images(
        model="imagen-3.0-generate-002",
        prompt=prompt,
        config=types.GenerateImagesConfig(
            number_of_images=1,
            aspect_ratio="16:9",
            safety_filter_level="block_only_high",
            person_generation="dont_allow",
        ),
    )
    if response.generated_images:
        image_bytes = response.generated_images[0].image.image_bytes
        return base64.b64encode(image_bytes).decode("utf-8")
    return ""
