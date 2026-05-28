"""
SitRep Marketing Video Agent — Powered by Gemini
Generates a 30-second marketing video using:
- Gemini Pro for script + shot sequencing
- Imagen 3 for AI-generated visuals
- Pillow + FFmpeg for video rendering
- GCS for storage
"""
import os
import json
import base64
import asyncio
import tempfile
import time
from pathlib import Path
from typing import Optional
from fastapi import APIRouter
from fastapi.responses import JSONResponse

marketing_router = APIRouter(prefix="/api/sitrep/marketing", tags=["marketing"])

GCP_PROJECT  = os.getenv("GCP_PROJECT", "cybergrid")
GCP_LOCATION = os.getenv("GCP_LOCATION", "us-central1")
GCS_BUCKET   = "cybergrid-sitrep-videos"

# ── GEMINI SCRIPT GENERATOR ──────────────────────────────────────────────────

async def generate_video_script(topic: str = "platform") -> dict:
    """Use Gemini to generate a marketing video script with shot list."""
    import anthropic
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
    
    prompt = f"""You are a video producer for SitRep Intelligence — an operator-grade threat intelligence platform.

Generate a 30-second marketing video script for: {topic}

Return ONLY valid JSON with this structure:
{{
  "title": "video title",
  "hook": "opening hook line",
  "shots": [
    {{
      "id": 1,
      "duration_frames": 90,
      "type": "title_card",
      "text_primary": "main text",
      "text_secondary": "subtitle",
      "visual_prompt": "imagen prompt for background",
      "category": "cyber|military|geo|economic|platform"
    }}
  ],
  "cta": "call to action text",
  "caption": "TikTok/social caption with hashtags"
}}

Shot types: title_card, intel_brief, map_scene, asset_reveal, pricing, cta
Total shots: 6-8 shots
Total frames: 900 (30 seconds at 30fps)
Make it dramatic, urgent, professional. No fluff."""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    
    text = response.content[0].text.strip()
    # Strip markdown if present
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    
    return json.loads(text.strip())

# ── IMAGEN FRAME GENERATOR ────────────────────────────────────────────────────

async def generate_shot_image(visual_prompt: str, category: str) -> Optional[bytes]:
    """Generate a background image for a shot using Imagen 3."""
    try:
        import vertexai
        from vertexai.preview.vision_models import ImageGenerationModel
        
        vertexai.init(project=GCP_PROJECT, location=GCP_LOCATION)
        model = ImageGenerationModel.from_pretrained("imagen-3.0-generate-001")
        
        # Enhance prompt for marketing aesthetic
        full_prompt = (
            f"Professional marketing visual for intelligence platform. "
            f"{visual_prompt}. "
            f"Cinematic lighting, dark background, dramatic atmosphere, "
            f"no text, no watermarks, 16:9 landscape, high quality."
        )
        
        response = model.generate_images(
            prompt=full_prompt,
            number_of_images=1,
            aspect_ratio="16:9",
        )
        
        if response.images:
            img = response.images[0]
            for attr in ['_image_bytes', 'image_bytes', 'data', '_data']:
                val = getattr(img, attr, None)
                if val:
                    return val if isinstance(val, bytes) else base64.b64decode(val)
        return None
    except Exception as e:
        print(f"[MarketingAgent] Imagen error: {e}")
        return None

# ── VIDEO FRAME RENDERER ──────────────────────────────────────────────────────

def render_marketing_frame(
    frame_idx: int,
    shot: dict,
    bg_image_bytes: Optional[bytes],
    total_frames: int,
    fonts: dict
) -> "Image":
    from PIL import Image, ImageDraw, ImageFont
    import io

    W, H = 1080, 1920  # TikTok vertical format
    
    # Background
    if bg_image_bytes:
        try:
            bg = Image.open(io.BytesIO(bg_image_bytes)).convert("RGBA")
            # Crop to vertical 9:16
            bg_w, bg_h = bg.size
            target_h = int(bg_w * 16 / 9)
            if target_h > bg_h:
                target_w = int(bg_h * 9 / 16)
                left = (bg_w - target_w) // 2
                bg = bg.crop((left, 0, left + target_w, bg_h))
            else:
                top = (bg_h - target_h) // 2
                bg = bg.crop((0, top, bg_w, top + target_h))
            bg = bg.resize((W, H), Image.LANCZOS)
        except:
            bg = Image.new("RGBA", (W, H), (10, 15, 30, 255))
    else:
        bg = Image.new("RGBA", (W, H), (10, 15, 30, 255))

    img = bg.copy()
    draw = ImageDraw.Draw(img)

    # Dark overlay for text readability
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 140))
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    # Progress bar at bottom
    progress = frame_idx / max(total_frames - 1, 1)
    bar_h = 4
    draw.rectangle([0, H - bar_h, int(W * progress), H], fill=(239, 68, 68, 255))

    # SitRep branding top
    draw.rectangle([0, 0, W, 80], fill=(10, 15, 30, 200))
    try:
        draw.text((40, 20), "SITREP", font=fonts.get("mono_sm"), fill=(239, 68, 68, 255))
        draw.text((170, 20), "INTELLIGENCE", font=fonts.get("mono_sm"), fill=(148, 163, 184, 255))
        draw.text((W - 200, 20), "sitrep.media", font=fonts.get("mono_sm"), fill=(71, 85, 105, 255))
    except:
        pass

    shot_type = shot.get("type", "title_card")

    # Animate text based on shot type
    shot_frame = frame_idx % max(shot.get("duration_frames", 90), 1)
    fade_in = min(shot_frame / 20, 1.0)
    alpha = int(255 * fade_in)

    if shot_type == "title_card":
        # Big centered title
        text = shot.get("text_primary", "")
        try:
            # Word wrap
            words = text.split()
            lines = []
            current = []
            for word in words:
                current.append(word)
                if len(" ".join(current)) > 18:
                    lines.append(" ".join(current[:-1]))
                    current = [word]
            if current:
                lines.append(" ".join(current))
            
            y = H // 2 - len(lines) * 80
            for line in lines:
                draw.text((W//2, y), line, font=fonts.get("bold_xl"),
                         fill=(241, 245, 249, alpha), anchor="mm")
                y += 140
        except:
            draw.text((W//2, H//2), text[:30], fill=(241, 245, 249, alpha), anchor="mm")

        sub = shot.get("text_secondary", "")
        if sub:
            try:
                draw.text((W//2, H//2 + 200), sub, font=fonts.get("mono_md"),
                         fill=(148, 163, 184, alpha), anchor="mm")
            except:
                pass

    elif shot_type == "intel_brief":
        # Intel card style
        card_y = 200
        draw.rectangle([60, card_y, W-60, card_y+600], fill=(13, 17, 32, 220))
        draw.rectangle([60, card_y, W-60, card_y+4], fill=(239, 68, 68, 255))
        
        cat = shot.get("category", "INTEL").upper()
        try:
            draw.text((100, card_y + 30), cat, font=fonts.get("mono_sm"),
                     fill=(239, 68, 68, alpha))
            draw.text((100, card_y + 80), shot.get("text_primary", ""),
                     font=fonts.get("bold_lg"), fill=(241, 245, 249, alpha))
            
            # Wrap secondary text
            sub = shot.get("text_secondary", "")
            y = card_y + 220
            words = sub.split()
            line = []
            for word in words:
                line.append(word)
                if len(" ".join(line)) > 30:
                    draw.text((100, y), " ".join(line[:-1]),
                             font=fonts.get("body"), fill=(148, 163, 184, alpha))
                    y += 60
                    line = [word]
            if line:
                draw.text((100, y), " ".join(line),
                         font=fonts.get("body"), fill=(148, 163, 184, alpha))
        except:
            pass

    elif shot_type == "cta":
        # Call to action
        try:
            draw.text((W//2, H//2 - 100), shot.get("text_primary", ""),
                     font=fonts.get("bold_xl"), fill=(241, 245, 249, alpha), anchor="mm")
            # CTA button
            btn_y = H//2 + 50
            draw.rectangle([W//2 - 300, btn_y, W//2 + 300, btn_y + 100],
                          fill=(239, 68, 68, 220))
            draw.text((W//2, btn_y + 50), shot.get("text_secondary", "sitrep.media"),
                     font=fonts.get("mono_md"), fill=(255, 255, 255, alpha), anchor="mm")
        except:
            pass

    elif shot_type == "pricing":
        try:
            draw.text((W//2, 300), "CHOOSE YOUR CLEARANCE",
                     font=fonts.get("mono_sm"), fill=(239, 68, 68, alpha), anchor="mm")
            
            tiers = [("SIGNAL", "$24.99"), ("BRIEF", "$39.99"), ("SENTRY", "$59.99")]
            colors = [(71, 85, 105), (239, 68, 68), (34, 197, 94)]
            y = 450
            for (name, price), color in zip(tiers, colors):
                draw.rectangle([100, y, W-100, y+180], fill=(13, 17, 32, 200))
                draw.rectangle([100, y, W-100, y+4], fill=(*color, 255))
                draw.text((160, y+30), name, font=fonts.get("mono_sm"),
                         fill=(*color, alpha))
                draw.text((160, y+80), price, font=fonts.get("bold_lg"),
                         fill=(241, 245, 249, alpha))
                y += 220
        except:
            pass

    return img.convert("RGB")


def get_marketing_fonts() -> dict:
    from PIL import ImageFont
    fonts = {}
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    
    def load(path, size):
        try:
            return ImageFont.truetype(path, size)
        except:
            return ImageFont.load_default()
    
    mono_bold = font_paths[0] if Path(font_paths[0]).exists() else font_paths[3]
    sans_bold = font_paths[2] if Path(font_paths[2]).exists() else font_paths[3]
    sans = font_paths[3] if Path(font_paths[3]).exists() else font_paths[3]
    
    fonts["bold_xl"] = load(sans_bold, 90)
    fonts["bold_lg"] = load(sans_bold, 65)
    fonts["mono_md"] = load(mono_bold, 45)
    fonts["mono_sm"] = load(mono_bold, 32)
    fonts["body"]    = load(sans, 40)
    return fonts


async def render_marketing_video(script: dict, shot_images: list) -> Optional[str]:
    """Render frames and encode to MP4 using FFmpeg."""
    import subprocess
    from PIL import Image
    import io

    FPS = 30
    fonts = get_marketing_fonts()
    shots = script.get("shots", [])
    
    with tempfile.TemporaryDirectory() as tmpdir:
        frame_num = 0
        
        for shot_idx, shot in enumerate(shots):
            duration = shot.get("duration_frames", 90)
            bg_bytes = shot_images[shot_idx] if shot_idx < len(shot_images) else None
            
            for f in range(duration):
                frame = render_marketing_frame(
                    frame_idx=f,
                    shot=shot,
                    bg_image_bytes=bg_bytes,
                    total_frames=duration,
                    fonts=fonts,
                )
                frame_path = os.path.join(tmpdir, f"frame_{frame_num:06d}.png")
                frame.save(frame_path)
                frame_num += 1
        
        # Encode with FFmpeg
        output_path = os.path.join(tmpdir, "marketing.mp4")
        cmd = [
            "ffmpeg", "-y",
            "-framerate", str(FPS),
            "-i", os.path.join(tmpdir, "frame_%06d.png"),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-crf", "23",
            "-preset", "fast",
            output_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[MarketingAgent] FFmpeg error: {result.stderr}")
            return None
        
        # Upload to GCS
        from google.cloud import storage
        gcs = storage.Client(project=GCP_PROJECT)
        bucket = gcs.bucket(GCS_BUCKET)
        timestamp = int(time.time())
        gcs_path = f"marketing/sitrep_marketing_{timestamp}.mp4"
        blob = bucket.blob(gcs_path)
        blob.upload_from_filename(output_path, content_type="video/mp4")
        url = f"https://storage.googleapis.com/{GCS_BUCKET}/{gcs_path}"
        print(f"[MarketingAgent] Video uploaded: {url}")
        return url


# ── API ROUTES ────────────────────────────────────────────────────────────────

@marketing_router.post("/generate/start")
async def start_marketing_video(topic: str = "SitRep Intelligence Platform"):
    """Start async video generation — returns job_id immediately."""
    import asyncio, uuid
    job_id = f"mktg-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    
    # Store job status in Redis
    try:
        import redis as redis_lib
        r = redis_lib.from_url(os.getenv("REDIS_URL", ""))
        r.setex(f"marketing:job:{job_id}", 3600, "running")
    except:
        pass
    
    # Fire async task
    asyncio.create_task(_run_marketing_job(job_id, topic))
    
    return JSONResponse({"job_id": job_id, "status": "started", "poll_url": f"/api/sitrep/marketing/status/{job_id}"})


async def _run_marketing_job(job_id: str, topic: str):
    """Background task for video generation."""
    try:
        import redis as redis_lib
        r = redis_lib.from_url(os.getenv("REDIS_URL", ""))
    except:
        r = None
    
    try:
        script = await generate_video_script(topic)
        shots = script.get("shots", [])
        image_tasks = [generate_shot_image(s.get("visual_prompt", ""), s.get("category", "")) for s in shots]
        shot_images = await asyncio.gather(*image_tasks)
        video_url = await render_marketing_video(script, list(shot_images))
        
        result = {"status": "complete", "video_url": video_url, "caption": script.get("caption", ""), "shots": len(shots)}
        if r:
            r.setex(f"marketing:job:{job_id}", 3600, json.dumps(result))
    except Exception as e:
        if r:
            r.setex(f"marketing:job:{job_id}", 3600, json.dumps({"status": "error", "error": str(e)}))


@marketing_router.get("/status/{job_id}")
async def get_job_status(job_id: str):
    """Poll for marketing video generation status."""
    try:
        import redis as redis_lib
        r = redis_lib.from_url(os.getenv("REDIS_URL", ""))
        val = r.get(f"marketing:job:{job_id}")
        if val:
            data = json.loads(val) if val != b"running" else {"status": "running"}
            return JSONResponse(data)
        return JSONResponse({"status": "not_found"}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@marketing_router.post("/generate")
async def generate_marketing_video(topic: str = "SitRep Intelligence Platform"):
    """
    Full pipeline: Gemini script → Imagen visuals → FFmpeg video → GCS
    Returns GCS URL of rendered marketing video.
    """
    try:
        print(f"[MarketingAgent] Starting video generation for: {topic}")
        
        # Step 1: Generate script with Gemini/Claude
        print("[MarketingAgent] Generating script...")
        script = await generate_video_script(topic)
        shots = script.get("shots", [])
        print(f"[MarketingAgent] Script ready: {len(shots)} shots")
        
        # Step 2: Generate visuals for each shot in parallel
        print("[MarketingAgent] Generating visuals...")
        image_tasks = [
            generate_shot_image(
                shot.get("visual_prompt", "dark intelligence platform aesthetic"),
                shot.get("category", "platform")
            )
            for shot in shots
        ]
        shot_images = await asyncio.gather(*image_tasks)
        generated = sum(1 for img in shot_images if img)
        print(f"[MarketingAgent] Generated {generated}/{len(shots)} visuals")
        
        # Step 3: Render video
        print("[MarketingAgent] Rendering video...")
        video_url = await render_marketing_video(script, list(shot_images))
        
        if not video_url:
            return JSONResponse({"error": "Video rendering failed"}, status_code=500)
        
        return JSONResponse({
            "status": "ok",
            "video_url": video_url,
            "script": script,
            "caption": script.get("caption", ""),
            "shots_count": len(shots),
            "visuals_generated": generated,
        })
        
    except Exception as e:
        import traceback
        print(f"[MarketingAgent] Error: {e}\n{traceback.format_exc()}")
        return JSONResponse({"error": str(e)}, status_code=500)


@marketing_router.get("/scripts")
async def list_marketing_scripts():
    """List available marketing video scripts."""
    return JSONResponse({
        "topics": [
            "SitRep Intelligence Platform",
            "Theater Map — Live Intelligence",
            "AI Image Generator",
            "Cyber Threat Intelligence",
            "Subscribe — Choose Your Clearance",
            "Dr. Viera × SitRep Collaboration",
        ]
    })
