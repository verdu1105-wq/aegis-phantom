f = open(r'C:\Users\VernonDunbar\Documents\Aegis_Phantom\tts_avatar_renderer.py', 'r', encoding='utf-8')
c = f.read()
f.close()

hud = """
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

"""

c = c.replace("AVATARS = {", hud + "AVATARS = {")
open(r'C:\Users\VernonDunbar\Documents\Aegis_Phantom\tts_avatar_renderer.py', 'w', encoding='utf-8').write(c)
print("Done")
