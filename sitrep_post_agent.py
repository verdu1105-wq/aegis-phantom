"""
sitrep_post_agent.py
SitRep Social Posting Agent
Handles:
  1. FCM push notification to Vern's iPhone (one-tap approve or full auto)
  2. TikTok Content Posting API
  3. LinkedIn Video Post API
  4. X (Twitter) v2 Media Upload + Tweet

Set AUTOPOST=true in env for fully hands-free posting.
Set AUTOPOST=false to require FCM approval tap from iPhone first.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import httpx
import redis as redis_lib

log = logging.getLogger("sitrep_post_agent")

# ── Config from environment ────────────────────────────────────────────────────
REDIS_URL        = os.environ.get("REDIS_URL", "")
AUTOPOST         = os.environ.get("AUTOPOST", "false").lower() == "true"
FCM_SERVER_KEY   = os.environ.get("FCM_SERVER_KEY", "")
FCM_DEVICE_TOKEN = os.environ.get("FCM_DEVICE_TOKEN", "")   # Vern's iPhone token
from sitrep_post_agent_token_helper import get_tiktok_token
TIKTOK_ACCESS_TOKEN = get_tiktok_token()
LINKEDIN_ACCESS_TOKEN = os.environ.get("LINKEDIN_ACCESS_TOKEN", "")
LINKEDIN_PERSON_ID   = os.environ.get("LINKEDIN_PERSON_ID", "")  # urn:li:person:XXX
X_BEARER_TOKEN   = os.environ.get("X_BEARER_TOKEN", "")
X_API_KEY        = os.environ.get("X_API_KEY", "")
X_API_SECRET     = os.environ.get("X_API_SECRET", "")
X_ACCESS_TOKEN   = os.environ.get("X_ACCESS_TOKEN", "")
X_ACCESS_SECRET  = os.environ.get("X_ACCESS_SECRET", "")

# GCS bucket for video storage
GCS_BUCKET       = os.environ.get("GCS_BUCKET", "cybergrid-sitrep-videos")

r = redis_lib.from_url(REDIS_URL, decode_responses=True)


# ── GCS Upload ─────────────────────────────────────────────────────────────────
async def upload_video_to_gcs(video_path: str, brief_id: str) -> Optional[str]:
    """Upload rendered video to GCS and return public URL."""
    from google.cloud import storage
    try:
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET)
        blob_name = f"briefs/{brief_id}.mp4"
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(video_path, content_type="video/mp4")
        blob.make_public()
        url = blob.public_url
        log.info(f"Video uploaded to GCS: {url}")
        return url
    except Exception as e:
        log.error(f"GCS upload failed: {e}")
        return None


# ── FCM Push Notification ──────────────────────────────────────────────────────
async def send_fcm_notification(brief: dict, video_url: str, caption: str) -> bool:
    """
    Send push notification to Vern's iPhone via FCM.
    Notification includes preview data and approve/skip actions.
    """
    if not FCM_SERVER_KEY or not FCM_DEVICE_TOKEN:
        log.warning("FCM not configured — skipping notification")
        return False

    payload = {
        "to": FCM_DEVICE_TOKEN,
        "notification": {
            "title": f"📡 SitRep Ready: {brief.get('category', '').upper()}",
            "body": brief.get("headline", "New brief ready to post"),
            "sound": "default",
        },
        "data": {
            "brief_id": brief.get("brief_id", ""),
            "category": brief.get("category", ""),
            "video_url": video_url,
            "caption": caption[:200],
            "action": "sitrep_post_approval",
            "approve_url": f"https://aegis-cwis-974184310088.us-east1.run.app/api/sitrep/approve/{brief.get('brief_id', '')}",
        },
        "android": {"priority": "high"},
        "apns": {
            "headers": {"apns-priority": "10"},
            "payload": {
                "aps": {
                    "category": "SITREP_POST",
                    "mutable-content": 1,
                }
            }
        }
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://fcm.googleapis.com/fcm/send",
                json=payload,
                headers={
                    "Authorization": f"key={FCM_SERVER_KEY}",
                    "Content-Type": "application/json",
                },
                timeout=10.0,
            )
            if resp.status_code == 200:
                log.info(f"FCM notification sent for {brief.get('brief_id')}")
                return True
            else:
                log.error(f"FCM error {resp.status_code}: {resp.text[:200]}")
                return False
    except Exception as e:
        log.error(f"FCM send failed: {e}")
        return False


# ── TikTok Posting ─────────────────────────────────────────────────────────────
async def post_to_tiktok(video_path: str, caption: str, brief_id: str) -> Optional[str]:
    """
    Post video to TikTok using Content Posting API v2 FILE_UPLOAD.
    video_path is a public GCS URL - we download then upload to TikTok.
    Returns publish_id on success or None.
    """
    token = get_tiktok_token()
    if not token:
        log.warning("TikTok not configured - skipping")
        return None
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            # Download video from GCS
            log.info(f"[TikTok] Downloading from GCS: {video_path}")
            dl_resp = await client.get(video_path)
            if dl_resp.status_code != 200:
                log.error(f"[TikTok] GCS download failed: {dl_resp.status_code}")
                return None
            video_bytes = dl_resp.content
            video_size = len(video_bytes)
            log.info(f"[TikTok] Downloaded {video_size} bytes")

            # Step 1: Initialize upload
            init_payload = {
                "post_info": {
                    "title": caption[:2200],
                    "privacy_level": "PUBLIC_TO_EVERYONE",
                    "disable_duet": False,
                    "disable_comment": False,
                    "disable_stitch": False,
                    "video_cover_timestamp_ms": 2000,
                },
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": video_size,
                    "chunk_size": video_size,
                    "total_chunk_count": 1,
                }
            }
            init_resp = await client.post(
                "https://open.tiktokapis.com/v2/post/publish/video/init/",
                json=init_payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=UTF-8",
                },
            )
            if init_resp.status_code != 200:
                log.error(f"TikTok init failed: {init_resp.text[:300]}")
                return None

            init_data = init_resp.json()
            publish_id = init_data.get("data", {}).get("publish_id")
            upload_url = init_data.get("data", {}).get("upload_url")

            if not publish_id or not upload_url:
                log.error(f"TikTok init missing fields: {init_data}")
                return None

            # Step 2: Upload video bytes
            upload_resp = await client.put(
                upload_url,
                content=video_bytes,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Range": f"bytes 0-{video_size - 1}/{video_size}",
                    "Content-Length": str(video_size),
                },
            )
            if upload_resp.status_code not in (200, 201, 206):
                log.error(f"TikTok upload failed: {upload_resp.status_code}")
                return None

            log.info(f"TikTok post initiated: {publish_id}")
            return publish_id

    except Exception as e:
        log.error(f"TikTok post failed: {e}")
        return None



# ── LinkedIn Posting ───────────────────────────────────────────────────────────
async def post_to_linkedin(video_path: str, caption: str, brief_id: str) -> Optional[str]:
    """
    Post video to LinkedIn using Video Share API.
    Returns post URN on success or None.
    """
    if not LINKEDIN_ACCESS_TOKEN or not LINKEDIN_PERSON_ID:
        log.warning("LinkedIn not configured — skipping")
        return None

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            headers = {
                "Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}",
                "Content-Type": "application/json",
                "X-Restli-Protocol-Version": "2.0.0",
            }

            # Step 1: Register upload
            register_payload = {
                "registerUploadRequest": {
                    "recipes": ["urn:li:digitalmediaRecipe:feedshare-video"],
                    "owner": f"urn:li:person:{LINKEDIN_PERSON_ID}",
                    "serviceRelationships": [{
                        "relationshipType": "OWNER",
                        "identifier": "urn:li:userGeneratedContent",
                    }]
                }
            }

            reg_resp = await client.post(
                "https://api.linkedin.com/v2/assets?action=registerUpload",
                json=register_payload,
                headers=headers,
            )

            if reg_resp.status_code != 200:
                log.error(f"LinkedIn register failed: {reg_resp.text[:300]}")
                return None

            reg_data = reg_resp.json()
            asset = reg_data.get("value", {}).get("asset")
            upload_url = (reg_data.get("value", {})
                          .get("uploadMechanism", {})
                          .get("com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest", {})
                          .get("uploadUrl"))

            if not asset or not upload_url:
                log.error(f"LinkedIn register missing fields")
                return None

            # Step 2: Upload video
            with open(video_path, "rb") as f:
                upload_resp = await client.put(
                    upload_url,
                    content=f.read(),
                    headers={"Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}"},
                )

            if upload_resp.status_code not in (200, 201):
                log.error(f"LinkedIn upload failed: {upload_resp.status_code}")
                return None

            # Step 3: Create post
            post_payload = {
                "author": f"urn:li:person:{LINKEDIN_PERSON_ID}",
                "lifecycleState": "PUBLISHED",
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": {
                        "shareCommentary": {"text": caption[:3000]},
                        "shareMediaCategory": "VIDEO",
                        "media": [{
                            "status": "READY",
                            "description": {"text": brief_id},
                            "media": asset,
                            "title": {"text": "SitRep Intelligence Brief"},
                        }]
                    }
                },
                "visibility": {
                    "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
                }
            }

            post_resp = await client.post(
                "https://api.linkedin.com/v2/ugcPosts",
                json=post_payload,
                headers=headers,
            )

            if post_resp.status_code in (200, 201):
                post_urn = post_resp.headers.get("x-restli-id", "unknown")
                log.info(f"LinkedIn post created: {post_urn}")
                return post_urn
            else:
                log.error(f"LinkedIn post failed: {post_resp.text[:300]}")
                return None

    except Exception as e:
        log.error(f"LinkedIn post failed: {e}")
        return None


# ── X (Twitter) Posting ────────────────────────────────────────────────────────
async def post_to_x(video_path: str, caption: str) -> Optional[str]:
    """
    Post video to X using v1.1 media upload + v2 tweet.
    Returns tweet_id on success or None.
    """
    if not X_API_KEY or not X_API_SECRET:
        log.warning("X/Twitter not configured — skipping")
        return None

    try:
        import base64
        import hashlib
        import hmac
        import time
        import urllib.parse

        def oauth_header(method, url, params, token, token_secret):
            oauth_params = {
                "oauth_consumer_key": X_API_KEY,
                "oauth_nonce": hashlib.md5(str(time.time()).encode()).hexdigest(),
                "oauth_signature_method": "HMAC-SHA1",
                "oauth_timestamp": str(int(time.time())),
                "oauth_token": token,
                "oauth_version": "1.0",
            }
            all_params = {**params, **oauth_params}
            param_str = "&".join(f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(str(v), safe='')}"
                                 for k, v in sorted(all_params.items()))
            base = f"{method}&{urllib.parse.quote(url, safe='')}&{urllib.parse.quote(param_str, safe='')}"
            signing_key = f"{urllib.parse.quote(X_API_SECRET, safe='')}&{urllib.parse.quote(token_secret, safe='')}"
            sig = base64.b64encode(hmac.new(signing_key.encode(), base.encode(), hashlib.sha1).digest()).decode()
            oauth_params["oauth_signature"] = sig
            return "OAuth " + ", ".join(f'{k}="{urllib.parse.quote(str(v), safe="")}"'
                                         for k, v in sorted(oauth_params.items()))

        async with httpx.AsyncClient(timeout=120.0) as client:
            # Step 1: INIT media upload
            file_size = os.path.getsize(video_path)
            init_params = {
                "command": "INIT",
                "total_bytes": str(file_size),
                "media_type": "video/mp4",
                "media_category": "tweet_video",
            }
            auth = oauth_header("POST", "https://upload.twitter.com/1.1/media/upload.json",
                                 init_params, X_ACCESS_TOKEN, X_ACCESS_SECRET)
            init_resp = await client.post(
                "https://upload.twitter.com/1.1/media/upload.json",
                data=init_params,
                headers={"Authorization": auth},
            )
            if init_resp.status_code != 202:
                log.error(f"X INIT failed: {init_resp.text[:200]}")
                return None
            media_id = init_resp.json().get("media_id_string")

            # Step 2: APPEND chunks (5MB chunks)
            chunk_size = 5 * 1024 * 1024
            segment_index = 0
            with open(video_path, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    append_auth = oauth_header(
                        "POST", "https://upload.twitter.com/1.1/media/upload.json",
                        {"command": "APPEND", "media_id": media_id, "segment_index": str(segment_index)},
                        X_ACCESS_TOKEN, X_ACCESS_SECRET,
                    )
                    await client.post(
                        "https://upload.twitter.com/1.1/media/upload.json",
                        data={"command": "APPEND", "media_id": media_id, "segment_index": str(segment_index)},
                        files={"media": chunk},
                        headers={"Authorization": append_auth},
                    )
                    segment_index += 1

            # Step 3: FINALIZE
            fin_auth = oauth_header("POST", "https://upload.twitter.com/1.1/media/upload.json",
                                     {"command": "FINALIZE", "media_id": media_id},
                                     X_ACCESS_TOKEN, X_ACCESS_SECRET)
            await client.post(
                "https://upload.twitter.com/1.1/media/upload.json",
                data={"command": "FINALIZE", "media_id": media_id},
                headers={"Authorization": fin_auth},
            )

            # Step 4: Wait for processing
            await asyncio.sleep(10)

            # Step 5: Post tweet
            tweet_text = caption[:280]
            tweet_auth = oauth_header("POST", "https://api.twitter.com/2/tweets",
                                       {}, X_ACCESS_TOKEN, X_ACCESS_SECRET)
            tweet_resp = await client.post(
                "https://api.twitter.com/2/tweets",
                json={"text": tweet_text, "media": {"media_ids": [media_id]}},
                headers={"Authorization": tweet_auth, "Content-Type": "application/json"},
            )
            if tweet_resp.status_code in (200, 201):
                tweet_id = tweet_resp.json().get("data", {}).get("id")
                log.info(f"X tweet posted: {tweet_id}")
                return tweet_id
            else:
                log.error(f"X tweet failed: {tweet_resp.text[:200]}")
                return None

    except Exception as e:
        log.error(f"X post failed: {e}")
        return None


# ── Main post dispatcher ───────────────────────────────────────────────────────
async def dispatch_post(brief: dict, video_path: str, caption: str) -> dict:
    """
    Main entry point — post to all configured platforms.
    If AUTOPOST=false, send FCM notification and wait for approval.
    If AUTOPOST=true, post immediately.
    """
    brief_id = brief.get("brief_id", "UNKNOWN")
    results = {"brief_id": brief_id, "platforms": {}}

    # Upload video to GCS first
    video_url = await upload_video_to_gcs(video_path, brief_id)
    if not video_url:
        log.error("GCS upload failed — cannot post")
        return {"brief_id": brief_id, "error": "gcs_upload_failed"}

    if not AUTOPOST:
        # Send FCM, store pending state, wait for approval tap
        sent = await send_fcm_notification(brief, video_url, caption)
        r.setex(
            f"sitrep:pending:{brief_id}",
            3600,  # 1 hour to approve
            json.dumps({
                "brief": brief,
                "video_url": video_url,
                "video_path": video_url,
                "caption": caption,
            })
        )
        return {
            "brief_id": brief_id,
            "status": "pending_approval",
            "fcm_sent": sent,
            "approve_url": f"/api/sitrep/approve/{brief_id}",
        }

    # Full auto-post
    import asyncio
    tiktok_id, linkedin_id, x_id = await asyncio.gather(
        post_to_tiktok(video_path, caption, brief_id),
        post_to_linkedin(video_path, caption, brief_id),
        post_to_x(video_path, caption),
    )

    results["platforms"] = {
        "tiktok":   {"id": tiktok_id,   "success": bool(tiktok_id)},
        "linkedin": {"id": linkedin_id, "success": bool(linkedin_id)},
        "x":        {"id": x_id,        "success": bool(x_id)},
    }

    # Mark brief as posted in Redis
    r.setex(f"sitrep:posted:{brief_id}", 86400, json.dumps(results))
    log.info(f"Post complete for {brief_id}: {results['platforms']}")
    return results


async def approve_and_post(brief_id: str) -> dict:
    """
    Called when Vern taps approve on his iPhone.
    Retrieves pending brief and posts immediately.
    """
    import asyncio
    pending_raw = r.get(f"sitrep:pending:{brief_id}")
    if not pending_raw:
        return {"error": "brief_not_found_or_expired"}

    pending = json.loads(pending_raw)
    brief = pending["brief"]
    video_path = pending.get("video_url") or pending.get("video_path")
    caption = pending["caption"]

    # Force AUTOPOST for this call
    tiktok_id, linkedin_id, x_id = await asyncio.gather(
        post_to_tiktok(video_path, caption, brief_id),
        post_to_linkedin(video_path, caption, brief_id),
        post_to_x(video_path, caption),
    )

    r.delete(f"sitrep:pending:{brief_id}")
    results = {
        "brief_id": brief_id,
        "status": "posted",
        "platforms": {
            "tiktok":   {"id": tiktok_id,   "success": bool(tiktok_id)},
            "linkedin": {"id": linkedin_id, "success": bool(linkedin_id)},
            "x":        {"id": x_id,        "success": bool(x_id)},
        }
    }
    r.setex(f"sitrep:posted:{brief_id}", 86400, json.dumps(results))
    log.info(f"Approved post complete: {brief_id}")
    return results


