"""
Label Studio sync API.

POST /ls/sync     — (admin) create the LS project if needed and push pending items as tasks
POST /ls/webhook  — (Label Studio) receives annotations and writes them back to project_items
"""

import logging
from collections import Counter

import httpx

from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel

from app.core.config import settings
from app.services.supabase_client import get_client
from app.services import labelstudio as ls
from app.services import audit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ls", tags=["Label Studio"])


def _require_admin(x_admin_key: str | None) -> None:
    if not settings.ADMIN_API_KEY or x_admin_key != settings.ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Not authorised.")


class SyncIn(BaseModel):
    project_id: str
    task_type: str = "eval_rating"


class PullIn(BaseModel):
    project_id: str


def _collapse_structured(label: dict) -> dict:
    """Collapse a 'structured' field's two Label Studio controls into one object.

    build_label_config renders a structured field (e.g. critical_miss) as a Yes/No flag
    plus a '<name>_finding' picker. Those arrive as two flat sibling keys; here we merge
    them into label[name] = {"present": bool, "finding": <class or None>} so a critical
    miss lands as structured data, not two disconnected keys. Handles an orphan finding
    (finding chosen but flag left blank) by reporting present=None.
    """
    for fk in [k for k in list(label) if k != "_result" and k.endswith("_finding")]:
        base = fk[: -len("_finding")]
        finding = label.pop(fk)
        flag = label.get(base)
        present = (flag == "Yes") if isinstance(flag, str) else flag
        label[base] = {"present": present, "finding": finding}
    return label


def _parse_result(result: list) -> dict:
    """Flatten a Label Studio annotation result into a label dict (any task type)."""
    label: dict = {"_result": result}
    for r in result:
        fn = r.get("from_name")
        v = r.get("value", {}) or {}
        if "rating" in v:
            val = v["rating"]
        elif "choices" in v:
            c = v["choices"]
            val = c[0] if isinstance(c, list) and len(c) == 1 else c
        elif "text" in v:
            t = v["text"]
            val = t[0] if isinstance(t, list) and len(t) == 1 else t
        elif "labels" in v:
            val = {"labels": v.get("labels"), "start": v.get("start"), "end": v.get("end"), "text": v.get("text")}
        else:
            val = v
        if fn in label and fn != "_result":
            if not isinstance(label[fn], list):
                label[fn] = [label[fn]]
            label[fn].append(val)
        else:
            label[fn] = val
    return _collapse_structured(label)


def _is_span_value(x) -> bool:
    """True if a value is an in-text span highlight, or a list of them (start/end + labels,
    not a structured yes/no). Spans are preserved across reviewers, never majority-voted."""
    if isinstance(x, dict):
        return ("start" in x or "labels" in x) and "present" not in x
    if isinstance(x, list):
        return any(isinstance(e, dict) and ("start" in e or "labels" in e) for e in x)
    return False


def _consensus(labels: list[dict]) -> tuple[dict, float, bool]:
    """Combine N reviewer labels into a majority-vote consensus, plus the agreement
    on the primary `verdict` field (fraction of reviewers who chose the top answer)
    and whether they disagreed (no strict majority). Structured dict fields like
    critical_miss are voted on `present` + majority finding."""
    n = len(labels) or 1
    verdicts = [str(l.get("verdict")) for l in labels if l.get("verdict") is not None]
    agreement, disagreed = 0.0, True
    if verdicts:
        _top, top_n = Counter(verdicts).most_common(1)[0]
        agreement = top_n / n
        disagreed = top_n * 2 <= n
    consensus: dict = {}
    keys = {k for l in labels for k in l if not k.startswith("_")}
    for k in keys:
        vals = [l.get(k) for l in labels if l.get(k) is not None]
        if not vals:
            continue
        # In-text spans don't vote — keep the union of every reviewer's highlights.
        if any(_is_span_value(v) for v in vals):
            merged: list = []
            for v in vals:
                merged.extend(v if isinstance(v, list) else [v])
            consensus[k] = merged
            continue
        if all(isinstance(v, dict) for v in vals):
            present = sum(1 for v in vals if v.get("present")) * 2 > n
            findings = [v.get("finding") for v in vals if v.get("present") and v.get("finding")]
            consensus[k] = {"present": present,
                            "finding": (Counter(findings).most_common(1)[0][0] if (present and findings) else None)}
        else:
            by_str = {str(v): v for v in vals}          # keep original value, vote by string
            top_key = Counter(str(v) for v in vals).most_common(1)[0][0]
            consensus[k] = by_str[top_key]
    return consensus, round(agreement, 3), disagreed


def _apply_task_annotations(db, task: dict, reviewers_target: int, project_id: str | None) -> bool | None:
    """Write an LS task's annotations to its item: for one reviewer the label is stored
    as-is; for many, a majority consensus + agreement is stored. The item is `done` only
    once `reviewers_target` reviewers are in. Returns done (bool), or None if skipped.
    Shared by the manual pull and the live webhook so both handle overlap identically."""
    item_id = (task.get("data") or {}).get("_item_id")
    anns = task.get("annotations") or []
    if not item_id or not anns:
        return None
    parsed = []
    for a in anns:
        cb = a.get("completed_by")
        who = cb.get("email") if isinstance(cb, dict) else (a.get("created_username") or "clinician")
        parsed.append({"by": who, "at": a.get("created_at") or a.get("updated_at"),
                       "label": _parse_result(a.get("result", []))})
    if len(parsed) == 1:
        label = parsed[0]["label"]
    else:
        consensus, agreement, disagreed = _consensus([p["label"] for p in parsed])
        label = {
            **consensus,
            "_result": parsed[0]["label"].get("_result"),
            "_reviewers": len(parsed),
            "_agreement": agreement,
            "_disagreed": disagreed,
            "_annotations": [{"by": p["by"], "at": p["at"],
                              "label": {k: v for k, v in p["label"].items() if k != "_result"}}
                             for p in parsed],
        }
    done = len(parsed) >= reviewers_target
    db.table("project_items").update({
        "label": label,
        "status": "done" if done else "in_progress",
        "labeled_by": parsed[-1]["by"],
        "labeled_at": parsed[-1]["at"],
    }).eq("id", item_id).execute()
    audit.record(db, item_id=item_id, project_id=project_id, action=audit.LABEL,
                 actor_id=parsed[-1]["by"], actor_name=parsed[-1]["by"], source="label_studio", value=label)
    return done


def _reviewers_target(db, project_id: str | None) -> int:
    if not project_id:
        return 1
    try:
        sub = db.table("project_submissions").select("eval_config").eq("id", project_id).limit(1).execute()
        return int(((sub.data[0].get("eval_config") if sub.data else None) or {}).get("reviewers_per_item") or 1)
    except Exception:
        return 1


def _maybe_auto_deliver(db, project_id: str) -> None:
    """When every item in a project is done, flip it to `delivered` and fire the client
    webhook, with no operator action. No-op if already delivered or items remain."""
    try:
        sub = db.table("project_submissions").select("stage").eq("id", project_id).limit(1).execute()
        if sub.data and sub.data[0].get("stage") == "delivered":
            return
        rows = db.table("project_items").select("status").eq("project_id", project_id).execute().data or []
        if not rows or any(r.get("status") != "done" for r in rows):
            return
        from datetime import datetime, timezone
        db.table("project_submissions").update(
            {"stage": "delivered", "updated_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", project_id).execute()
        logger.info("Auto-delivered project %s (all items complete)", project_id)
        from app.api.v1.project import _fire_webhook   # lazy: avoid circular import
        _fire_webhook(db, project_id)
    except Exception as exc:
        logger.error("Auto-deliver failed for %s: %s", project_id, exc)


def push_new_items(db, project_id: str, items: list[dict], task_type: str = "eval_rating") -> dict:
    """Auto-sync: ensure the LS project exists (built from the project's config) and push
    exactly these newly-ingested items, so clinicians can start without an operator click.
    Pushes only the given items, so repeated ingests never duplicate tasks."""
    if not items:
        return {"pushed": 0}
    sub = db.table("project_submissions").select("id,company,ls_project_id,eval_config").eq("id", project_id).limit(1).execute()
    if not sub.data:
        raise ValueError("project not found")
    s = sub.data[0]
    eval_config = s.get("eval_config")
    if not eval_config:
        raise ValueError("no eval_config yet")     # unconfigured project: skip, operator syncs later
    label_config = ls.build_label_config(eval_config)
    for k in ls.required_data_keys(eval_config):
        if any(not (r.get("content") or {}).get(k) for r in items):
            raise ValueError(f"items missing '{k}'")
    reviewers = int(eval_config.get("reviewers_per_item") or 1)
    ls_pid = s.get("ls_project_id")
    if not ls_pid:
        title = f"{s.get('company') or 'Senebiclabs project'} — {task_type}"
        ls_pid = ls.create_project(title=title, label_config=label_config, reviewers=reviewers)
        db.table("project_submissions").update({"ls_project_id": ls_pid}).eq("id", project_id).execute()
    else:
        ls.update_project_config(ls_pid, label_config, reviewers=reviewers)
    pushed = ls.push_tasks(ls_pid, items)
    logger.info("Auto-sync pushed %d new items to LS project %s", pushed, ls_pid)
    return {"ls_project_id": ls_pid, "pushed": pushed}


@router.post("/sync", summary="Create LS project + push pending items as tasks (admin)")
def ls_sync(body: SyncIn, x_admin_key: str | None = Header(default=None)):
    _require_admin(x_admin_key)
    if not ls.is_configured():
        raise HTTPException(status_code=503, detail="Label Studio is not configured (set LS_URL and LS_TOKEN).")
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")

    # eval_config may not exist yet (pre-migration) — degrade to the static config.
    try:
        sub = db.table("project_submissions").select("id,company,ls_project_id,eval_config").eq("id", body.project_id).limit(1).execute()
    except Exception:
        sub = db.table("project_submissions").select("id,company,ls_project_id").eq("id", body.project_id).limit(1).execute()
    if not sub.data:
        raise HTTPException(status_code=404, detail="Project not found.")
    s = sub.data[0]
    ls_pid = s.get("ls_project_id")

    # Per-project schema drives the labeling config when present; otherwise fall back
    # to a built-in task-type config (keeps existing projects working).
    eval_config = s.get("eval_config")
    try:
        label_config = ls.build_label_config(eval_config) if eval_config else ls.get_config(body.task_type)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid eval_config: {exc}")

    # Pull the pending items first so we can check them against the config BEFORE
    # touching Label Studio — a mismatch here gives the operator a plain instruction
    # instead of a raw 400 from the import endpoint.
    items = (
        db.table("project_items").select("id,content")
        .eq("project_id", body.project_id).eq("status", "pending").execute()
    )
    rows = items.data or []
    if not rows:
        raise HTTPException(status_code=422, detail="No pending items to send. Add items to this project first.")

    for k in ls.required_data_keys(eval_config):
        n_missing = sum(1 for r in rows if not (r.get("content") or {}).get(k))
        if n_missing:
            hint = (
                " For images, upload them through the client portal so each item gets an image URL — "
                "a plain CSV of filenames can't carry the images."
                if k == "image"
                else f" Add a '{k}' column to your data, or switch to a config that matches it."
            )
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{n_missing} of {len(rows)} item(s) have no '{k}' value, which this task needs.{hint}"
                ),
            )

    reviewers = int((eval_config or {}).get("reviewers_per_item") or 1)   # overlap: N clinicians per item
    try:
        if not ls_pid:
            title = f"{s.get('company') or 'Senebiclabs project'} — {body.task_type}"
            ls_pid = ls.create_project(title=title, label_config=label_config, reviewers=reviewers)
            db.table("project_submissions").update({"ls_project_id": ls_pid}).eq("id", body.project_id).execute()
        else:
            # Keep the live LS project in step with the current config, so edits made
            # in "Set config" after the first sync are actually applied.
            ls.update_project_config(ls_pid, label_config, reviewers=reviewers)
        pushed = ls.push_tasks(ls_pid, rows)
    except httpx.HTTPStatusError as exc:
        logger.error("LS sync failed: %s", exc)
        raise HTTPException(status_code=502, detail=ls.explain_ls_error(exc))
    except Exception as exc:
        logger.error("LS sync failed: %s", exc)
        raise HTTPException(status_code=502, detail="Could not reach Label Studio. Check that it is running and LS_URL / LS_TOKEN are set.")

    # Mark what we pushed as 'queued' (in LS, awaiting review) so the background
    # /sync-pending never re-pushes the same items.
    ids = [r["id"] for r in rows if r.get("id")]
    for i in range(0, len(ids), 100):
        db.table("project_items").update({"status": "queued"}).in_("id", ids[i:i + 100]).execute()

    return {"ok": True, "ls_project_id": ls_pid, "pushed": pushed}


@router.post("/pull", summary="Pull annotations from LS into the database (admin)")
def ls_pull(body: PullIn, x_admin_key: str | None = Header(default=None)):
    _require_admin(x_admin_key)
    if not ls.is_configured():
        raise HTTPException(status_code=503, detail="Label Studio is not configured.")
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")

    sub = db.table("project_submissions").select("id,ls_project_id,eval_config").eq("id", body.project_id).limit(1).execute()
    if not sub.data:
        raise HTTPException(status_code=404, detail="Project not found.")
    ls_pid = sub.data[0].get("ls_project_id")
    if not ls_pid:
        raise HTTPException(status_code=400, detail="This project has not been sent to Label Studio yet.")
    reviewers_target = int((sub.data[0].get("eval_config") or {}).get("reviewers_per_item") or 1)

    try:
        tasks = ls.export_tasks(ls_pid)
    except Exception as exc:
        logger.error("LS pull failed: %s", exc)
        raise HTTPException(status_code=502, detail="Could not reach Label Studio.")

    written = 0
    for t in tasks:
        try:
            if _apply_task_annotations(db, t, reviewers_target, body.project_id) is not None:
                written += 1
        except Exception as exc:
            logger.error("LS pull item update failed: %s", exc)

    return {"ok": True, "pulled": written}


@router.post("/webhook", summary="Receive annotations from Label Studio")
async def ls_webhook(req: Request, x_ls_secret: str | None = Header(default=None)):
    if settings.LS_WEBHOOK_SECRET and x_ls_secret != settings.LS_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Bad webhook secret.")
    body = await req.json()
    if body.get("action") not in ("ANNOTATION_CREATED", "ANNOTATION_UPDATED"):
        return {"ok": True}

    ann = body.get("annotation") or {}
    task_ref = ann.get("task")
    task_id = task_ref if isinstance(task_ref, int) else (task_ref or {}).get("id")
    if not task_id:
        return {"ok": True}

    db = get_client()
    if db is None:
        return {"ok": False}
    # Re-fetch the whole task so we see ALL annotations (multi-reviewer), not just this one.
    try:
        task = ls.get_task(task_id)
    except Exception as exc:
        logger.error("LS webhook task fetch failed: %s", exc)
        return {"ok": False}
    item_id = (task.get("data") or {}).get("_item_id")
    if not item_id:
        return {"ok": True}

    try:
        pr = db.table("project_items").select("project_id").eq("id", item_id).limit(1).execute()
        project_id = pr.data[0]["project_id"] if pr.data else None
    except Exception:
        project_id = None

    try:
        done = _apply_task_annotations(db, task, _reviewers_target(db, project_id), project_id)
    except Exception as exc:
        logger.error("LS webhook apply failed: %s", exc)
        return {"ok": False}

    # A completed task frees a slot in the rolling window — top it back up from the backlog
    # so the next batch of pending items flows into Label Studio. This is the "refills as
    # clinicians finish" half of the backpressure loop.
    if done and project_id:
        try:
            from app.api.v1.project import _kick_sync   # lazy: avoid circular import
            _kick_sync(project_id)
        except Exception:
            pass
        # Auto-deliver: when this item completing means the whole batch is done, publish it.
        _maybe_auto_deliver(db, project_id)
    return {"ok": True}
