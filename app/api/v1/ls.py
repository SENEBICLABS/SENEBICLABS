"""
Label Studio sync API.

POST /ls/sync     — (admin) create the LS project if needed and push pending items as tasks
POST /ls/webhook  — (Label Studio) receives annotations and writes them back to project_items
"""

import hmac
import logging

import httpx

from fastapi import APIRouter, HTTPException, Header, Request
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.core.config import settings
from app.services.supabase_client import get_client
from app.services import labelstudio as ls
from app.services import audit
from app.services import second_reading
from app.services import tasks
from app.services import scoring
from app.services import delivery
from app.services.paging import fetch_all

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ls", tags=["Label Studio"])


def _require_admin(x_admin_key: str | None) -> dict:
    """The acting operator (their own key, or the root key), or 403. See services/operators."""
    from app.services import operators
    return operators.require(x_admin_key)


class SyncIn(BaseModel):
    project_id: str
    task_type: str = "eval_rating"


class PullIn(BaseModel):
    project_id: str


# Scoring and delivery are shared with the native task store, so they live in services
# (scoring.py, delivery.py) and nothing outside the Label Studio routes imports this module
# for them. The old names stay here: the routes below, the operator routes and the tests
# use them, and patching them here still reaches every Label Studio route.
_collapse_structured = scoring.collapse_structured
_parse_result = scoring.parse_result
_is_span_value = scoring.is_span_value
_primary_field = scoring.primary_field
_consensus = scoring.consensus
_apply_task_annotations = scoring.apply_task_annotations
_ls_project_of = scoring.ls_project_of
_text_field_names = scoring.text_field_names
_reviewers_target = scoring.reviewers_target
_qa_settings = scoring.qa_settings
_project_ec = scoring.project_ec
_qa_from_ec = scoring.qa_from_ec
_maybe_auto_deliver = delivery.maybe_auto_deliver


NATIVE_PROJECT = ("This project uses Senebiclabs' own task store, not Label Studio. Its items "
                  "are released by POST /project/admin/release and re-scored by "
                  "POST /project/admin/rebuild.")


def _refuse_native(db, project_id: str) -> None:
    if tasks.is_native(db, project_id):
        raise HTTPException(status_code=409, detail=NATIVE_PROJECT)


def push_new_items(db, project_id: str, items: list[dict], task_type: str = "eval_rating") -> dict:
    """Auto-sync: ensure the LS project exists (built from the project's config) and push
    exactly these newly-ingested items, so clinicians can start without an operator click.
    Pushes only the given items, so repeated ingests never duplicate tasks."""
    if not items:
        return {"pushed": 0}
    if tasks.is_native(db, project_id):
        raise ValueError(NATIVE_PROJECT)
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
    _refuse_native(db, body.project_id)

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
    rows = fetch_all(lambda: db.table("project_items").select("id,content")
                     .eq("project_id", body.project_id).eq("status", "pending"))
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
        # Name the actual failure. The old message asserted that LS_URL or LS_TOKEN
        # were unset and that Label Studio was down, and the real cause was a
        # trailing newline on a pasted token. An operator reading a message that
        # confidently blames the wrong thing goes and checks the wrong thing.
        raise HTTPException(
            status_code=502,
            detail=f"Label Studio call failed: {type(exc).__name__}: {exc}",
        )

    # Mark what we pushed as 'queued' (in LS, awaiting review) so the background
    # /sync-pending never re-pushes the same items.
    ids = [r["id"] for r in rows if r.get("id")]
    for i in range(0, len(ids), 100):
        db.table("project_items").update({"status": "queued"}).in_("id", ids[i:i + 100]).execute()

    out = {"ok": True, "ls_project_id": ls_pid, "pushed": pushed}

    # Tasks in Label Studio are not yet work a clinician can reach. The clinician
    # platform serves from `pools`, which this repo only ever reads: a pool is created
    # by an operator over there. Until one points at this Label Studio project, the
    # sync has succeeded and shipped nothing anybody can open.
    #
    # Reporting a bare ok:true here let an operator complete every documented step and
    # believe the work was live. That is survivable with four cases and someone watching;
    # with a bulk client it is the whole job sitting idle while everyone assumes it is
    # being done. So say it plainly instead.
    try:
        pools = (db.table("pools").select("id,name").eq("ls_project_id", int(ls_pid))
                 .execute()).data or []
    except Exception:
        pools = None          # the pools table belongs to the other repo; never fail on it
    if pools is not None:
        out["pools"] = [p.get("name") for p in pools]
        if not pools:
            out["clinicians_can_reach_this"] = False
            out["next_step"] = (
                f"No pool points at Label Studio project {ls_pid}, so no clinician can see "
                "these tasks yet. Create one in the clinician platform's operator console "
                "(/ops) against this project id, then assign clinicians to it."
            )
        else:
            out["clinicians_can_reach_this"] = True

    return out


@router.post("/pull", summary="Pull annotations from LS into the database (admin)")
def ls_pull(body: PullIn, x_admin_key: str | None = Header(default=None)):
    _require_admin(x_admin_key)
    if not ls.is_configured():
        raise HTTPException(status_code=503, detail="Label Studio is not configured.")
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")
    _refuse_native(db, body.project_id)

    sub = db.table("project_submissions").select("id,ls_project_id,eval_config").eq("id", body.project_id).limit(1).execute()
    if not sub.data:
        raise HTTPException(status_code=404, detail="Project not found.")
    ls_pid = sub.data[0].get("ls_project_id")
    if not ls_pid:
        raise HTTPException(status_code=400, detail="This project has not been sent to Label Studio yet.")
    ec0 = sub.data[0].get("eval_config") or {}
    reviewers_target = int(ec0.get("reviewers_per_item") or 1)
    adjudicate = bool(ec0.get("adjudicate"))
    text_fields = _text_field_names(ec0)
    primary = _primary_field(ec0)

    try:
        tasks = ls.export_tasks(ls_pid)
    except httpx.HTTPStatusError as exc:
        logger.error("LS pull failed: %s", exc)
        raise HTTPException(status_code=502, detail=ls.explain_ls_error(exc))
    except Exception as exc:
        logger.error("LS pull failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=f"Label Studio call failed: {type(exc).__name__}: {exc}",
        )

    written = 0
    for t in tasks:
        try:
            if _apply_task_annotations(db, t, reviewers_target, body.project_id, adjudicate,
                                       text_fields, primary, ec0) is not None:
                written += 1
        except Exception as exc:
            logger.error("LS pull item update failed: %s", exc)

    # Anything still waiting on a second reading whose approval has since landed.
    second_reading.reconcile(db, body.project_id, ec0)

    # A manual pull can complete the batch too — respect the sign-off gate (marks ready, or
    # ships only if auto_deliver is on). Held-for-adjudication items keep it from delivering.
    _maybe_auto_deliver(db, body.project_id)
    return {"ok": True, "pulled": written}


@router.post("/webhook", summary="Receive annotations from Label Studio")
async def ls_webhook(req: Request, x_ls_secret: str | None = Header(default=None)):
    # Fail CLOSED. This previously read `if settings.LS_WEBHOOK_SECRET and ...`, so an
    # unset secret skipped the check entirely — and it was unset in production, leaving
    # the endpoint open. The payload is not trusted (annotations are re-fetched from
    # Label Studio with our own token), but an unauthenticated caller could still name
    # arbitrary task ids and force database writes, audit rows and outbound LS calls.
    # A protection that silently disappears when a config value is missing is not a
    # protection, so a missing secret now rejects rather than admits.
    if not settings.LS_WEBHOOK_SECRET:
        logger.error("LS webhook rejected: LS_WEBHOOK_SECRET is not configured.")
        raise HTTPException(status_code=503, detail="Webhook not configured.")
    if not hmac.compare_digest(x_ls_secret or "", settings.LS_WEBHOOK_SECRET):
        raise HTTPException(status_code=403, detail="Bad webhook secret.")
    body = await req.json()
    if body.get("action") not in ("ANNOTATION_CREATED", "ANNOTATION_UPDATED"):
        return {"ok": True}

    ann = body.get("annotation") or {}
    task_ref = ann.get("task")
    task_id = task_ref if isinstance(task_ref, int) else (task_ref or {}).get("id")
    if not task_id:
        return {"ok": True}
    # Everything below is blocking I/O (Label Studio, the database, and a short wait for a
    # second-reading approval). Run it on a worker thread: done inline in this async
    # handler it would stall every other request on the instance while it waited.
    return await run_in_threadpool(_handle_annotated_task, task_id)


def _handle_annotated_task(task_id: int) -> dict:
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

    ec = _project_ec(db, project_id)
    target, adjudicate, text_fields, primary = _qa_from_ec(ec)
    try:
        # The clinician platform publishes an approved answer a moment before it records
        # the approval, so wait briefly for it rather than park the item.
        status = _apply_task_annotations(db, task, target, project_id, adjudicate, text_fields,
                                         primary, ec, sr_wait=6.0)
    except Exception as exc:
        logger.error("LS webhook apply failed: %s", exc)
        return {"ok": False}

    # An LS task reaching its reviewer target frees a slot in the rolling window — top it back
    # up from the backlog so the next batch flows in. Do this whether the item is done or held
    # for adjudication (both mean the task is fully reviewed in Label Studio).
    if status in ("done", "needs_adjudication", second_reading.STATUS) and project_id:
        try:
            from app.api.v1.project import _kick_sync   # lazy: avoid circular import
            _kick_sync(project_id)
        except Exception:
            pass
    # Delivery only when the item is truly done — a disagreement held for adjudication must not
    # count toward "batch complete".
    if status == "done" and project_id:
        _maybe_auto_deliver(db, project_id)
    return {"ok": True}
