import os
import asyncio
import aiohttp
from dotenv import load_dotenv
from TikTokLive import TikTokLiveClient
from TikTokLive.client.web.web_settings import WebDefaults
from TikTokLive.events import CommentEvent, ConnectEvent, DisconnectEvent, JoinEvent
from cwis_engine import process_join, process_comment, process_stream_anomaly, state, print_status

load_dotenv()

# ─── CONFIG ───────────────────────────────────────────────────────────────────
TARGET              = "@cavalryspice"
CLOUD_URL           = "https://aegis-phantom-974184310088.us-east1.run.app"
TIKTOK_SIGN_API_KEY = os.getenv("TIKTOK_SIGN_API_KEY")

if not TIKTOK_SIGN_API_KEY:
    raise RuntimeError("❌ TIKTOK_SIGN_API_KEY not found in .env")

WebDefaults.tiktok_sign_api_key = TIKTOK_SIGN_API_KEY
# ──────────────────────────────────────────────────────────────────────────────

client = TikTokLiveClient(unique_id=TARGET)

@client.on(ConnectEvent)
async def on_connect(event: ConnectEvent):
    print(f"✅ RADAR LOCKED: {TARGET}")
    print_status()

@client.on(DisconnectEvent)
async def on_disconnect(event: DisconnectEvent):
    print("⚠️  LINK SEVERED — standing by...")
    await process_stream_anomaly("DISCONNECT")

@client.on(JoinEvent)
async def on_join(event: JoinEvent):
    try:
        user_info = event.user_info
        username  = user_info.unique_id or user_info.nick_name or "unknown"
        followers = getattr(user_info, 'follower_count', 0) or 0
        following = getattr(user_info, 'following_count', 0) or 0

        print(f"👤 JOIN: @{username} [{followers}F/{following}FG]")

        # Feed into CWIS
        await process_join(username, followers, following)

        # Forward to Cloud Run
        async with aiohttp.ClientSession() as session:
            await session.post(
                f"{CLOUD_URL}/intercept",
                json={"username": username, "comment": f"[JOIN] {username}", "userId": str(user_info.id or "")},
                timeout=aiohttp.ClientTimeout(total=3)
            )
    except Exception as e:
        print(f"❌ Join handler error: {e}")

@client.on(CommentEvent)
async def on_comment(event: CommentEvent):
    try:
        user_info = event.user_info
        username  = user_info.unique_id or user_info.nick_name or "unknown"
        comment   = event.comment
        followers = getattr(user_info, 'follower_count', 0) or 0
        following = getattr(user_info, 'following_count', 0) or 0

        print(f"📡 [{username}]: {comment}")

        # Feed into CWIS AI analyzer
        await process_comment(username, comment, followers, following)

        # Forward to Cloud Run
        async with aiohttp.ClientSession() as session:
            await session.post(
                f"{CLOUD_URL}/intercept",
                json={
                    "username": username,
                    "comment":  comment,
                    "userId":   str(user_info.id or ""),
                    "followers": followers,
                    "following": following
                },
                timeout=aiohttp.ClientTimeout(total=3)
            )
    except Exception as e:
        print(f"❌ Comment handler error: {e}")

if __name__ == "__main__":
    print(f"🚀 AEGIS RADAR + CWIS starting → {TARGET}")
    while True:
        try:
            client.run()
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"🔄 Reconnecting... {e}")
            import time
            time.sleep(10)
