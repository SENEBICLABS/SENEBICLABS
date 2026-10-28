"""
The task store: Senebiclabs' own tasks, answers, and the events that get answers scored.

What lives where (schema in migrations/005_native_task_store.sql):

  project_items.task_no   the task's number. An item IS the task; the number exists so
                          the workforce tables keep their integer task columns
  task_answers            the answer of record, as a result array (one entry per field)
  answer_events           written in the same transaction as each answer; drained here

native_task() returns a task as {id, data, annotations}, the shape
scoring.apply_task_annotations parses, votes, holds splits for adjudication and bridges
second reading from. Each answer is attributed to the clinician who gave it.

Every project is on it: project_submissions.task_store is always 'native' (migration
008 enforces it; the workforce app still reads the column).
"""
import logging
import time

from app.services import delivery, scoring
from app.services.paging import fetch_all
from app.services.render import render_content

logger = logging.getLogger(__name__)

NATIVE = "native"

# A processing event older than this is taken to belong to a worker that died.
STALE_SECONDS = 300


def task_ref(label: dict | None):
    """The task number an item's label was built from. Consumers that join to the workforce
    tables (whose column for it is still named ls_task_id) read it through here."""
    return (label or {}).get("_task_no")


# ── releasing tasks to clinicians ────────────────────────────────────────────────

def release(db, project_id: str) -> int:
    """Make every pending item servable. No copy is made and there is no window to
    respect: the workforce app serves straight from project_items. The status filter on the update is
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
    """An item and its answers: {id, data, annotations: [{id, result, created_at,
    completed_by: {email}}]}. Withdrawn answers are left out. Answers come back oldest
    first."""
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
    data = render_content(item.get("content"))
    data["_item_id"] = item.get("id")
    return {
        "id": task_no,
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
