"""
Recording an item's content without ever destroying work done on an earlier version.

The database function ingest_item_version (migrations/006_item_versions.sql) decides, under
a lock on (project, index), whether an upload is new, a repeat, a change to an item nobody
has started, or a change to one with work on it, in which case the old item is kept and
marked superseded. This module supplies what it compares: a hash of the content that is
stable across uploads. A signed URL is not: it carries a fresh token every time, so it is
left out, and the image is identified by the hash of its own bytes instead.
"""
import hashlib
import json

SUPERSEDED = "superseded"

# Content keys that change on every upload without the content itself changing.
_VOLATILE_KEYS = ("image",)


def content_hash(content: dict) -> str:
    stable = {k: v for k, v in (content or {}).items()
              if not (k in _VOLATILE_KEYS and "_image_sha256" in content)}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, default=str).encode()).hexdigest()


class IngestUnavailable(RuntimeError):
    """The database does not have migration 006 yet."""


def record(db, project_id: str, idx: int, content: dict) -> dict:
    """Record content at an index. Returns {outcome, item_id, task_no, previous_item_id}."""
    try:
        rows = db.rpc("ingest_item_version", {
            "p_project": project_id, "p_idx": int(idx), "p_content": content,
            "p_content_hash": content_hash(content),
        }).execute().data
    except Exception as exc:
        text = str(exc)
        if "ingest_item_version" in text and ("PGRST202" in text or "does not exist" in text):
            raise IngestUnavailable("migration 006 has not been applied") from exc
        raise
    row = (rows or [{}])[0]
    return {"outcome": row.get("outcome"), "item_id": row.get("item_id"),
            "task_no": row.get("task_no"), "previous_item_id": row.get("previous_item_id")}


def parse_ts(stamp):
    """An aware datetime from a timestamp in either form ("…Z", or Postgres's
    "…+00:00", trailing fractional zeros trimmed), or None."""
    import re
    from datetime import datetime, timezone
    if not stamp:
        return None
    s = re.sub(r"\.(\d{1,6})", lambda m: "." + m.group(1).ljust(6, "0"),
               str(stamp).replace("Z", "+00:00"), count=1)
    try:
        t = datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def given_before(annotations: list[dict], cutoff) -> list[dict]:
    """The answers given before a version was superseded. An answer with no readable time
    is left out: it cannot be shown to belong to the old content."""
    limit = parse_ts(cutoff)
    if limit is None:
        return []
    kept = []
    for a in annotations:
        at = parse_ts(a.get("created_at"))
        if at is not None and at <= limit:
            kept.append(a)
    return kept


def current(query):
    """Narrow a project_items query to current items: superseded versions are history."""
    return query.neq("status", SUPERSEDED)
