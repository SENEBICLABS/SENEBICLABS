"""
Delivery: decide what happens when every item in a project is done.

Shared by every task store: the Label Studio routes (api/v1/ls.py), the operator routes
(api/v1/project.py) and the native store's scoring (services/tasks.py) all end here.
"""
import logging
from datetime import datetime, timezone

from app.services import items as items_svc
from app.services.paging import fetch_all

logger = logging.getLogger(__name__)


def maybe_auto_deliver(db, project_id: str) -> None:
    """When every item in a project is done, decide delivery. By default a human must sign off:
    the project is marked ready (a `_ready_for_delivery` timestamp) and NOTHING is shipped —
    the operator delivers explicitly via POST /admin/advance {stage: delivered}. Only when
    eval_config.auto_deliver is set does it ship automatically. A single `needs_adjudication`
    item means not every item is done, so this correctly holds until the split is resolved."""
    try:
        sub = db.table("project_submissions").select("stage,eval_config").eq("id", project_id).limit(1).execute()
        if sub.data and sub.data[0].get("stage") == "delivered":
            return
        # Paged: a truncated read would show every status as done when the pages past
        # the first are still pending, and auto-deliver a half-reviewed batch.
        rows = fetch_all(lambda: items_svc.current(db.table("project_items").select("status"))
                         .eq("project_id", project_id))
        if not rows or any(r.get("status") != "done" for r in rows):
            return
        ec = (sub.data[0].get("eval_config") if sub.data else None) or {}
        if not ec.get("auto_deliver"):
            # Human sign-off required (the safe default). Mark ready once; never ship on our own.
            if not ec.get("_ready_for_delivery"):
                ec["_ready_for_delivery"] = datetime.now(timezone.utc).isoformat()
                db.table("project_submissions").update({"eval_config": ec}).eq("id", project_id).execute()
                logger.info("Project %s fully labeled — awaiting human sign-off (auto_deliver off).", project_id)
            return
        # Conditional: only the caller whose update actually moves the project to delivered
        # sends the webhook. Two scoring workers finishing the last items at once would
        # otherwise both see "not delivered" above and both notify the client.
        moved = (db.table("project_submissions").update(
            {"stage": "delivered", "updated_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", project_id).neq("stage", "delivered").execute()).data
        if not moved:
            return
        logger.info("Auto-delivered project %s (all items complete, auto_deliver on)", project_id)
        from app.api.v1.project import _fire_webhook   # lazy: project imports the services
        _fire_webhook(db, project_id)
    except Exception as exc:
        logger.error("Auto-deliver failed for %s: %s", project_id, exc)
