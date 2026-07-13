f = open(r'C:\Users\VernonDunbar\Documents\Aegis_Phantom\airlock_engine.py', 'r', encoding='utf-8')
c = f.read()
f.close()

# Fix 1: Import the playwright block at the top
old_import = 'import httpx'
new_import = '''import httpx
try:
    from tiktok_block_playwright import tiktok_block_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False'''

if old_import in c and 'PLAYWRIGHT_AVAILABLE' not in c:
    c = c.replace(old_import, new_import, 1)
    print("Fixed: playwright import added")

# Fix 2: Replace _tiktok_block_with_retry to use playwright
old_retry = 'async def _tiktok_block_with_retry(username: str):'
new_retry = '''async def _tiktok_block_with_retry(username: str):
    # Try Playwright first (headless browser -- bypasses TikTok bot detection)
    if PLAYWRIGHT_AVAILABLE:
        try:
            result = await tiktok_block_playwright(username, headless=True)
            if result:
                print(f"[OK] PLAYWRIGHT BLOCK CONFIRMED: @{username}")
                return
            else:
                print(f"[!] Playwright block failed for @{username} -- falling back to API")
        except Exception as e:
            print(f"[!] Playwright error for @{username}: {e}")
    # Fallback to original API method'''

if old_retry in c:
    c = c.replace(old_retry, new_retry)
    print("Fixed: playwright wired into block retry")

f = open(r'C:\Users\VernonDunbar\Documents\Aegis_Phantom\airlock_engine.py', 'w', encoding='utf-8')
f.write(c)
f.close()
print("Done.")
