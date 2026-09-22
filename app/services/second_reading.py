"""
Second reading: authored work is read by a second clinician before it counts.

The reading itself runs on the clinician platform (the workforce app), which is the only
side that knows who is who, and so the only side that can guarantee the reader is not the
author. There, a pool whose config has `review_required: true` works in two phases: one
clinician authors, a DIFFERENT clinician approves, edits-and-approves, or sends it back
with a reason. Drafts stay in its `review_items` table; Label Studio receives an answer
only when it is approved. Author ≠ reader is enforced in that app's serving query and
again when a review is submitted.

This module is the bridge. When an authored answer reaches us from Label Studio, it counts
as done only once the platform's `review_items` row for that task says it was approved,
and only that approved annotation is taken as the answer. Everything else is held:

  approved                   -> done, with {approved, rounds, edited} recorded
  review row still settling  -> `second_reading` (the platform writes the annotation a
                                moment before it records the approval; see wait below)
  no review row at all       -> `needs_adjudication`: the answer reached Label Studio
                                without being read (annotated directly, or the pool is
                                not set to review_required). A senior reviewer decides.

Items left in `second_reading` are re-checked by `reconcile`, which runs on a results
poll, a pull, and before delivery — so nothing waits on an event that already passed.
"""
import logging
import time

from app.services import labelstudio as ls

logger = logging.getLogger(__name__)

STATUS = "second_reading"

NOT_READ = ("This answer reached Label Studio without a second reading — it was not "
            "submitted through the clinician platform's review flow, or the project's pool "
            "is not set to review_required. A senior reviewer must approve it.")


def enabled(ec: dict | None) -> bool:
    return bool((ec or {}).get("second_reading"))


def review_row(db, ls_project_id: int | None, ls_task_id: int | None) -> dict | None:
    """The platform's review record for one Label Studio task, or None."""
    if not ls_project_id or not ls_task_id:
        return None
    pools = (db.table("pools").select("id").eq("ls_project_id", ls_project_id)
             .execute()).data or []
    if not pools:
        return None
    rows = (db.table("review_items").select("*")
            .in_("pool_id", [p["id"] for p in pools]).eq("ls_task_id", ls_task_id)
            .execute()).data or []
    return rows[0] if rows else None


def drafting_started(db, ls_project_id: int | None) -> bool:
    """Whether any clinician has started on this project on the platform. Drafts stay there
    until approved, so nothing reaches us while authors write; the platform's review rows
    are the only sign that work is under way."""
    if not ls_project_id:
        return False
    try:
        pools = (db.table("pools").select("id").eq("ls_project_id", ls_project_id)
                 .execute()).data or []
        if not pools:
            return False
        return bool((db.table("review_items").select("id")
                     .in_("pool_id", [p["id"] for p in pools]).limit(1).execute()).data)
    except Exception:
        return False


def approved_annotations(anns: list[dict], row: dict | None) -> list[dict]:
    """Only the annotation the platform approved. Two reviewers racing can briefly leave
    the loser's annotation on the task before it is withdrawn; it must not be counted."""
    if not row or row.get("state") != "approved" or not row.get("ls_annotation_id"):
        return anns
    keep = [a for a in anns if a.get("id") == row["ls_annotation_id"]]
    return keep or anns


def decide(db, ls_project_id, ls_task_id, wait_s: float = 0.0) -> tuple[str, dict | None, dict | None]:
    """(status, second_reading record for the label, review row) for an authored answer.

    `wait_s`: the platform publishes the annotation first and records the approval right
    after, so a webhook can arrive in between. The webhook waits briefly for the approval
    rather than parking the item; anything later is caught by `reconcile`."""
    deadline = time.monotonic() + wait_s
    while True:
        row = review_row(db, ls_project_id, ls_task_id)
        if row and row.get("state") == "approved":
            return "done", {
                "approved": True,
                "rounds": int(row.get("revision") or 0) + 1,
                "edited": row.get("review_action") == "edited",
                "at": row.get("reviewed_at"),
            }, row
        if row is None:
            if time.monotonic() >= deadline:
                return "needs_adjudication", {"approved": False, "reason": NOT_READ}, None
        elif time.monotonic() >= deadline:
            return STATUS, None, row                # review is under way on the platform
        time.sleep(0.5)


def reconcile(db, project_id: str, ec: dict) -> int:
    """Re-check items waiting on a second reading. Returns how many settled.

    Re-reads each task from Label Studio and re-applies it through the normal path, so the
    label is rebuilt from the approved annotation alone."""
    if not enabled(ec):
        return 0
    waiting = (db.table("project_items").select("id,label")
               .eq("project_id", project_id).eq("status", STATUS).execute()).data or []
    if not waiting:
        return 0
    from app.api.v1.ls import _apply_task_annotations, _qa_from_ec   # lazy: ls imports us
    target, adjudicate, text_fields, primary = _qa_from_ec(ec)
    settled = 0
    for it in waiting:
        task_id = (it.get("label") or {}).get("_ls_task_id")
        if not task_id:
            continue
        try:
            task = ls.get_task(task_id)
            status = _apply_task_annotations(db, task, target, project_id, adjudicate,
                                             text_fields, primary, ec)
            if status != STATUS:
                settled += 1
        except Exception as exc:
            logger.error("second-reading reconcile failed for item %s: %s", it.get("id"), exc)
    return settled


def summary(items: list[dict]) -> dict | None:
    """Second-reading figures for the report."""
    recs = [((it.get("label") or {}).get("_second_reading"), it.get("status")) for it in items]
    waiting = sum(1 for it in items if it.get("status") == STATUS)
    read = [r for r, _ in recs if isinstance(r, dict)]
    if not read and not waiting:
        return None
    approved = [r for r in read if r.get("approved")]
    return {
        "approved": len(approved),
        "approved_first_reading": sum(1 for r in approved if int(r.get("rounds") or 1) == 1),
        "sent_back_at_least_once": sum(1 for r in approved if int(r.get("rounds") or 1) > 1),
        "edited_by_reader": sum(1 for r in approved if r.get("edited")),
        "approved_by_senior_reviewer": sum(1 for r in approved if r.get("by_senior_reviewer")),
        "awaiting_reading": waiting,
        "not_read": sum(1 for r, st in recs if isinstance(r, dict) and not r.get("approved")
                        and st == "needs_adjudication"),
    }
