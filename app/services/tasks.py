"""
The native task store: Senebiclabs' own tasks, answers, and the events that get answers
scored. It replaces Label Studio, which held a copy of each item, the clinicians' answers
and a per-task count, and told us by webhook when an answer arrived.

What lives where (schema in migrations/005_native_task_store.sql):

  project_items.task_no   the task's number. An item IS the task; the number exists so
                          the workforce tables keep their integer task columns
  task_answers            the answer of record, in the Label Studio result-array format
  answer_events           written in the same transaction as each answer; drained here

A project is on one store at a time (project_submissions.task_store). A native project
never calls Label Studio, and nothing here falls back to it.

The scoring path is unchanged. native_task() returns a task in the same shape as Label
Studio's GET /api/tasks/{id}, so scoring.apply_task_annotations parses, votes, holds splits for
adjudication and bridges second reading exactly as it always has. The one thing that
changes is who an answer is attributed to: the clinician who gave it, where Label Studio
could only say "the service account".
"""
import logging
import time

from app.services import delivery, scoring
from app.services.paging import fetch_all

logger = logging.getLogger(__name__)

NATIVE = "native"
LABEL_STUDIO = "label_studio"
STORES = (NATIVE, LABEL_STUDIO)

# New projects are created on the native store, and no new Label Studio project is ever
# created. A project that already has one keeps working there until it is retired.
NO_NEW_LABEL_STUDIO = (
    "No Label Studio project exists for this project, and new Label Studio projects "
    "are no longer created. Use the native task store and release items through "
    "POST /project/admin/release."
)

# Native task numbers start here (see the migration); Label Studio's ids sit below it.
NATIVE_TASK_FLOOR = 1_000_000_000

# A processing event older than this is taken to belong to a worker that died.
STALE_SECONDS = 300


def _missing_column(exc: Exception) -> bool:
    text = str(exc)
    return "task_store" in text and ("42703" in text or "does not exist" in text or "column" in text)


def store_of(db, project_id: str | None) -> str:
    """Which store a project uses. Before migration 005 is applied the column does not exist
    and every project is on Label Studio, which is the one case answered without asking.
    Any other failure is raised: guessing 'label_studio' for a native project would send
    its work to the wrong place silently."""
    if not project_id:
        return LABEL_STUDIO
    try:
        rows = (db.table("project_submissions").select("task_store")
                .eq("id", project_id).limit(1).execute()).data
    except Exception as exc:
        if _missing_column(exc):
            return LABEL_STUDIO
        raise
    return ((rows[0].get("task_store") if rows else None) or LABEL_STUDIO)


def is_native(db, project_id: str | None) -> bool:
    return store_of(db, project_id) == NATIVE


def task_ref(label: dict | None):
    """The task an item's label was built from: its native number, or for work done on
    Label Studio, the Label Studio task id. Consumers that join to the workforce tables
    (whose ls_task_id column holds either) read it through here."""
    label = label or {}
    return label.get("_task_no") if label.get("_task_no") is not None else label.get("_ls_task_id")


# ── switching a project's store ──────────────────────────────────────────────────

class StoreChangeRefused(ValueError):
    pass


def set_store(db, project_id: str, store: str) -> None:
    """Move a project between stores, only while that cannot strand any work.

    To native: nothing may have reached Label Studio yet (no LS project, no item past
    'pending'). To Label Studio: no answer may exist in the native store. The second is the
    rollback path for a project switched by mistake before clinicians start."""
    if store not in STORES:
        raise StoreChangeRefused(f"store must be one of: {', '.join(STORES)}")
    sub = (db.table("project_submissions").select("id,ls_project_id")
           .eq("id", project_id).limit(1).execute()).data
    if not sub:
        raise StoreChangeRefused("Project not found.")
    if store == NATIVE:
        if sub[0].get("ls_project_id"):
            raise StoreChangeRefused(
                "This project already has tasks in Label Studio. Move it with the migration "
                "script (scripts/migrate_ls_project.py), which carries its history across.")
        moved = (db.table("project_items").select("id").eq("project_id", project_id)
                 .in_("status", ["queued", "in_progress", "second_reading", "needs_adjudication", "done"])
                 .limit(1).execute()).data
        if moved:
            raise StoreChangeRefused("Items in this project have already been sent for review.")
    else:
        answered = (db.table("task_answers").select("id").eq("project_id", project_id)
                    .limit(1).execute()).data
        if answered:
            raise StoreChangeRefused(
                "Clinicians have already answered on the native store; moving back would "
                "strand their answers.")
    db.table("project_submissions").update({"task_store": store}).eq("id", project_id).execute()


# ── releasing tasks to clinicians ────────────────────────────────────────────────

def release(db, project_id: str) -> int:
    """Make every pending item servable. On the native store this is the whole of what
    'sync' used to mean: no copy is made and there is no window to respect, because the
    workforce app serves straight from project_items. The status filter on the update is
    the lock, so two concurrent releases never double-count."""
    rows = fetch_all(lambda: db.table("project_items").select("id")
                     .eq("project_id", project_id).eq("status", "pending"))
    ids = [r["id"] for r in rows if r.get("id")]
    released = 0
    for i in range(0, len(ids), 100):
        res = (db.table("project_items").update({"status": "queued"})
               .in_("id", ids[i:i + 100]).eq("status", "pending").execute())
        released += len(res.data or [])
    return released


# ── reading a task the way the scoring path expects ──────────────────────────────

def native_task(db, item: dict) -> dict:
    """An item and its answers, shaped like Label Studio's task payload: {id, data,
    annotations: [{id, result, created_at, completed_by: {email}}]}. Withdrawn answers are
    left out. Answers come back oldest first, the order Label Studio returned them in."""
    from app.services import labelstudio as ls

    task_no = item.get("task_no")
    answers = fetch_all(lambda: db.table("task_answers")
                        .select("id,clinician_id,result,created_at,kind")
                        .eq("task_no", task_no).is_("withdrawn_at", "null").order("id"))
    ids = sorted({a["clinician_id"] for a in answers if a.get("clinician_id")})
    emails: dict = {}
    for i in range(0, len(ids), 100):
        for c in (db.table("clinicians").select("id,email")
                  .in_("id", ids[i:i + 100]).execute().data or []):
            emails[c["id"]] = c.get("email")
    content = item.get("content") or {}
    data = {k: ls.render_value(v) for k, v in content.items() if not str(k).startswith("_")}
    data["_item_id"] = item.get("id")
    return {
        "id": task_no,
        "_native": True,
        "data": data,
        "annotations": [{
            "id": a["id"],
            "result": a.get("result") or [],
            "created_at": a.get("created_at"),
            "completed_by": {"email": emails.get(a.get("clinician_id")) or a.get("clinician_id") or "clinician"},
        } for a in answers],
    }


def native_task_by_number(db, task_no) -> dict | None:
    rows = (db.table("project_items").select("id,task_no,project_id,content")
            .eq("task_no", task_no).limit(1).execute()).data
    return native_task(db, rows[0]) if rows else None


# ── scoring answers as they arrive ───────────────────────────────────────────────

def process_task(db, task_no: int, project_id: str) -> str | None:
    """Re-score one task from all of its stored answers. Idempotent: running it twice, or
    after more answers arrive, gives the label the full set of answers implies."""
    rows = (db.table("project_items").select("id,task_no,project_id,content")
            .eq("task_no", task_no).limit(1).execute()).data
    if not rows:
        return None                          # the project was deleted under the event
    ec = scoring.project_ec(db, project_id)
    target, adjudicate, text_fields, primary = scoring.qa_from_ec(ec)
    return scoring.apply_task_annotations(db, native_task(db, rows[0]), target, project_id,
                                          adjudicate, text_fields, primary, ec)


def process_events(db, limit: int = 20, budget_s: float = 20.0) -> dict:
    """Drain the outbox: claim, score, finish, until it is empty or the time budget is
    spent. Safe to run from several places at once (the claim gives each task to one worker)
    and safe to run again after a crash (an abandoned claim goes back to pending)."""
    started = time.monotonic()
    processed = failed = 0
    finished_projects: set = set()
    while time.monotonic() - started < budget_s:
        claimed = (db.rpc("claim_answer_events",
                          {"p_limit": limit, "p_stale_seconds": STALE_SECONDS}).execute().data or [])
        if not claimed:
            break
        for ev in claimed:
            try:
                status = process_task(db, ev["task_no"], ev["project_id"])
                db.rpc("finish_answer_event", {"p_event_id": ev["event_id"], "p_ok": True,
                                               "p_upto_event_id": ev.get("upto_event_id")}).execute()
                processed += 1
                if status == "done":
                    finished_projects.add(ev["project_id"])
            except Exception as exc:
                # The event id and the error's kind only: an exception's text can quote
                # the data it choked on, and that may be clinical.
                logger.error("answer event %s failed (%s)", ev.get("event_id"), type(exc).__name__)
                try:
                    db.rpc("finish_answer_event", {
                        "p_event_id": ev["event_id"], "p_ok": False,
                        "p_upto_event_id": ev.get("upto_event_id"),
                        "p_error": f"{type(exc).__name__}: {exc}"[:500]}).execute()
                except Exception:
                    logger.error("could not record failure of answer event %s", ev.get("event_id"))
                failed += 1
    # Once per project rather than per task: it reads every item's status.
    for project_id in finished_projects:
        delivery.maybe_auto_deliver(db, project_id)
    return {"processed": processed, "failed": failed}


def open_events(db, project_id: str, status: str | None = None) -> list[dict]:
    """Events an operator should see: anything not done, newest first."""
    q = (db.table("answer_events").select("id,task_no,kind,status,attempts,last_error,created_at,processed_at")
         .eq("project_id", project_id))
    if status:
        q = q.eq("status", status)
    else:
        q = q.in_("status", ["pending", "processing", "failed"])
    return q.order("id", desc=True).limit(500).execute().data or []
