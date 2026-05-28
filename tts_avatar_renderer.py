"""
SitRep TTS Avatar Renderer v1
Generates MP4 videos using Google TTS + animated avatars.
No camera required — fully automated content pipeline.

Avatars:
  phantom    → Cyber/Breaking — Jolly Roger skull, red glow
  sentry     → Military — Targeting reticle, tactical HUD  
  anchor     → Political — News desk, clean broadcast
  hud        → Economic — Data stream, market overlay
  biohazard  → Health/Outbreak — Animated biohazard symbol
  sonar      → Naval — Sonar ping, maritime ops
  classified → UFO/Anomalous — Eye, classified aesthetic
"""

import io
import json
import logging
import math
import os
import random
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("tts_avatar_renderer")

WIDTH, HEIGHT = 1080, 1920
FPS = 30

# ── CATEGORY → AVATAR ─────────────────────────────────────────────────────────
CATEGORY_AVATAR = {
    "cyber":     "phantom",
    "military":  "sentry",
    "political": "anchor",
    "economic":  "hud",
    "health":    "biohazard",
    "bio":       "biohazard",
    "naval":     "sonar",
    "ufo":       "classified",
    "breaking":  "phantom",
    "marketing": "anchor",
}

# ── PALETTES ───────────────────────────────────────────────────────────────────
PALETTES = {
    "phantom":    {"bg":(4,0,8),    "p":(196,30,30),  "s":(255,80,0),   "t":(255,200,200), "g":(180,0,0)},
    "sentry":     {"bg":(0,6,14),   "p":(0,200,140),  "s":(14,165,233), "t":(180,240,220), "g":(0,180,120)},
    "anchor":     {"bg":(8,6,18),   "p":(196,30,30),  "s":(255,255,255),"t":(240,240,255), "g":(100,20,20)},
    "hud":        {"bg":(0,8,20),   "p":(14,165,233), "s":(0,255,200),  "t":(180,220,255), "g":(0,100,180)},
    "biohazard":  {"bg":(2,8,2),    "p":(0,200,0),    "s":(180,255,0),  "t":(180,255,180), "g":(0,160,0)},
    "sonar":      {"bg":(0,4,12),   "p":(0,180,220),  "s":(0,255,240),  "t":(180,230,255), "g":(0,140,180)},
    "classified": {"bg":(5,0,16),   "p":(124,58,237), "s":(239,68,68),  "t":(196,181,253), "g":(76,29,149)},
}

# ── TTS VOICES ─────────────────────────────────────────────────────────────────
VOICES = {
    "phantom":   {"name":"en-US-Neural2-D","pitch":-4.0,"rate":0.92},
    "sentry":    {"name":"en-US-Neural2-J","pitch":-2.0,"rate":1.0},
    "anchor":    {"name":"en-US-Neural2-F","pitch":0.0, "rate":1.05},
    "hud":       {"name":"en-US-Neural2-D","pitch":-1.0,"rate":1.0},
    "biohazard": {"name":"en-US-Neural2-F","pitch":-1.0,"rate":0.95},
    "sonar":     {"name":"en-US-Neural2-J","pitch":-2.0,"rate":1.0},
    "classified":{"name":"en-US-Neural2-D","pitch":-3.0,"rate":0.9},
}

def get_fonts():
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    p = next((x for x in paths if os.path.exists(x)), None)
    if p:
        return {k: ImageFont.truetype(p, s) for k, s in
                [("huge",90),("large",62),("medium",44),("small",32),("tiny",24),("micro",18)]}
    d = ImageFont.load_default()
    return {k: d for k in ["huge","large","medium","small","tiny","micro"]}

def scanlines(draw, c, frame_num):
    for y in range(0, HEIGHT, 4):
        draw.line([(0,y),(WIDTH,y)], fill=tuple(max(0,v//6) for v in c["bg"]), width=1)

def wrap_text(text, max_chars=38):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if len(cur)+len(w)+1 <= max_chars:
            cur = cur+" "+w if cur else w
        else:
            if cur: lines.append(cur)
            cur = w
    if cur: lines.append(cur)
    return lines

# ── SCRIPT OVERLAY ─────────────────────────────────────────────────────────────
def draw_script_overlay(draw, frame_num, total_frames, script_lines, fonts, c):
    """Scrolling script at bottom of frame."""
    lines_per_screen = 4
    total_lines = len(script_lines)
    progress = frame_num / max(total_frames, 1)
    start_line = int(progress * max(0, total_lines - lines_per_screen))
    visible = script_lines[start_line:start_line + lines_per_screen]

    # Background box
    box_y = HEIGHT - 520
    draw.rectangle([0, box_y, WIDTH, HEIGHT - 60], fill=(0,0,0,180) if hasattr(draw,'_image') else (0,0,0))
    draw.line([0, box_y, WIDTH, box_y], fill=c["p"], width=3)

    for i, line in enumerate(visible):
        alpha = 255 if i < lines_per_screen - 1 else 180
        color = c["t"] if i > 0 else c["s"]
        draw.text((60, box_y + 20 + i * 100), line, font=fonts["medium"], fill=color)

    # Progress bar
    bar_w = int(WIDTH * progress)
    draw.rectangle([0, HEIGHT - 65, bar_w, HEIGHT - 55], fill=c["p"])
    draw.rectangle([0, HEIGHT - 65, WIDTH, HEIGHT - 55], outline=c["p"], width=1)

def draw_header(draw, brief, fonts, c, frame_num):
    """Top header bar."""
    draw.rectangle([0, 0, WIDTH, 110], fill=tuple(min(255,v+10) for v in c["bg"]))
    draw.line([0, 110, WIDTH, 110], fill=c["p"], width=3)

    cat = brief.get("cat", brief.get("category","INTEL")).upper()
    t = frame_num / FPS
    blink = int(t * 2) % 2 == 0

    if blink:
        draw.rectangle([40, 18, 200, 58], fill=c["p"])
        draw.text((120, 38), "LIVE", font=fonts["small"], fill=(0,0,0), anchor="mm")
    draw.text((220, 38), f"// {cat} INTELLIGENCE", font=fonts["small"], fill=c["t"], anchor="lm")
    draw.text((WIDTH-40, 38), "sitrep.media", font=fonts["tiny"], fill=c["s"], anchor="rm")

    # Priority badge
    score = brief.get("score","")
    if score:
        draw.text((WIDTH-40, 75), f"PRIORITY {score}", font=fonts["tiny"], fill=c["p"], anchor="rm")

# ── PHANTOM AVATAR ─────────────────────────────────────────────────────────────
def draw_phantom(draw, frame_num, c, fonts, brief):
    t = frame_num / FPS
    pulse = 0.7 + 0.3 * math.sin(t * 2.5)
    cx, cy = WIDTH//2, 680

    # Glow rings
    for r in range(280, 160, -25):
        intensity = int(25 * pulse * (1-(r-160)/120))
        gc = (min(255,c["g"][0]+intensity), c["g"][1], c["g"][2])
        draw.ellipse([cx-r,cy-r,cx+r,cy+r], outline=gc, width=1)

    # Skull
    sr = 155
    draw.ellipse([cx-sr,cy-sr,cx+sr,cy+sr], fill=c["bg"], outline=c["p"], width=4)

    # Eyes
    eg = int(200*pulse)
    for ex in [cx-55, cx+55]:
        draw.ellipse([ex-28,cy-58,ex+28,cy-2], fill=(eg,0,0), outline=c["p"], width=2)
        draw.ellipse([ex-12,cy-46,ex+12,cy-14], fill=(0,0,0))

    # Nose
    draw.polygon([(cx,cy+5),(cx-14,cy+38),(cx+14,cy+38)], fill=c["bg"], outline=c["p"])

    # Teeth
    for i in range(6):
        tx = cx-75+i*30
        col = c["p"] if i%2==0 else c["bg"]
        draw.rectangle([tx,cy+50,tx+20,cy+85], fill=col, outline=c["p"])

    # Crossbones
    for dx, dy in [(-1,1),(1,1)]:
        x1,y1 = cx+dx*(-180),cy+120
        x2,y2 = cx+dx*(-55),cy+210
        draw.line([x1,y1,x2,y2], fill=c["p"], width=9)
        draw.ellipse([x1-20,y1-15,x1+20,y1+15], fill=c["p"])
        draw.ellipse([x2-20,y2-15,x2+20,y2+15], fill=c["p"])

    # Pulse ring
    pr = int(175+18*math.sin(t*1.8))
    draw.ellipse([cx-pr,cy-pr,cx+pr,cy+pr], outline=c["p"], width=max(1,int(3*pulse)))

    draw.text((cx, cy+290), "PHANTOM // AEGIS INTEL", font=fonts["small"], fill=c["p"], anchor="mm")

# ── SENTRY AVATAR ──────────────────────────────────────────────────────────────
def draw_sentry(draw, frame_num, c, fonts, brief):
    t = frame_num / FPS
    pulse = 0.7+0.3*math.sin(t*3)
    cx, cy = WIDTH//2, 680

    for r in [230,190,150]:
        draw.ellipse([cx-r,cy-r,cx+r,cy+r], outline=c["p"], width=max(1,int(2*pulse)))

    for x1,y1,x2,y2 in [(cx-220,cy,cx-65,cy),(cx+65,cy,cx+220,cy),(cx,cy-220,cx,cy-65),(cx,cy+65,cx,cy+220)]:
        draw.line([x1,y1,x2,y2], fill=c["p"], width=3)

    dr = int(14*pulse)
    draw.ellipse([cx-dr,cy-dr,cx+dr,cy+dr], fill=c["p"])

    sa = int(t*90)%360
    draw.arc([cx-160,cy-160,cx+160,cy+160], sa, sa+90, fill=c["s"], width=5)

    for angle in range(0,360,15):
        rad = math.radians(angle)
        ri = 185 if angle%90!=0 else 168
        draw.line([cx+int(ri*math.cos(rad)),cy+int(ri*math.sin(rad)),
                   cx+int(198*math.cos(rad)),cy+int(198*math.sin(rad))], fill=c["p"], width=2)

    for bx,by,sx,sy in [(cx-175,cy-175,1,1),(cx+175,cy-175,-1,1),(cx-175,cy+175,1,-1),(cx+175,cy+175,-1,-1)]:
        draw.line([bx,by,bx+sx*40,by], fill=c["s"], width=3)
        draw.line([bx,by,bx,by+sy*40], fill=c["s"], width=3)

    draw.text((cx,cy+270), "SENTRY // TACTICAL", font=fonts["small"], fill=c["p"], anchor="mm")

# ── ANCHOR AVATAR ──────────────────────────────────────────────────────────────
def draw_anchor(draw, frame_num, c, fonts, brief):
    t = frame_num / FPS
    cx = WIDTH//2

    draw.text((cx, 300), "Sit", font=fonts["huge"], fill=c["s"], anchor="rm")
    draw.text((cx, 300), "Rep", font=fonts["huge"], fill=c["p"], anchor="lm")
    draw.text((cx, 390), "INTELLIGENCE", font=fonts["medium"], fill=c["t"], anchor="mm")
    draw.line([80,430,WIDTH-80,430], fill=c["p"], width=3)
    draw.line([80,438,WIDTH-80,438], fill=c["s"], width=1)

    # Animated data ticker
    ticker_items = ["LIVE","BREAKING","VERIFIED","SITREP"]
    ti = int(t*0.5) % len(ticker_items)
    blink = int(t*2)%2==0
    if blink:
        draw.rectangle([cx-120,480,cx+120,530], fill=c["p"])
        draw.text((cx,505), ticker_items[ti], font=fonts["small"], fill=(0,0,0), anchor="mm")

    # Source badges
    for i, src in enumerate(["CISA","ISW","OSINT","REUTERS"]):
        bx = 80 + i*240
        draw.rectangle([bx,560,bx+200,600], fill=tuple(v//3 for v in c["p"]), outline=c["p"])
        draw.text((bx+100,580), src, font=fonts["tiny"], fill=c["t"], anchor="mm")

    draw.text((cx, 650), "sitrep.media", font=fonts["small"], fill=c["s"], anchor="mm")

# ── BIOHAZARD AVATAR ───────────────────────────────────────────────────────────
def draw_biohazard(draw, frame_num, c, fonts, brief):
    t = frame_num / FPS
    pulse = 0.7+0.3*math.sin(t*2)
    spin = math.radians(t*15)
    cx, cy = WIDTH//2, 680

    # Outer glow
    for r in range(300,180,-25):
        intensity = int(20*pulse*(1-(r-180)/120))
        gc = (0,min(255,c["g"][1]+intensity),0)
        draw.ellipse([cx-r,cy-r,cx+r,cy+r], outline=gc, width=1)

    # Biohazard symbol — 3 interlocked circles
    bsr = 80  # bio symbol radius
    for i in range(3):
        angle = spin + math.radians(i*120)
        bx = cx + int(bsr*math.cos(angle))
        by = cy + int(bsr*math.sin(angle))
        draw.ellipse([bx-55,by-55,bx+55,by+55], outline=c["p"], width=8)

    # Center circle
    draw.ellipse([cx-35,cy-35,cx+35,cy+35], fill=c["p"])
    draw.ellipse([cx-20,cy-20,cx+20,cy+20], fill=c["bg"])

    # Warning ring
    pr = int(170+15*math.sin(t*2))
    draw.ellipse([cx-pr,cy-pr,cx+pr,cy+pr], outline=c["p"], width=max(2,int(4*pulse)))

    # Warning text
    warn_alpha = int(255*pulse)
    warn_col = (0,min(255,warn_alpha),0)
    draw.text((cx,cy+280), "BIOHAZARD // HEALTH INTEL", font=fonts["small"], fill=c["p"], anchor="mm")

    blink = int(t*2)%2==0
    if blink:
        draw.rectangle([cx-200,cy+310,cx+200,cy+360], fill=c["p"])
        draw.text((cx,cy+335), "CONTAINMENT ALERT", font=fonts["small"], fill=(0,0,0), anchor="mm")

# ── SONAR AVATAR ───────────────────────────────────────────────────────────────
def draw_sonar(draw, frame_num, c, fonts, brief):
    t = frame_num / FPS
    cx, cy = WIDTH//2, 680
    sweep = math.radians((t*60)%360)

    # Sonar rings
    for r in [40,80,120,160,200,240]:
        draw.ellipse([cx-r,cy-r,cx+r,cy+r], outline=c["p"], width=1)

    # Sweep line
    sx = cx + int(240*math.cos(sweep))
    sy = cy + int(240*math.sin(sweep))
    draw.line([cx,cy,sx,sy], fill=c["s"], width=3)

    # Fading sweep trail
    for i in range(1,8):
        trail_angle = sweep - math.radians(i*8)
        tx = cx + int(240*math.cos(trail_angle))
        ty = cy + int(240*math.sin(trail_angle))
        alpha = max(0,255-i*35)
        tc = (0,int(c["p"][1]*alpha//255),int(c["p"][2]*alpha//255))
        draw.line([cx,cy,tx,ty], fill=tc, width=2)

    # Ping blips
    rng = random.Random(int(t*2))
    for _ in range(3):
        pr = rng.randint(60,220)
        pa = rng.uniform(0,2*math.pi)
        px = cx+int(pr*math.cos(pa))
        py = cy+int(pr*math.sin(pa))
        draw.ellipse([px-5,py-5,px+5,py+5], fill=c["s"])

    # Cardinal markers
    for angle,label in [(0,"E"),(90,"S"),(180,"W"),(270,"N")]:
        rad = math.radians(angle)
        mx = cx+int(258*math.cos(rad))
        my = cy+int(258*math.sin(rad))
        draw.text((mx,my), label, font=fonts["tiny"], fill=c["t"], anchor="mm")

    draw.text((cx,cy+290), "SONAR // MARITIME OPS", font=fonts["small"], fill=c["p"], anchor="mm")

# ── CLASSIFIED AVATAR ──────────────────────────────────────────────────────────
def draw_classified(draw, frame_num, c, fonts, brief):
    t = frame_num / FPS
    pulse = 0.6+0.4*math.sin(t*1.5)
    cx, cy = WIDTH//2, 680

    # Outer rings
    for r,w in [(240,1),(200,2),(160,1)]:
        draw.ellipse([cx-r,cy-r,cx+r,cy+r], outline=c["p"], width=w)

    # Eye shape
    eye_w, eye_h = 200, 110
    # Eye white (dark)
    draw.ellipse([cx-eye_w,cy-eye_h,cx+eye_w,cy+eye_h], fill=tuple(v//4 for v in c["p"]), outline=c["p"], width=3)

    # Iris
    ir = int(70+10*math.sin(t*2))
    draw.ellipse([cx-ir,cy-ir,cx+ir,cy+ir], fill=c["p"])

    # Pupil
    pr = int(35+5*pulse)
    draw.ellipse([cx-pr,cy-pr,cx+pr,cy+pr], fill=(0,0,0))

    # Pupil glow
    gr = int(20*pulse)
    draw.ellipse([cx-gr,cy-gr,cx+gr,cy+gr], fill=c["s"])

    # Scanning lines across eye
    scan_y = cy - eye_h + int((t*40)%(eye_h*2))
    if cy-eye_h < scan_y < cy+eye_h:
        draw.line([cx-eye_w,scan_y,cx+eye_w,scan_y], fill=c["s"], width=2)

    # TOP SECRET badge
    blink = int(t*1.5)%2==0
    if blink:
        draw.rectangle([cx-200,cy+270,cx+200,cy+320], fill=c["p"])
        draw.text((cx,cy+295), "TOP SECRET // SCI", font=fonts["small"], fill=(0,0,0), anchor="mm")
    else:
        draw.text((cx,cy+295), "CLASSIFIED // ANOMALOUS", font=fonts["small"], fill=c["p"], anchor="mm")

# ── DISPATCHER ─────────────────────────────────────────────────────────────────

def draw_hud(draw, frame_num, c, fonts, brief):
    import random
    t = frame_num / FPS
    cx, cy = WIDTH//2, 680
    rng = random.Random(frame_num // 3)
    for _ in range(35):
        x = rng.randint(0, WIDTH)
        y = rng.randint(200, HEIGHT-600)
        char = rng.choice("0123456789ABCDEF")
        draw.text((x, y), char, font=fonts["micro"], fill=(0, int(c["p"][1]*0.4), int(c["p"][2]*0.4)))
    for r in [220, 180, 140]:
        draw.ellipse([cx-r, cy-r, cx+r, cy+r], outline=c["p"], width=1)
    sa = int(t * 90) % 360
    draw.arc([cx-160, cy-160, cx+160, cy+160], sa, sa+90, fill=c["s"], width=5)
    draw.text((cx, cy-20), "SENTRY", font=fonts["large"], fill=c["p"], anchor="mm")
    draw.text((cx, cy+40), "ECONOMIC INTEL", font=fonts["small"], fill=c["s"], anchor="mm")
    draw.text((cx, cy+270), "HUD // ECONOMIC INTEL", font=fonts["small"], fill=c["p"], anchor="mm")

AVATARS = {
    "phantom":   draw_phantom,
    "sentry":    draw_sentry,
    "anchor":    draw_anchor,
    "hud":       draw_hud,
    "biohazard": draw_biohazard,
    "sonar":     draw_sonar,
    "classified":draw_classified,
}

# ── RENDER FRAME ───────────────────────────────────────────────────────────────
def render_frame(frame_num, total_frames, brief, fonts, avatar, script_lines):
    c = PALETTES.get(avatar, PALETTES["anchor"])
    img = Image.new("RGB", (WIDTH, HEIGHT), c["bg"])
    draw = ImageDraw.Draw(img)
    scanlines(draw, c, frame_num)
    draw_header(draw, brief, fonts, c, frame_num)
    AVATARS[avatar](draw, frame_num, c, fonts, brief)
    draw_script_overlay(draw, frame_num, total_frames, script_lines, fonts, c)
    return img

# ── TTS ─────────────────────────────────────────────────────────────────────
def generate_tts(script, avatar, output_path):
    try:
        from google.cloud import texttospeech
        client = texttospeech.TextToSpeechClient()
        v = VOICES.get(avatar, VOICES["anchor"])
        clean = script.strip()
        for ch in ["*","#","—","–","►","▶","●","■"]:
            clean = clean.replace(ch, " ")
        words = clean.split()
        if len(words) > 800:
            clean = " ".join(words[:800])
        response = client.synthesize_speech(
            input=texttospeech.SynthesisInput(text=clean),
            voice=texttospeech.VoiceSelectionParams(language_code="en-US", name=v["name"]),
            audio_config=texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
                speaking_rate=v["rate"],
                pitch=v["pitch"],
                effects_profile_id=["headphone-class-device"],
            ),
        )
        with open(output_path, "wb") as f:
            f.write(response.audio_content)
        log.info(f"[TTS] {len(response.audio_content)} bytes → {output_path}")
        return True
    except Exception as e:
        log.error(f"[TTS] Error: {e}")
        return False

def get_audio_duration(path):
    try:
        r = subprocess.run(["ffprobe","-v","quiet","-show_entries","format=duration","-of","csv=p=0",path],
                           capture_output=True,text=True,timeout=10)
        return float(r.stdout.strip())
    except:
        return 60.0

# ── MAIN RENDER ────────────────────────────────────────────────────────────────
def render_tts_video(brief: dict, output_path: str) -> bool:
    """
    Full pipeline: TTS → frames → FFmpeg → MP4
    Returns True on success.
    """
    cat = brief.get("cat", brief.get("category","cyber"))
    avatar = CATEGORY_AVATAR.get(cat, "anchor")
    script = brief.get("script", brief.get("hook",""))
    if not script:
        log.error("[TTS Renderer] No script in brief")
        return False

    fonts = get_fonts()
    script_lines = []
    for para in script.split(". "):
        script_lines.extend(wrap_text(para.strip()))

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        audio_path = str(tmpdir / "voice.mp3")
        frame_dir  = tmpdir / "frames"
        frame_dir.mkdir()

        # 1. Generate TTS audio
        log.info(f"[TTS Renderer] Generating audio — avatar={avatar} category={cat}")
        has_audio = generate_tts(script, avatar, audio_path)
        duration = get_audio_duration(audio_path) if has_audio else 60.0
        duration = min(max(duration, 30.0), 90.0)  # clamp 30-90s
        total_frames = int(duration * FPS)

        log.info(f"[TTS Renderer] Duration={duration:.1f}s frames={total_frames}")

        # 2. Render frames
        for i in range(total_frames):
            frame = render_frame(i, total_frames, brief, fonts, avatar, script_lines)
            frame.save(str(frame_dir / f"frame_{i:05d}.png"), "PNG")
            if i % (FPS*5) == 0:
                log.info(f"  Frame {i}/{total_frames} ({i/total_frames*100:.0f}%)")

        # 3. FFmpeg encode
        if has_audio:
            cmd = [
                "ffmpeg","-y",
                "-framerate",str(FPS),
                "-i",str(frame_dir/"frame_%05d.png"),
                "-i",audio_path,
                "-c:v","libx264","-pix_fmt","yuv420p","-crf","23","-preset","fast",
                "-c:a","aac","-b:a","128k",
                "-shortest","-movflags","+faststart",
                output_path,
            ]
        else:
            cmd = [
                "ffmpeg","-y",
                "-framerate",str(FPS),
                "-i",str(frame_dir/"frame_%05d.png"),
                "-c:v","libx264","-pix_fmt","yuv420p","-crf","23","-preset","fast",
                "-movflags","+faststart",
                output_path,
            ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode == 0:
                log.info(f"[TTS Renderer] Video ready: {output_path}")
                return True
            else:
                log.error(f"[TTS Renderer] FFmpeg error: {result.stderr[-500:]}")
                return False
        except subprocess.TimeoutExpired:
            log.error("[TTS Renderer] FFmpeg timed out")
            return False
        except FileNotFoundError:
            log.error("[TTS Renderer] FFmpeg not found")
            return False

