"""
Second reading: authored work is read by a second clinician before it counts.

The reading itself runs on the clinician platform (the workforce app), which is the only
side that knows who is who, and so the only side that can guarantee the reader is not the
author. There, a pool whose config has `review_required: true` works in two phases: one
clinician authors, a DIFFERENT clinician approves, edits-and-approves, or sends it back
with a reason. Drafts stay in its `review_items` table; an answer is recorded only when it
is approved. Author ≠ reader is enforced in that app's serving query and again when a
review is submitted.

This module is the bridge. An authored answer counts as done only once the platform's
`review_items` row for that task says it was approved, and only that approved answer is
taken. Everything else is held:

  approved                   -> done, with {approved, rounds, edited} recorded
  review row not approved    -> `second_reading`: the reading is under way
  no review row at all       -> `needs_adjudication`: the answer was recorded without
                                being read (the pool is not set to review_required).
                                A senior reviewer decides.

Items left in `second_reading` are re-checked by `reconcile`, which runs on a results
poll and before delivery, so nothing waits on an event that already passed.
"""
import logging

logger = logging.getLogger(__name__)

STATUS = "second_reading"

NOT_READ = ("This answer was recorded without a second reading: it was not submitted "
            "through the clinician platform's review flow, or the project's pool is not set "
            "to review_required. A senior reviewer must approve it.")


def enabled(ec: dict | None) -> bool:
    return bool((ec or {}).get("second_reading"))


def _pools(db, project_id: str | None) -> list[dict]:
    """The platform's pools for a project (one per project, enforced there)."""
    if not project_id:
        return []
    return (db.table("pools").select("id").eq("project_id", project_id).execute()).data or []


def review_row(db, project_id: str | None, task_no: int | None) -> dict | None:
    """The platform's review record for one task, or None. The workforce table calls the
    task number ls_task_id, a name kept from before the task store."""
    if not task_no:
        return None
    pools = _pools(db, project_id)
    if not pools:
        return None
    rows = (db.table("review_items").select("*")
            .in_("pool_id", [p["id"] for p in pools]).eq("ls_task_id", task_no)
            .execute()).data or []
    return rows[0] if rows else None


def drafting_started(db, project_id: str | None) -> bool:
    """Whether any clinician has started on this project on the platform. Drafts stay there
    until approved, so nothing reaches us while authors write; the platform's review rows
    are the only sign that work is under way."""
    if not project_id:
        return False
    try:
        pools = _pools(db, project_id)
        if not pools:
            return False
        return bool((db.table("review_items").select("id")
                     .in_("pool_id", [p["id"] for p in pools]).limit(1).execute()).data)
    except Exception:
        return False


def approved_annotations(anns: list[dict], row: dict | None) -> list[dict]:
    """Only the answer the platform approved. Two reviewers racing can briefly leave the
    loser's answer on the task before it is withdrawn; it must not be counted."""
    if not row or row.get("state") != "approved" or not row.get("ls_annotation_id"):
        return anns
    keep = [a for a in anns if a.get("id") == row["ls_annotation_id"]]
    return keep or anns


def decide(db, project_id: str | None, task_no) -> tuple[str, dict | None, dict | None]:
    """(status, second_reading record for the label, review row) for an authored answer.
    The platform records the answer and its approval in one transaction, so there is never
    anything to wait for."""
    row = review_row(db, project_id, task_no)
    if row and row.get("state") == "approved":
        return "done", {
            "approved": True,
            "rounds": int(row.get("revision") or 0) + 1,
            "edited": row.get("review_action") == "edited",
            "at": row.get("reviewed_at"),
        }, row
    if row is None:
        return "needs_adjudication", {"approved": False, "reason": NOT_READ}, None
    return STATUS, None, row                        # review is under way on the platform


def reconcile(db, project_id: str, ec: dict) -> int:
    """Re-check items waiting on a second reading. Returns how many settled.

    Re-reads each task and re-applies it through the normal path, so the label is rebuilt
    from the approved answer alone."""
    if not enabled(ec):
        return 0
    waiting = (db.table("project_items").select("id,label")
               .eq("project_id", project_id).eq("status", STATUS).execute()).data or []
    if not waiting:
        return 0
    # lazy: scoring imports this module
    from app.services.scoring import apply_task_annotations, qa_from_ec
    from app.services import tasks
    target, adjudicate, text_fields, primary = qa_from_ec(ec)
    settled = 0
    for it in waiting:
        task_no = (it.get("label") or {}).get("_task_no")
        if not task_no:
            continue
        try:
            task = tasks.native_task_by_number(db, task_no)
            if task is None:
                continue
            status = apply_task_annotations(db, task, target, project_id, adjudicate,
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
