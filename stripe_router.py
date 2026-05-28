"""
SitRep Stripe Webhook Handler
Handles checkout.session.completed → writes subscriber to Redis
Provides /api/sitrep/access/verify for frontend token validation

REQUIRED CLOUD RUN ENV VARS:
  STRIPE_WEBHOOK_SECRET  = whsec_cSaARxKFg5GfmQ2Mc8FoMEt9iqoaq0cm
  STRIPE_SIGNAL_PRICE_ID = price_1TN3zlDGQkU6cKEv3v19ssZs
  STRIPE_BRIEF_PRICE_ID  = price_1TN3zlDGQkU6cKEv9xv2KfHC
  STRIPE_SENTRY_PRICE_ID = price_1TN3zlDGQkU6cKEv0KWW9Kwl
  STRIPE_TOKEN_SALT      = sitrep-phantom-salt-2026
"""

import os, json, secrets, hashlib
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
import hmac, hashlib

stripe_router = APIRouter()

# Env vars — add these to Cloud Run:
# STRIPE_WEBHOOK_SECRET = whsec_xxxx
# STRIPE_SIGNAL_PRICE_ID = price_xxxx
# STRIPE_BRIEF_PRICE_ID  = price_xxxx
# STRIPE_SENTRY_PRICE_ID = price_xxxx

WEBHOOK_SECRET     = os.getenv("STRIPE_WEBHOOK_SECRET", "")
SIGNAL_PRICE_ID    = os.getenv("STRIPE_SIGNAL_PRICE_ID", "")
BRIEF_PRICE_ID     = os.getenv("STRIPE_BRIEF_PRICE_ID", "")
SENTRY_PRICE_ID    = os.getenv("STRIPE_SENTRY_PRICE_ID", "")

# Map Stripe price IDs → tier names
PRICE_TO_TIER = {}

def get_redis():
    """Get redis from main module"""
    try:
        from main import r
        return r
    except:
        return None

def build_price_map():
    global PRICE_TO_TIER
    PRICE_TO_TIER = {}
    if SIGNAL_PRICE_ID: PRICE_TO_TIER[SIGNAL_PRICE_ID] = "signal"
    if BRIEF_PRICE_ID:  PRICE_TO_TIER[BRIEF_PRICE_ID]  = "brief"
    if SENTRY_PRICE_ID: PRICE_TO_TIER[SENTRY_PRICE_ID] = "sentry"

build_price_map()

def verify_stripe_signature(payload: bytes, sig_header: str, secret: str) -> bool:
    """Verify Stripe webhook signature"""
    if not secret:
        return True  # Skip verification if secret not configured yet
    try:
        elements = dict(el.split("=", 1) for el in sig_header.split(","))
        timestamp = elements.get("t", "")
        signatures = [v for k, v in elements.items() if k == "v1"]
        signed_payload = f"{timestamp}.{payload.decode('utf-8')}"
        expected = hmac.new(
            secret.encode("utf-8"),
            signed_payload.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return any(hmac.compare_digest(expected, sig) for sig in signatures)
    except Exception as e:
        print(f"Stripe sig verify error: {e}")
        return False

def generate_access_token(email: str, tier: str) -> str:
    """Generate deterministic but secret access token"""
    salt = os.getenv("STRIPE_TOKEN_SALT", "sitrep-phantom-2026")
    raw = f"{email}:{tier}:{salt}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]

@stripe_router.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events"""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    # Verify signature
    if WEBHOOK_SECRET and not verify_stripe_signature(payload, sig_header, WEBHOOK_SECRET):
        raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        event = json.loads(payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type = event.get("type", "")
    print(f"Stripe event: {event_type}")

    if event_type == "checkout.session.completed":
        session = event["data"]["object"]
        email = session.get("customer_details", {}).get("email", "")
        customer_id = session.get("customer", "")
        subscription_id = session.get("subscription", "")

        # Get price ID from line items if available, else from metadata
        tier = session.get("metadata", {}).get("tier", "")

        # Try to determine tier from price ID
        if not tier:
            price_id = session.get("metadata", {}).get("price_id", "")
            tier = PRICE_TO_TIER.get(price_id, "brief")  # default to brief

        if not email:
            print("Webhook: no email found in session")
            return JSONResponse({"status": "ok", "note": "no email"})

        # Generate access token
        token = generate_access_token(email, tier)

        # Store in Redis
        r = get_redis()
        if r:
            subscriber_data = {
                "email": email,
                "tier": tier,
                "token": token,
                "customer_id": customer_id,
                "subscription_id": subscription_id,
                "created": datetime.utcnow().isoformat(),
                "active": True
            }
            # Store by token (for lookup)
            r.hset(f"sitrep:token:{token}", mapping=subscriber_data)
            # Store by email (for management)
            r.hset(f"sitrep:email:{email.lower()}", mapping=subscriber_data)
            # Add to subscriber set
            r.sadd("sitrep:subscribers", email.lower())
            print(f"Subscriber created: {email} → {tier} → {token}")
        else:
            print(f"Redis unavailable — subscriber not stored: {email}")

        return JSONResponse({
            "status": "ok",
            "tier": tier,
            "token": token
        })

    if event_type in ("customer.subscription.deleted", "customer.subscription.updated"):
        # Handle cancellation / downgrade
        subscription = event["data"]["object"]
        status = subscription.get("status", "")
        customer_id = subscription.get("customer", "")

        r = get_redis()
        if r and status in ("canceled", "unpaid", "past_due"):
            # Find subscriber by customer_id and mark inactive
            # Scan for matching customer
            for key in r.scan_iter("sitrep:token:*"):
                data = r.hgetall(key)
                if data.get("customer_id") == customer_id:
                    r.hset(key, "active", "False")
                    email = data.get("email", "")
                    if email:
                        r.hset(f"sitrep:email:{email.lower()}", "active", "False")
                    print(f"Subscriber deactivated: {customer_id}")
                    break

    if event_type == "invoice.payment_failed":
        invoice = event["data"]["object"]
        customer_id = invoice.get("customer", "")
        attempt_count = invoice.get("attempt_count", 1)

        # Only revoke after 3 failed attempts
        if attempt_count >= 3 and r:
            for key in r.scan_iter("sitrep:token:*"):
                data = r.hgetall(key)
                if data.get("customer_id") == customer_id:
                    r.hset(key, "active", "False")
                    email = data.get("email", "")
                    if email:
                        r.hset(f"sitrep:email:{email.lower()}", "active", "False")
                    print(f"Access revoked (payment failed x{attempt_count}): {customer_id}")
                    break

    return JSONResponse({"status": "ok"})


@stripe_router.get("/api/sitrep/access/verify")
async def verify_access(token: str = ""):
    """
    Frontend calls this to verify a token and get tier.
    Returns: {valid: bool, tier: str, email: str}
    """
    if not token:
        return JSONResponse({"valid": False, "tier": "free"})

    r = get_redis()
    if not r:
        # Redis unavailable — fail open for now
        return JSONResponse({"valid": False, "tier": "free", "error": "store_unavailable"})

    data = r.hgetall(f"sitrep:token:{token}")
    if not data:
        return JSONResponse({"valid": False, "tier": "free"})

    active = data.get("active", "True")
    if active == "False":
        return JSONResponse({"valid": False, "tier": "free", "reason": "cancelled"})

    return JSONResponse({
        "valid": True,
        "tier": data.get("tier", "brief"),
        "email": data.get("email", ""),
        "created": data.get("created", "")
    })

@stripe_router.post("/api/stripe/checkout")
async def create_checkout(request: Request):
    """Create a Stripe checkout session for a subscription tier"""
    try:
        import stripe
        data = await request.json()
        tier = data.get("tier", "brief")
        email = data.get("email", "")
        success_url = data.get("success_url", "https://sitrep.media/success")
        cancel_url = data.get("cancel_url", "https://sitrep.media/subscribe")

        price_map = {
            "signal": SIGNAL_PRICE_ID,
            "brief": BRIEF_PRICE_ID,
            "sentry": SENTRY_PRICE_ID,
        }
        price_id = price_map.get(tier.lower(), BRIEF_PRICE_ID)
        if not price_id:
            return JSONResponse({"error": f"No price ID for tier: {tier}"}, status_code=400)

        stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
        session_params = {
            "payment_method_types": ["card"],
            "line_items": [{"price": price_id, "quantity": 1}],
            "mode": "subscription",
            "success_url": success_url + "?session_id={CHECKOUT_SESSION_ID}",
            "cancel_url": cancel_url,
        }
        if email:
            session_params["customer_email"] = email

        session = stripe.checkout.Session.create(**session_params)
        return JSONResponse({"url": session.url, "session_id": session.id})
    except Exception as e:
        import traceback
        print(f"[Stripe] Checkout error: {e}\n{traceback.format_exc()}")
        return JSONResponse({"error": str(e)}, status_code=500)


@stripe_router.get("/api/sitrep/subscribers")
async def list_subscribers(request: Request):
    """Admin endpoint — list all subscribers"""
    # Simple admin key check
    admin_key = request.headers.get("x-admin-key", "")
    expected = os.getenv("ADMIN_KEY", "")
    if not expected or admin_key != expected:
        raise HTTPException(status_code=403, detail="Forbidden")

    r = get_redis()
    if not r:
        return {"subscribers": [], "count": 0}

    emails = r.smembers("sitrep:subscribers")
    subscribers = []
    for email in emails:
        data = r.hgetall(f"sitrep:email:{email}")
        if data:
            # Don't expose token
            subscribers.append({
                "email": data.get("email"),
                "tier": data.get("tier"),
                "created": data.get("created"),
                "active": data.get("active")
            })

    return {"subscribers": subscribers, "count": len(subscribers)}
