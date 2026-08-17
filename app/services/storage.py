"""
Private, per-client image storage with signed URLs (Slice #2 — isolation).

Isolation model:
- ONE **private** bucket (never public). Objects are not world-readable.
- Each client's objects live under a per-client path prefix.
- Access is granted only via time-limited **signed URLs**.

De-identification (scope): the storage KEY is a salted hash, so no PHI-bearing
filename ever appears in a path, URL, or export. This covers keys/filenames only —
NOT DICOM headers or burned-in pixel PHI (out of scope until we do DICOM).
"""
import hashlib
import mimetypes
import os

from app.core.config import settings
from app.services.supabase_client import get_client

BUCKET = "client-images"                 # single PRIVATE bucket, per-client prefixes
DEFAULT_SIGNED_URL_TTL = 7 * 24 * 3600   # 7 days — covers a pilot review window


def _db(db=None):
    db = db or get_client()
    if db is None:
        raise RuntimeError("Supabase is not configured.")
    return db


def ensure_bucket(db=None) -> None:
    db = _db(db)
    try:
        db.storage.create_bucket(BUCKET, options={"public": False})
    except Exception:
        pass  # already exists


def client_id_for(email: str) -> str:
    """Stable, de-identified per-client prefix derived from the owner's email."""
    return hashlib.sha256((email or "unknown").strip().lower().encode()).hexdigest()[:16]


def _deid_key(client_id: str, original_name: str) -> str:
    ext = os.path.splitext(original_name)[1].lower() or ".png"
    digest = hashlib.sha256(f"{client_id}/{original_name}".encode()).hexdigest()[:32]
    return f"{client_id}/{digest}{ext}"


def upload_image(client_id: str, local_path: str, db=None) -> str:
    """Upload one image under the client's prefix with a de-identified key.

    Returns the storage path (not a URL). The original filename never leaves this call.
    """
    db = _db(db)
    key = _deid_key(client_id, os.path.basename(local_path))
    ctype = mimetypes.guess_type(local_path)[0] or "image/png"
    with open(local_path, "rb") as f:
        db.storage.from_(BUCKET).upload(key, f.read(), {"content-type": ctype, "upsert": "true"})
    return key


def upload_image_bytes(client_id: str, original_name: str, data: bytes, db=None) -> str:
    """Upload one image from raw bytes (e.g. a browser upload) under the client's prefix with
    a de-identified key. Same isolation/de-id as upload_image; the original name never leaves."""
    db = _db(db)
    key = _deid_key(client_id, original_name or "image.png")
    ctype = mimetypes.guess_type(original_name or "image.png")[0] or "image/png"
    db.storage.from_(BUCKET).upload(key, data, {"content-type": ctype, "upsert": "true"})
    return key


def signed_url(path: str, ttl: int = DEFAULT_SIGNED_URL_TTL, db=None) -> str:
    """A time-limited URL for one object. Does NOT auto-refresh: if a review window
    outlives the TTL, re-run sync to regenerate URLs (see README)."""
    db = _db(db)
    res = db.storage.from_(BUCKET).create_signed_url(path, ttl)
    url = res
    if isinstance(res, dict):
        url = res.get("signedURL") or res.get("signedUrl") or res.get("signed_url")
    if url and url.startswith("/"):
        url = settings.SUPABASE_URL.rstrip("/") + url  # absolutise a relative /storage path
    return url


def public_url(path: str, db=None) -> str:
    """The non-signed URL. For a PRIVATE bucket this returns 403 — used only to prove
    objects are not world-readable (isolation gate test)."""
    db = _db(db)
    return db.storage.from_(BUCKET).get_public_url(path)
