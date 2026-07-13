# AEGIS PHANTOM — Lifespan Patch Script
# Upgrades airlock_engine.py from deprecated @app.on_event to lifespan handler
# Run from: C:\Users\VernonDunbar\Documents\Aegis_Phantom\

$file = "C:\Users\VernonDunbar\Documents\Aegis_Phantom\airlock_engine.py"

# Read file
$content = [System.IO.File]::ReadAllText($file, [System.Text.Encoding]::UTF8)

# CHANGE 1: Add asynccontextmanager import
$old1 = 'from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request'
$new1 = "from contextlib import asynccontextmanager`nfrom fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request"
$content = $content.Replace($old1, $new1)

# CHANGE 2: Replace bare app = FastAPI() with lifespan version
$old2 = 'app = FastAPI()'
$new2 = @'
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── STARTUP ──────────────────────────────────────────────────────────────
    print(f"""
    \u2588\u2588\u2588\u2588\u2588\u2588\u2557 \u2588\u2588\u2557  \u2588\u2588\u2557 \u2588\u2588\u2588\u2588\u2588\u2557 \u2588\u2588\u2588\u2557   \u2588\u2588\u2557\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2557 \u2588\u2588\u2588\u2588\u2588\u2588\u2557 \u2588\u2588\u2588\u2557   \u2588\u2588\u2588\u2557
   \u2588\u2588\u2554\u2550\u2550\u2550\u2550\u255d \u2588\u2588\u2551  \u2588\u2588\u2551\u2588\u2588\u2554\u2550\u2550\u2588\u2588\u2557\u2588\u2588\u2588\u2588\u2557  \u2588\u2588\u2551\u255a\u2550\u2550\u2588\u2588\u2554\u2550\u2550\u255d\u2588\u2588\u2554\u2550\u2550\u2550\u2588\u2588\u2557\u2588\u2588\u2588\u2588\u2557 \u2588\u2588\u2588\u2588\u2551
   \u2588\u2588\u2551  \u2588\u2588\u2588\u2557\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2551\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2551\u2588\u2588\u2554\u2588\u2588\u2557 \u2588\u2588\u2551   \u2588\u2588\u2551   \u2588\u2588\u2551   \u2588\u2588\u2551\u2588\u2588\u2554\u2588\u2588\u2588\u2588\u2554\u2588\u2588\u2551
   \u2588\u2588\u2551   \u2588\u2588\u2551\u2588\u2588\u2554\u2550\u2550\u2588\u2588\u2551\u2588\u2588\u2554\u2550\u2550\u2588\u2588\u2551\u2588\u2588\u2551\u255a\u2588\u2588\u2557\u2588\u2588\u2551   \u2588\u2588\u2551   \u2588\u2588\u2551   \u2588\u2588\u2551\u2588\u2588\u2551\u255a\u2588\u2588\u2554\u255d\u2588\u2588\u2551
   \u255a\u2588\u2588\u2588\u2588\u2588\u2588\u2554\u255d\u2588\u2588\u2551  \u2588\u2588\u2551\u2588\u2588\u2551  \u2588\u2588\u2551\u2588\u2588\u2551 \u255a\u2588\u2588\u2588\u2588\u2551   \u2588\u2588\u2551   \u255a\u2588\u2588\u2588\u2588\u2588\u2588\u2554\u255d\u2588\u2588\u2551 \u255a\u2550\u255d \u2588\u2588\u2551
    \u255a\u2550\u2550\u2550\u2550\u2550\u255d \u255a\u2550\u255d  \u255a\u2550\u255d\u255a\u2550\u255d  \u255a\u2550\u255d\u255a\u2550\u255d  \u255a\u2550\u2550\u2550\u255d   \u255a\u2550\u255d    \u255a\u2550\u2550\u2550\u2550\u2550\u255d \u255a\u2550\u255d     \u255a\u2550\u255d
    AEGIS PHANTOM \u2014 AIRLOCK ENGINE v3.2
    TARGET: @{TARGET}
    PATCHES: Client Guard | Room ID Validation | Block Retry | Exponential Backoff | Lifespan
    """)
    await load_goon_vault()
    asyncio.create_task(health_checkin())
    asyncio.create_task(run_monitor())
    yield
    # \u2500\u2500 SHUTDOWN \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    print("[AEGIS] AIRLOCK ENGINE \u2014 System disconnected cleanly.")

app = FastAPI(lifespan=lifespan)
'@
$content = $content.Replace($old2, $new2)

# CHANGE 3: Remove old @app.on_event startup block
$old3 = @'
#  STARTUP
@app.on_event("startup")
async def startup():
    print(f"""
    ================================================
    AEGIS PHANTOM -- AIRLOCK ENGINE v3.1
    TARGET: @{TARGET}
    PATCHES: Client Guard | Room ID Validation | Block Retry | Exponential Backoff
    ================================================
    """)
    await load_goon_vault()
    asyncio.create_task(health_checkin())
    asyncio.create_task(run_monitor())
'@
$new3 = '# STARTUP handled by lifespan context manager above'
$content = $content.Replace($old3, $new3)

# Write back
[System.IO.File]::WriteAllText($file, $content, [System.Text.Encoding]::UTF8)
Write-Host "✅ Lifespan patch applied — airlock_engine.py updated to v3.2"
Write-Host "✅ @app.on_event deprecated warning eliminated"
Write-Host "Run: python airlock_engine.py to verify clean startup"
