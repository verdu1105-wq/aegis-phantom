f = open('airlock_engine.py', 'r', encoding='utf-8')
c = f.read()
f.close()

c = c.replace('from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request', 'from contextlib import asynccontextmanager\nfrom fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request')

c = c.replace('app = FastAPI()', '@asynccontextmanager\nasync def lifespan(app: FastAPI):\n    await load_goon_vault()\n    asyncio.create_task(health_checkin())\n    asyncio.create_task(run_monitor())\n    yield\n    print("[AEGIS] System disconnected cleanly.")\n\napp = FastAPI(lifespan=lifespan)')

c = c.replace('@app.on_event("startup")\nasync def startup():', '# STARTUP handled by lifespan above\nasync def startup_disabled():')

open('airlock_engine.py', 'w', encoding='utf-8').write(c)
print('Patch applied successfully')