"""
Revocable API keys for programmatic clients.

A key is still a signed portal token (so `verify_token` yields the account email),
but we also record a SHA-256 hash of it here. That lets a single key be revoked
without rotating the shared signing secret (which would invalidate everyone).

Self-serve and operator-minted keys are recorded. A key that predates this
registry (or a request made before the table exists) simply isn't found here, and
a missing row means "not revoked" — so nothing breaks and the check fails open on
availability while still enforcing revocation for keys we do track.

The raw key is shown to the client exactly once, at creation; we only ever store
its hash, so a database leak can't reveal a usable key.
"""

import hashlib
import logging

from app.services.portal_tokens import make_api_key

logger = logging.getLogger(__name__)

TABLE = "api_keys"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def record(db, email: str, raw_key: str, label: str | None = None) -> dict | None:
    """Store the hash of an already-minted key. Returns the row, or None if the
    store is unavailable (never raises — key issuance must not fail on this)."""
    try:
        row = {
            "email": email.strip().lower(),
            "key_hash": _hash(raw_key),
            "last4": raw_key[-4:],
            "label": (label or "API key").strip()[:80],
            "revoked": False,
        }
        res = db.table(TABLE).insert(row).execute()
        return res.data[0] if res.data else None
    except Exception as exc:
        logger.warning("api_keys.record skipped: %s", exc)
        return None


def create(db, email: str, label: str | None = None) -> dict:
    """Mint a new key for `email`, record its hash, and return the raw key ONCE."""
    raw = make_api_key(email.strip().lower())
    rec = record(db, email, raw, label)
    return {
        "api_key": raw,
        "id": (rec or {}).get("id"),
        "last4": raw[-4:],
        "label": (rec or {}).get("label") or (label or "API key"),
        "created_at": (rec or {}).get("created_at"),
    }


def list_for(db, email: str) -> list[dict]:
    return (
        db.table(TABLE)
        .select("id,label,last4,created_at,revoked")
        .eq("email", email.strip().lower())
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )


def revoke(db, email: str, key_id: str) -> bool:
    """Revoke one key, scoped to its owner so a token can't revoke someone else's."""
    res = (
        db.table(TABLE)
        .update({"revoked": True})
        .eq("id", key_id)
        .eq("email", email.strip().lower())
        .execute()
    )
    return bool(res.data)


def is_revoked(db, token: str) -> bool:
    """True only if this key is recorded AND revoked. Missing row / no table / db
    down all read as 'not revoked' so live traffic is never blocked by this check."""
    if db is None:
        return False
    try:
        r = db.table(TABLE).select("revoked").eq("key_hash", _hash(token)).limit(1).execute()
        return bool(r.data) and bool(r.data[0].get("revoked"))
    except Exception:
        return False
