import re

filepath = r"C:\Users\VernonDunbar\Documents\Aegis_Phantom\airlock_engine.py"

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

old = """async def load_goon_vault():
    global goon_vault
    try:
        async with httpx.AsyncClient(timeout=5) as http:
            r = await http.get(f"{CLOUD_URL}/api/goons",
                headers={"Authorization": f"Bearer {CLOUD_TOKEN}"}
            )
            data = r.json()
            goon_vault = set(data.get("goons", []))
            print(f"🛡️ Vault loaded: {len(goon_vault)} goons from cloud")
    except Exception as e:
        print(f"⚠️ Vault load failed: {e}")"""

new = """async def load_goon_vault():
    global goon_vault
    try:
        import redis as redis_lib
        r = redis_lib.from_url("redis://:DTqOikGrN26czTE4b4DWgj5Tkz849aq8@redis-10919.c284.us-east1-2.gce.cloud.redislabs.com:10919")
        raw = r.smembers("goons")
        goon_vault = set(g.decode() for g in raw)
        print(f"🛡️ Vault loaded: {len(goon_vault)} goons from Redis Cloud")
    except Exception as e:
        print(f"⚠️ Vault load failed: {e}")"""

if old in content:
    content = content.replace(old, new)
    print("✅ Exact match replaced")
else:
    # Fuzzy replace — find and replace the whole function
    content = re.sub(
        r'async def load_goon_vault\(\):.*?(?=async def |\Z)',
        new + '\n\n',
        content,
        flags=re.DOTALL
    )
    print("✅ Fuzzy match replaced")

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

print("Done — vault now loads from Redis Cloud directly")
