"""
AEGIS � Token Helper
Read TikTok token from Firestore with env var fallback.
"""
import os
from google.cloud import firestore

_firestore_client = None

def get_firestore():
    global _firestore_client
    if _firestore_client is None:
        _firestore_client = firestore.Client(project="cybergrid")
    return _firestore_client

def get_tiktok_token() -> str:
    try:
        db = get_firestore()
        doc = db.collection("aegis_config").document("tiktok_tokens").get()
        if doc.exists:
            token = doc.to_dict().get("access_token")
            if token:
                return token
    except Exception as e:
        print(f"[TokenHelper] Firestore read failed, falling back to env: {e}")
    return os.getenv("TIKTOK_ACCESS_TOKEN", "")
