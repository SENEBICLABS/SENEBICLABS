"""
Customer-portal magic-link tokens.

A token is a stateless, HMAC-signed proof that the holder controls an email
address. We email a link containing the token; clicking it lets the company view
their project(s). No passwords, no session table — the signature and expiry are
self-contained.

Format:  <base64url(payload)>.<base64url(hmac_sha256(payload))>
Payload: {"email": "<lowercased>", "exp": <unix seconds>}
"""

import base64
import hashlib
import hmac
import json
import time

from app.core.config import settings

DEFAULT_TTL_SECONDS = 14 * 24 * 3600  # 14 days


def _secret() -> bytes:
    s = settings.PORTAL_SECRET or settings.SUPABASE_SERVICE_KEY or "dev-portal-secret"
    return s.encode()


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(body: str) -> str:
    return _b64e(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())


def make_token(email: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    payload = {"email": email.lower(), "exp": int(time.time()) + ttl_seconds}
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{_sign(body)}"


def make_api_key(email: str) -> str:
    """A long-lived, programmatic key for API clients (10 years). Same signed
    format as a portal token, so verify_token validates it; the long expiry is
    what makes it usable from code instead of a 14-day magic link."""
    return make_token(email, ttl_seconds=10 * 365 * 24 * 3600)


def verify_token(token: str) -> str | None:
    """Return the email if the token is valid and unexpired, else None."""
    try:
        body, sig = token.split(".", 1)
        if not hmac.compare_digest(sig, _sign(body)):
            return None
        payload = json.loads(_b64d(body))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload.get("email")
    except Exception:
        return None
