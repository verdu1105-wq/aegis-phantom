import os

path = r'C:\Users\VernonDunbar\Documents\Aegis_Phantom\main.py'

with open(path, 'r', encoding="utf-8-sig") as f:
    content = f.read()

# Strip everything we added before
for marker in ['# AEGIS SENTRY - AI Intelligence Proxy', '# -- AEGIS SENTRY', 'if __name__ == "__main__"']:
    idx = content.find(marker)
    if idx > 0:
        content = content[:idx].rstrip()

new_code = '''

# AEGIS SENTRY - AI Intelligence Proxy
@app.post("/api/intelligence")
async def intelligence_proxy(request: Request):
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=503, detail="AI engine not configured")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body")
    prompt = body.get("prompt", "").strip()
    do_stream = body.get("stream", False)
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt required")
    if len(prompt) > 8000:
        raise HTTPException(status_code=400, detail="prompt too long")

    headers = {
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01"
    }
    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1000,
        "stream": do_stream,
        "messages": [{"role": "user", "content": prompt}]
    }

    if do_stream:
        from fastapi.responses import StreamingResponse
        async def stream_gen():
            async with httpx.AsyncClient(timeout=60) as client:
                async with client.stream(
                    "POST",
                    "https://api.anthropic.com/v1/messages",
                    json=payload,
                    headers=headers
                ) as resp:
                    async for chunk in resp.aiter_bytes():
                        yield chunk
        return StreamingResponse(
            stream_gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )
    else:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                json=payload,
                headers=headers
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail="AI engine error")
            return JSONResponse(content=resp.json())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
'''

content = content + new_code

with open(path, 'w', encoding="utf-8-sig", newline='\n') as f:
    f.write(content)

print(f"Done. Total lines: {content.count(chr(10))}")

# Verify
import ast
try:
    ast.parse(content)
    print("Syntax OK")
except SyntaxError as e:
    print(f"Syntax ERROR: {e}")
