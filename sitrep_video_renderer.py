"""
sitrep_video_renderer.py
Generates animated MP4 videos for each SitRep brief category.
Uses Pillow for frame generation + FFmpeg for encoding.
No external video services required — runs entirely inside Cloud Run.

Styles:
  kinetic       → Military / strike overlay
  hud           → Cyber / CVE heads-up display
  breaking_news → Political / Economic broadcast
  classified    → UFO / anomalous intel
  satellite     → Naval / ship damage
  terminal      → Fallback / general
"""

import json
import logging
import math
import os
import random
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("sitrep_renderer")

# ── Constants ──────────────────────────────────────────────────────────────────
WIDTH, HEIGHT = 1080, 1920       # TikTok portrait 9:16
FPS = 30
DURATION_SEC = 15                # 15-second video
TOTAL_FRAMES = FPS * DURATION_SEC

# Colors per style
COLORS = {
    "kinetic": {
        "bg": (10, 8, 0),
        "primary": (255, 68, 0),
        "secondary": (255, 140, 0),
        "text": (255, 220, 180),
        "accent": (200, 40, 0),
    },
    "hud": {
        "bg": (0, 8, 20),
        "primary": (14, 165, 233),
        "secondary": (0, 255, 200),
        "text": (180, 220, 255),
        "accent": (99, 102, 241),
    },
    "breaking_news": {
        "bg": (8, 0, 18),
        "primary": (220, 38, 38),
        "secondary": (251, 191, 36),
        "text": (255, 255, 255),
        "accent": (148, 163, 184),
    },
    "classified": {
        "bg": (5, 0, 16),
        "primary": (124, 58, 237),
        "secondary": (239, 68, 68),
        "text": (196, 181, 253),
        "accent": (76, 29, 149),
    },
    "satellite": {
        "bg": (2, 8, 20),
        "primary": (0, 255, 204),
        "secondary": (14, 165, 233),
        "text": (180, 240, 220),
        "accent": (239, 68, 68),
    },
    "terminal": {
        "bg": (5, 10, 5),
        "primary": (74, 222, 128),
        "secondary": (26, 74, 26),
        "text": (180, 255, 180),
        "accent": (16, 185, 129),
    },
}

# ── Font loading ───────────────────────────────────────────────────────────────
def get_fonts() -> dict:
    """Load fonts, fallback to default if custom not available."""
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
        "/system/fonts/DroidSansMono.ttf",
    ]
    font_path = None
    for fp in font_paths:
        if os.path.exists(fp):
            font_path = fp
            break

    try:
        if font_path:
            return {
                "small":   ImageFont.truetype(font_path, 28),
                "medium":  ImageFont.truetype(font_path, 40),
                "large":   ImageFont.truetype(font_path, 56),
                "xlarge":  ImageFont.truetype(font_path, 72),
                "title":   ImageFont.truetype(font_path, 88),
            }
    except Exception:
        pass

    default = ImageFont.load_default()
    return {k: default for k in ["small", "medium", "large", "xlarge", "title"]}


# ── Text wrapping helper ───────────────────────────────────────────────────────
def wrap_text(draw: ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    lines = []
    current = []
    for word in words:
        test = " ".join(current + [word])
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


# ── Scanline overlay ───────────────────────────────────────────────────────────
def draw_scanlines(img: Image, alpha: int = 20):
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for y in range(0, HEIGHT, 4):
        draw.line([(0, y), (WIDTH, y)], fill=(0, 0, 0, alpha))
    img = Image.alpha_composite(img.convert("RGBA"), overlay)
    return img.convert("RGB")


# ── Corner brackets (HUD style) ───────────────────────────────────────────────
def draw_hud_corners(draw: ImageDraw, color: tuple, size: int = 60, thickness: int = 3, margin: int = 40):
    m = margin
    # Top-left
    draw.line([(m, m), (m + size, m)], fill=color, width=thickness)
    draw.line([(m, m), (m, m + size)], fill=color, width=thickness)
    # Top-right
    draw.line([(WIDTH - m - size, m), (WIDTH - m, m)], fill=color, width=thickness)
    draw.line([(WIDTH - m, m), (WIDTH - m, m + size)], fill=color, width=thickness)
    # Bottom-left
    draw.line([(m, HEIGHT - m - size), (m, HEIGHT - m)], fill=color, width=thickness)
    draw.line([(m, HEIGHT - m), (m + size, HEIGHT - m)], fill=color, width=thickness)
    # Bottom-right
    draw.line([(WIDTH - m, HEIGHT - m - size), (WIDTH - m, HEIGHT - m)], fill=color, width=thickness)
    draw.line([(WIDTH - m - size, HEIGHT - m), (WIDTH - m, HEIGHT - m)], fill=color, width=thickness)


# ── Grid background ────────────────────────────────────────────────────────────
def draw_grid(draw: ImageDraw, color: tuple, spacing: int = 60, alpha_factor: float = 0.15):
    c = tuple(int(v * alpha_factor) for v in color)
    for x in range(0, WIDTH, spacing):
        draw.line([(x, 0), (x, HEIGHT)], fill=c, width=1)
    for y in range(0, HEIGHT, spacing):
        draw.line([(0, y), (WIDTH, y)], fill=c, width=1)


# ── STYLE RENDERERS ────────────────────────────────────────────────────────────

def render_frame_terminal(frame_idx: int, brief: dict, fonts: dict) -> Image:
    c = COLORS["terminal"]
    img = Image.new("RGB", (WIDTH, HEIGHT), c["bg"])
    draw = ImageDraw.Draw(img)
    draw_grid(draw, c["primary"], spacing=80, alpha_factor=0.08)

    progress = frame_idx / TOTAL_FRAMES
    lines = [
        f"> SITREP INTELLIGENCE // {brief.get('brief_id', 'SR-UNKNOWN')}",
        f"> CATEGORY: {brief.get('label', '').upper()}",
        f"> CLASSIFICATION: {brief.get('classification', 'UNCLASSIFIED')}",
        f"> PRIORITY: {brief.get('priority', 'A1')}",
        "",
        f"> {brief.get('headline', 'INTELLIGENCE BRIEF')}",
        "",
    ]
    for tp in brief.get("talking_points", [])[:3]:
        lines.append(f">> {tp.get('point', '')[:60]}")
        lines.append(f"   {tp.get('impact', '')[:60]}")
        lines.append("")

    lines.append(f"> BOTTOM LINE: {brief.get('bottom_line', '')[:80]}")
    lines.append("")
    lines.append("> SITREP INTEL // sitrep.media")

    visible_chars = int(progress * sum(len(l) + 1 for l in lines) * 1.1)
    y = 120
    chars_used = 0
    for line in lines:
        if chars_used >= visible_chars:
            break
        chars_to_show = min(len(line), max(0, visible_chars - chars_used))
        visible_line = line[:chars_to_show]
        draw.text((60, y), visible_line, font=fonts["small"], fill=c["primary"])
        chars_used += len(line) + 1
        y += 52

    # Blinking cursor
    if frame_idx % 30 < 20:
        draw.rectangle([60, y, 80, y + 32], fill=c["primary"])

    return img


def render_frame_hud(frame_idx: int, brief: dict, fonts: dict) -> Image:
    c = COLORS["hud"]
    img = Image.new("RGB", (WIDTH, HEIGHT), c["bg"])
    draw = ImageDraw.Draw(img)

    draw_grid(draw, c["primary"], spacing=60, alpha_factor=0.12)
    draw_hud_corners(draw, c["primary"], size=80, thickness=3)

    progress = frame_idx / TOTAL_FRAMES
    t = frame_idx / FPS

    # Rotating scan ring
    ring_cx, ring_cy = WIDTH // 2, HEIGHT // 3
    ring_r = 180
    draw.ellipse(
        [ring_cx - ring_r, ring_cy - ring_r, ring_cx + ring_r, ring_cy + ring_r],
        outline=(*c["primary"], 80), width=1
    )
    sweep_angle = (t * 90) % 360  # 1 rotation per 4 sec
    sweep_end_x = ring_cx + ring_r * math.cos(math.radians(sweep_angle))
    sweep_end_y = ring_cy + ring_r * math.sin(math.radians(sweep_angle))
    draw.line([(ring_cx, ring_cy), (sweep_end_x, sweep_end_y)], fill=c["primary"], width=2)

    # Priority badge
    priority = brief.get("priority", "A1")
    draw.rectangle([WIDTH // 2 - 80, ring_cy - 30, WIDTH // 2 + 80, ring_cy + 30],
                   outline=c["primary"], width=2)
    draw.text((WIDTH // 2 - 70, ring_cy - 22), f"THREAT // {priority}",
              font=fonts["small"], fill=c["primary"])

    # Headline — fade in
    if progress > 0.15:
        headline = brief.get("headline", "CYBER THREAT DETECTED")
        lines = wrap_text(draw, headline.upper(), fonts["large"], WIDTH - 120)
        y = HEIGHT // 2 - 60
        for line in lines[:3]:
            bbox = draw.textbbox((0, 0), line, font=fonts["large"])
            x = (WIDTH - (bbox[2] - bbox[0])) // 2
            draw.text((x, y), line, font=fonts["large"], fill=c["text"])
            y += 70

    # Talking points — scroll in
    if progress > 0.35:
        y = HEIGHT * 0.62
        for i, tp in enumerate(brief.get("talking_points", [])[:3]):
            alpha_progress = min(1.0, (progress - 0.35 - i * 0.12) / 0.1)
            if alpha_progress > 0:
                draw.text((80, y), f"◆ {tp.get('point', '')[:55]}",
                          font=fonts["small"], fill=c["secondary"])
                y += 52

    # Bottom line
    if progress > 0.75:
        bl = brief.get("bottom_line", "")[:80]
        draw.rectangle([60, HEIGHT - 220, WIDTH - 60, HEIGHT - 140],
                       fill=(14, 165, 233, 40), outline=c["primary"], width=1)
        draw.text((80, HEIGHT - 205), "BOTTOM LINE", font=fonts["small"], fill=c["primary"])
        lines = wrap_text(draw, bl, fonts["small"], WIDTH - 160)
        y = HEIGHT - 175
        for line in lines[:2]:
            draw.text((80, y), line, font=fonts["small"], fill=c["text"])
            y += 38

    # Watermark
    draw.text((WIDTH - 320, HEIGHT - 80), "SITREP // sitrep.media",
              font=fonts["small"], fill=(*c["primary"][:3],))

    return img


def render_frame_breaking_news(frame_idx: int, brief: dict, fonts: dict) -> Image:
    c = COLORS["breaking_news"]
    img = Image.new("RGB", (WIDTH, HEIGHT), c["bg"])
    draw = ImageDraw.Draw(img)
    progress = frame_idx / TOTAL_FRAMES

    is_breaking = brief.get("is_breaking", False)
    banner_color = (220, 38, 38) if is_breaking else (30, 58, 138)

    # Top banner
    draw.rectangle([0, 0, WIDTH, 140], fill=banner_color)
    banner_text = "⚡ BREAKING" if is_breaking else "SITREP INTELLIGENCE"
    draw.text((60, 30), banner_text, font=fonts["large"], fill=(255, 255, 255))
    draw.text((60, 95), brief.get("label", "").upper(), font=fonts["small"],
              fill=(255, 200, 200))

    # Category color stripe
    draw.rectangle([0, 140, 8, HEIGHT], fill=c["primary"])

    # Main headline
    if progress > 0.1:
        headline = brief.get("headline", "INTELLIGENCE BRIEF")
        lines = wrap_text(draw, headline.upper(), fonts["xlarge"], WIDTH - 120)
        y = 200
        for line in lines[:3]:
            draw.text((60, y), line, font=fonts["xlarge"], fill=c["text"])
            y += 90

    # Hook
    if progress > 0.25:
        hook = brief.get("hook", "")
        lines = wrap_text(draw, hook, fonts["medium"], WIDTH - 120)
        y = 530
        for line in lines[:3]:
            draw.text((60, y), line, font=fonts["medium"], fill=c["accent"])
            y += 56

    # Divider
    if progress > 0.3:
        draw.line([(60, 700), (WIDTH - 60, 700)], fill=c["primary"], width=2)

    # Talking points
    if progress > 0.35:
        y = 730
        for i, tp in enumerate(brief.get("talking_points", [])[:3]):
            if progress > 0.35 + i * 0.15:
                draw.rectangle([60, y - 4, 68, y + 44], fill=c["primary"])
                draw.text((90, y), tp.get("point", "")[:55],
                          font=fonts["small"], fill=c["text"])
                draw.text((90, y + 36), tp.get("impact", "")[:55],
                          font=fonts["small"], fill=c["accent"])
                y += 110

    # Bottom ticker
    draw.rectangle([0, HEIGHT - 120, WIDTH, HEIGHT], fill=(15, 23, 42))
    ticker_text = (f"  {brief.get('brief_id', '')}  •  "
                   f"{brief.get('bottom_line', '')[:60]}  •  "
                   f"sitrep.media  •  ")
    ticker_text = ticker_text * 3
    offset = int((frame_idx / FPS * 120) % (len(ticker_text) * 14)) * -1
    draw.text((offset, HEIGHT - 95), ticker_text, font=fonts["small"], fill=c["secondary"])

    return img


def render_frame_classified(frame_idx: int, brief: dict, fonts: dict) -> Image:
    c = COLORS["classified"]
    img = Image.new("RGB", (WIDTH, HEIGHT), c["bg"])
    draw = ImageDraw.Draw(img)
    progress = frame_idx / TOTAL_FRAMES
    t = frame_idx / FPS

    # Scanline effect
    for y_pos in range(0, HEIGHT, 6):
        alpha = 15 + int(5 * math.sin(y_pos * 0.1 + t))
        draw.line([(0, y_pos), (WIDTH, y_pos)], fill=(20, 0, 40), width=1)

    # File header
    draw.rectangle([60, 80, WIDTH - 60, 180], outline=c["primary"], width=1)
    draw.text((80, 95), f"FILE: {brief.get('brief_id', 'SR-CLASSIFIED')}",
              font=fonts["small"], fill=c["primary"])
    draw.text((80, 135), brief.get("classification", "TOP SECRET // SCI"),
              font=fonts["small"], fill=c["secondary"])

    # TOP SECRET stamp — rotated effect using multiple draws
    if progress > 0.2:
        stamp_x, stamp_y = WIDTH // 2 - 200, HEIGHT // 3 - 60
        draw.rectangle([stamp_x, stamp_y, stamp_x + 400, stamp_y + 100],
                       outline=(239, 68, 68), width=4)
        draw.text((stamp_x + 40, stamp_y + 18), "TOP SECRET",
                  font=fonts["title"], fill=(239, 68, 68))

    # Redacted bars that reveal content
    y = HEIGHT // 3 + 100
    for i, tp in enumerate(brief.get("talking_points", [])[:3]):
        reveal_progress = max(0, (progress - 0.3 - i * 0.2) / 0.15)
        reveal_progress = min(1.0, reveal_progress)

        point_text = tp.get("point", "")[:60]
        visible_chars = int(len(point_text) * reveal_progress)

        if reveal_progress < 1.0:
            # Redacted bar
            draw.rectangle([60, y, WIDTH - 60, y + 44], fill=(30, 0, 60))
            if visible_chars > 0:
                draw.text((80, y + 8), point_text[:visible_chars],
                          font=fonts["medium"], fill=c["text"])
        else:
            draw.text((80, y + 8), point_text, font=fonts["medium"], fill=c["text"])
            draw.text((80, y + 50), f"▶ {tp.get('impact', '')[:60]}",
                      font=fonts["small"], fill=c["primary"])
        y += 130

    # Scan line sweep
    scan_y = int((t * 300) % HEIGHT)
    draw.line([(0, scan_y), (WIDTH, scan_y)], fill=(*c["primary"], 60), width=2)

    # Bottom classification bar
    draw.rectangle([0, HEIGHT - 100, WIDTH, HEIGHT], fill=(30, 0, 60))
    draw.text((80, HEIGHT - 75),
              f"SITREP INTEL // sitrep.media // {brief.get('generated_at', '')}",
              font=fonts["small"], fill=c["primary"])

    return img


def render_frame_satellite(frame_idx: int, brief: dict, fonts: dict) -> Image:
    c = COLORS["satellite"]
    img = Image.new("RGB", (WIDTH, HEIGHT), c["bg"])
    draw = ImageDraw.Draw(img)
    progress = frame_idx / TOTAL_FRAMES
    t = frame_idx / FPS

    # Simulated SAR satellite imagery background (geometric shapes suggesting terrain)
    draw_grid(draw, c["primary"], spacing=40, alpha_factor=0.08)

    # Water surface simulation
    draw.rectangle([0, 350, WIDTH, 750], fill=(5, 20, 35))
    for i in range(20):
        wave_y = 350 + i * 20 + int(3 * math.sin(t + i * 0.5))
        draw.line([(0, wave_y), (WIDTH, wave_y)],
                  fill=(10, 40, 65), width=1)

    # Vessel markers
    vessels = [
        {"x": 280, "y": 520, "label": "VESSEL-01", "status": "NOMINAL"},
        {"x": 520, "y": 480, "label": "VESSEL-02", "status": "ANOMALY DETECTED"},
        {"x": 750, "y": 560, "label": "VESSEL-03", "status": "NOMINAL"},
    ]

    for v in vessels:
        color = c["accent"] if "ANOMALY" in v["status"] else c["primary"]
        draw.ellipse([v["x"] - 12, v["y"] - 6, v["x"] + 12, v["y"] + 6],
                     fill=color)
        # Crosshair on anomaly
        if "ANOMALY" in v["status"] and progress > 0.3:
            draw.line([(v["x"] - 25, v["y"]), (v["x"] + 25, v["y"])],
                      fill=c["accent"], width=1)
            draw.line([(v["x"], v["y"] - 25), (v["x"], v["y"] + 25)],
                      fill=c["accent"], width=1)
            # Pulsing ring
            ring_r = 20 + int(10 * math.sin(t * 4))
            draw.ellipse([v["x"] - ring_r, v["y"] - ring_r,
                          v["x"] + ring_r, v["y"] + ring_r],
                         outline=c["accent"], width=1)
        draw.text((v["x"] + 16, v["y"] - 10), v["label"],
                  font=fonts["small"], fill=color)

    # Satellite data overlay
    draw.rectangle([0, 0, WIDTH, 320], fill=(2, 8, 20))
    draw.text((60, 50), "SENTINEL-2 SAR // REAL-TIME PASS",
              font=fonts["medium"], fill=c["primary"])
    draw.text((60, 105), f"CATEGORY: {brief.get('label', '').upper()}",
              font=fonts["small"], fill=c["secondary"])
    draw.text((60, 150), f"BRIEF: {brief.get('brief_id', '')}",
              font=fonts["small"], fill=c["secondary"])

    # Scan sweep
    scan_y = 350 + int((t * 100) % 400)
    draw.line([(0, scan_y), (WIDTH, scan_y)], fill=(*c["primary"][:3],), width=2)

    # Headline
    if progress > 0.2:
        headline = brief.get("headline", "NAVAL INTELLIGENCE BRIEF")
        lines = wrap_text(draw, headline.upper(), fonts["large"], WIDTH - 120)
        y = 780
        for line in lines[:3]:
            draw.text((60, y), line, font=fonts["large"], fill=c["text"])
            y += 72

    # Talking points
    if progress > 0.4:
        y = 1000
        for i, tp in enumerate(brief.get("talking_points", [])[:3]):
            if progress > 0.4 + i * 0.15:
                draw.text((60, y), f"◆ {tp.get('point', '')[:55]}",
                          font=fonts["small"], fill=c["secondary"])
                draw.text((60, y + 38), f"  {tp.get('impact', '')[:55]}",
                          font=fonts["small"], fill=c["text"])
                y += 110

    # Bottom bar
    draw.rectangle([0, HEIGHT - 100, WIDTH, HEIGHT], fill=(2, 8, 20))
    draw.text((60, HEIGHT - 75), "SITREP // sitrep.media // UNCLASSIFIED//FOUO",
              font=fonts["small"], fill=c["primary"])

    return img


# ── Dispatch renderer ──────────────────────────────────────────────────────────
STYLE_RENDERERS = {
    "terminal":      render_frame_terminal,
    "hud":           render_frame_hud,
    "breaking_news": render_frame_breaking_news,
    "classified":    render_frame_classified,
    "satellite":     render_frame_satellite,
    "kinetic":       render_frame_breaking_news,  # kinetic uses breaking_news base + red palette
}


def render_video(brief: dict, output_path: str) -> bool:
    """
    Render a complete MP4 video for a brief.
    Returns True on success, False on failure.
    """
    style = brief.get("video_style", "terminal")
    renderer = STYLE_RENDERERS.get(style, render_frame_terminal)
    fonts = get_fonts()

    log.info(f"Rendering {style} video for {brief.get('brief_id', 'UNKNOWN')} → {output_path}")

    with tempfile.TemporaryDirectory() as tmpdir:
        frame_dir = Path(tmpdir) / "frames"
        frame_dir.mkdir()

        # Render each frame
        for i in range(TOTAL_FRAMES):
            frame = renderer(i, brief, fonts)
            frame_path = frame_dir / f"frame_{i:05d}.png"
            frame.save(str(frame_path), "PNG")

            if i % 30 == 0:
                log.info(f"  Frame {i}/{TOTAL_FRAMES}")

        # Encode with FFmpeg
        cmd = [
            "ffmpeg", "-y",
            "-framerate", str(FPS),
            "-i", str(frame_dir / "frame_%05d.png"),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-crf", "23",
            "-preset", "fast",
            "-movflags", "+faststart",
            output_path,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode == 0:
                log.info(f"Video rendered successfully: {output_path}")
                return True
            else:
                log.error(f"FFmpeg error: {result.stderr[-500:]}")
                return False
        except subprocess.TimeoutExpired:
            log.error("FFmpeg timed out after 120s")
            return False
        except FileNotFoundError:
            log.error("FFmpeg not found — install with: apt-get install ffmpeg")
            return False


def generate_caption(brief: dict) -> str:
    """Generate TikTok/social caption from brief."""
    headline = brief.get("headline", "")
    hook = brief.get("hook", "")
    tags_map = {
        "military":    "#MilitaryIntel #DefenseNews #SITREP #NationalSecurity #BreakingNews #MilTwitter #USMilitary",
        "cyber":       "#CyberSecurity #ThreatIntel #Hacking #InfoSec #SITREP #CyberAttack #ZeroDay #CVE #DarkWeb",
        "political":   "#PoliticalIntel #BreakingNews #SITREP #Politics #NationalSecurity #DC #Congress",
        "economic":    "#EconomicIntel #Markets #Geopolitics #SITREP #Economy #Sanctions #TradeWar #Finance",
        "ufo":         "#UAP #UFO #AnomalousIntel #AARO #SITREP #Disclosure #Pentagon #Classified",
        "naval":       "#NavalOps #MaritimeSecurity #FleetNews #SITREP #Navy #Warship #StraitOfHormuz #USNI",
    }
    hashtags = tags_map.get(brief.get("category", ""), "#SITREP #Intelligence")

    caption = f"{headline}\n\n{hook}\n\n"
    for tp in brief.get("talking_points", [])[:2]:
        caption += f"▶ {tp.get('point', '')}\n"
    caption += f"\nFull brief → sitrep.media\n\n{hashtags}"
    return caption[:2000]
