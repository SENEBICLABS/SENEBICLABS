"""
Operator keys: one admin credential per person.

A single shared admin key cannot say who adjudicated an item or delivered a project, and
one leak exposes everything with no way to cut off just that person. Each operator now
has their own key, sent in the same X-Admin-Key header:

  - issued once (only a SHA-256 hash is stored, as with client API keys),
  - revocable on its own, taking effect on the next request,
  - named on every admin decision in the audit trail.

The deployment's ADMIN_API_KEY stays as the root key: the API's own background calls use
it, it is the only key that can manage operators, and it is the break-glass credential.
It is recorded in the audit trail as "root", so its use stays visible.
"""
import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timezone

from fastapi import HTTPException

from app.core.config import settings

logger = logging.getLogger(__name__)

PREFIX = "op_"
ROOT = {"id": "root", "name": "root", "root": True}


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def authenticate(db, key: str | None) -> dict | None:
    """The operator a key belongs to, or None. The root key needs no database, so the
    API's own background calls and break-glass access survive a database outage."""
    if not key:
        return None
    if settings.ADMIN_API_KEY and hmac.compare_digest(key, settings.ADMIN_API_KEY):
        return dict(ROOT)
    if not key.startswith(PREFIX) or db is None:
        return None
    try:
        rows = (db.table("operators").select("id,name,email,active")
                .eq("key_hash", _hash(key)).limit(1).execute()).data
    except Exception as exc:
        logger.error("Operator lookup failed: %s", exc)
        return None
    if not rows or not rows[0].get("active"):
        return None
    op = rows[0]
    try:
        db.table("operators").update({"last_used_at": datetime.now(timezone.utc).isoformat()}) \
            .eq("id", op["id"]).execute()
    except Exception:
        pass                                    # a missed timestamp must not block the request
    return {"id": op["id"], "name": op["name"], "email": op.get("email"), "root": False}


def require(x_admin_key: str | None, root_only: bool = False) -> dict:
    """The acting operator for an admin request, or 403."""
    from app.services.supabase_client import get_client
    op = authenticate(get_client(), x_admin_key)
    if not op or (root_only and not op.get("root")):
        raise HTTPException(status_code=403, detail="Not authorised.")
    return op


def create(db, name: str, email: str | None) -> tuple[dict, str]:
    """A new operator and their key. The key is returned here and never again."""
    key = PREFIX + secrets.token_urlsafe(32)
    row = (db.table("operators").insert({"name": name, "email": email, "key_hash": _hash(key),
                                         "active": True}).execute()).data[0]
    return {k: row.get(k) for k in ("id", "name", "email", "active", "created_at")}, key


def revoke(db, operator_id: str) -> bool:
    rows = (db.table("operators").update({"active": False}).eq("id", operator_id).execute()).data
    return bool(rows)


def listing(db) -> list[dict]:
    fields = ("id", "name", "email", "active", "created_at", "last_used_at")
    rows = (db.table("operators").select(",".join(fields))
            .order("created_at").execute()).data or []
    # Projected here as well as in the query: a key hash must never reach a response.
    return [{k: r.get(k) for k in fields} for r in rows]


def actor(op: dict) -> dict:
    """audit.record keyword arguments for an operator."""
    return {"actor_id": op["id"], "actor_name": op["name"]}
