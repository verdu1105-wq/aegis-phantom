#!/usr/bin/env python3
"""
AEGIS PHANTOM — TikTok Follower Scraper v2
Run: python follower_scrape.py
"""
import requests, redis, os, json, time, re
from dotenv import load_dotenv

script_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(script_dir, '.env'))

REDIS_URL = os.getenv("REDIS_URL", "redis://:DTqOikGrN26czTE4b4DWgj5Tkz849aq8@redis-10919.c284.us-east1-2.gce.cloud.redislabs.com:10919")
SESSION_ID = os.getenv("TIKTOK_SESSION_ID", "")
SIGN_KEY = os.getenv("TIKTOK_SIGN_API_KEY", "")
TARGET = "hogboss4"

print(f"Session: {'YES ' + SESSION_ID[:8] + '...' if SESSION_ID else 'MISSING'}")
print(f"Sign Key: {'YES' if SIGN_KEY else 'MISSING'}")

if not SESSION_ID:
    print("No TIKTOK_SESSION_ID"); exit(1)

try:
    r = redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=5)
    r.ping()
    print(f"Redis OK — vault:{r.scard('goons')} whitelist:{r.scard('whitelist')}")
except Exception as e:
    print(f"Redis failed: {e}"); exit(1)

def sign_url(url):
    """Sign URL using EulerStream API"""
    if not SIGN_KEY:
        return url
    try:
        resp = requests.get(
            "https://tiktok-sign.vercel.app/api/sign",
            params={"url": url, "key": SIGN_KEY},
            timeout=10
        )
        if resp.status_code == 200:
            return resp.json().get("signedUrl", url)
    except:
        pass
    return url

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.tiktok.com/",
    "Cookie": f"sessionid={SESSION_ID}; tt_webid_v2=1",
}

def get_user_id():
    print(f"\nLooking up @{TARGET}...")
    url = f"https://www.tiktok.com/api/user/detail/?uniqueId={TARGET}&aid=1988&device_platform=web_pc"
    signed = sign_url(url)
    try:
        resp = requests.get(signed, headers=HEADERS, timeout=10)
        print(f"  Status: {resp.status_code} | Size: {len(resp.content)}")
        if resp.content:
            data = resp.json()
            uid = data.get("userInfo",{}).get("user",{}).get("id","")
            if uid:
                print(f"  User ID: {uid}")
                return uid
    except Exception as e:
        print(f"  Error: {e}")
    
    # Try scraping HTML
    try:
        resp = requests.get(f"https://www.tiktok.com/@{TARGET}", headers=HEADERS, timeout=10)
        match = re.search(r'"authorId":"(\d+)"', resp.text) or re.search(r'"id":"(\d+)".*?"uniqueId":"{TARGET}"', resp.text)
        if match:
            print(f"  User ID from HTML: {match.group(1)}")
            return match.group(1)
    except:
        pass
    return None

def scrape_followers(user_id):
    followers = []
    cursor = 0
    page = 0

    print(f"\nScraping @{TARGET} followers...")
    while page < 200:
        base_url = f"https://www.tiktok.com/api/user/list/?user_id={user_id}&type=1&count=30&minCursor={cursor}&maxCursor=0&aid=1988&device_platform=web_pc"
        signed_url = sign_url(base_url)
        try:
            resp = requests.get(signed_url, headers=HEADERS, timeout=15)
            print(f"  Page {page+1}: {resp.status_code} | {len(resp.content)} bytes")
            if not resp.content:
                print("  Empty response — stopping"); break
            data = resp.json()
            status = data.get("statusCode", 0)
            if status != 0:
                print(f"  TikTok error {status}"); break
            users = data.get("userList", [])
            if not users:
                print(f"  Done — {len(followers)} total"); break
            for u in users:
                un = u.get("user",{}).get("uniqueId","")
                if un: followers.append(un.lower())
            cursor = data.get("minCursor", 0)
            if not data.get("hasMore", False):
                print(f"  All pages complete!"); break
            page += 1
            time.sleep(1.5)
        except json.JSONDecodeError:
            print(f"  Non-JSON response: {resp.text[:100] if resp.text else 'empty'}"); break
        except Exception as e:
            print(f"  Error: {e}"); break
    return followers

def main():
    user_id = get_user_id()
    if not user_id:
        print("\n❌ Could not get user ID")
        print("TikTok API requires signed requests — trying with EulerStream...")
        user_id = "6569595380"  # fallback

    followers = scrape_followers(user_id)

    if followers:
        print(f"\n✅ {len(followers)} followers scraped")
        # Save backup
        with open(os.path.join(script_dir, "followers_backup.txt"), "w") as f:
            f.write("\n".join(followers))
        print(f"💾 Saved to followers_backup.txt")
        # Import to whitelist
        added = sum(1 for u in followers if r.sadd("whitelist", u))
        print(f"✅ {added} added to whitelist")
        print(f"Final whitelist: {r.scard('whitelist')}")
        # Check conflicts
        goons = r.smembers("goons")
        conflicts = [f for f in followers if f in goons]
        if conflicts:
            print(f"\n⚠️ {len(conflicts)} followers also in goon vault:")
            for c in conflicts: print(f"  @{c}")
    else:
        print("\n❌ No followers scraped")
        print("TikTok requires EulerStream signing for this endpoint")
        print("Alternative: Export follower list from TikTok app manually")
        print("Then paste into ADMIN > Follower Whitelist Import on dashboard")

if __name__ == "__main__":
    main()
