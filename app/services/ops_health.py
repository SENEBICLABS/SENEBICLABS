"""
Is anything stuck, failing or waiting on a person? One read-only pass over the database,
for an operator or a monitor to act on.

Every check reports a status (ok, warn, alert), a count and the ids of what needs a look:
project ids, task numbers, event ids. Never content, never a clinician's name, never a
client's URL or secret. GET /project/admin/ops-health serves it and answers 503 when any
check is in alert, so a plain uptime monitor can raise the alarm without parsing anything.

What each check means for an operator is written in docs/operations.md.
"""
import time
from datetime import datetime, timedelta, timezone

from app.services.paging import fetch_all

# How long things may wait before someone should look.
EVENT_PENDING_ALERT = timedelta(minutes=10)
REVIEW_WAITING_WARN = timedelta(hours=24)
ADJUDICATION_WAITING_WARN = timedelta(hours=24)
SIGN_OFF_WAITING_WARN = timedelta(hours=48)
DB_SLOW_WARN_MS = 2000
MAX_IDS = 20


def _parse(stamp) -> datetime | None:
    if not stamp:
        return None
    try:
        t = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _check(name, status, count, ids=(), note=""):
    return {"name": name, "status": status, "count": count,
            "ids": [str(i) for i in list(ids)[:MAX_IDS]], "note": note}


def _missing_table(exc: Exception) -> bool:
    text = str(exc)
    return "42P01" in text or "PGRST205" in text or "does not exist" in text


def answer_events(db, now):
    try:
        rows = fetch_all(lambda: db.table("answer_events")
                         .select("id,task_no,project_id,status,attempts,created_at,locked_at")
                         .in_("status", ["pending", "processing", "failed"]).order("id"))
    except Exception as exc:
        if _missing_table(exc):
            return [_check("scoring_events", "ok", 0, note="native task store not installed")]
        raise
    old = [r for r in rows if r["status"] == "pending" and (_parse(r.get("created_at")) or now) < now - EVENT_PENDING_ALERT]
    failed = [r for r in rows if r["status"] == "failed"]
    stale = [r for r in rows if r["status"] == "processing"
             and (_parse(r.get("locked_at")) or now) < now - timedelta(minutes=5)]
    retrying = [r for r in rows if r["status"] == "pending" and int(r.get("attempts") or 0) > 0]
    return [
        _check("scoring_events_waiting", "alert" if old else "ok", len(old), [r["id"] for r in old],
               "Answers recorded but not scored for over 10 minutes. Is the scheduled drain running? "
               "POST /project/internal/process-answers"),
        _check("scoring_events_failed", "alert" if failed else "ok", len(failed), [r["id"] for r in failed],
               "Scoring gave up after its retries. See last_error via GET /project/admin/answer-events/{project}, "
               "fix, then POST /project/admin/answer-events/retry"),
        _check("scoring_events_abandoned", "warn" if stale else "ok", len(stale), [r["id"] for r in stale],
               "A worker died mid-scoring; the next drain reclaims these automatically"),
        _check("scoring_events_retrying", "warn" if retrying else "ok", len(retrying), [r["id"] for r in retrying],
               "Scoring failed at least once and will be retried"),
    ]


def review_queues(db, now):
    out = []
    try:
        reviews = fetch_all(lambda: db.table("review_items").select("id,ls_task_id,authored_at")
                            .eq("state", "needs_review").order("id"))
        old = [r for r in reviews if (_parse(r.get("authored_at")) or now) < now - REVIEW_WAITING_WARN]
        out.append(_check("second_readings_waiting", "warn" if old else "ok", len(old),
                          [r["ls_task_id"] for r in old], "Authored work waiting over a day for a second reader"))
    except Exception as exc:
        if not _missing_table(exc):
            raise
    held = fetch_all(lambda: db.table("project_items").select("project_id,task_no,labeled_at")
                     .eq("status", "needs_adjudication").order("id"))
    old = [r for r in held if (_parse(r.get("labeled_at")) or now) < now - ADJUDICATION_WAITING_WARN]
    out.append(_check("adjudication_waiting", "warn" if old else "ok", len(old),
                      sorted({r["project_id"] for r in old}),
                      "Disagreements held over a day for a senior reviewer (project ids)"))
    return out


def deliveries(db, now):
    subs = fetch_all(lambda: db.table("project_submissions").select("id,stage,eval_config").order("id"))
    failing, given_up, awaiting = [], [], []
    for s in subs:
        ec = s.get("eval_config") or {}
        last = ec.get("_webhook_last") or {}
        if s.get("stage") == "delivered" and ec.get("_webhook_url") and last and not last.get("delivered"):
            from app.api.v1.project import DELIVERY_MAX_ROUNDS     # lazy: project imports this module
            status = last.get("status")
            refused = status is not None and 400 <= int(status) < 500
            (given_up if refused or int(last.get("rounds") or 1) >= DELIVERY_MAX_ROUNDS else failing).append(s["id"])
        ready = _parse(ec.get("_ready_for_delivery"))
        if s.get("stage") != "delivered" and ready and ready < now - SIGN_OFF_WAITING_WARN:
            awaiting.append(s["id"])
    return [
        _check("deliveries_failing", "warn" if failing else "ok", len(failing), failing,
               "Client webhook failed; the scheduled run retries it with backoff"),
        _check("deliveries_given_up", "alert" if given_up else "ok", len(given_up), given_up,
               "Client webhook refused or out of retries. Contact the client; they can poll /results or "
               "use POST /project/webhook/redeliver"),
        _check("deliveries_awaiting_sign_off", "warn" if awaiting else "ok", len(awaiting), awaiting,
               "Fully reviewed over two days ago and waiting for an operator to deliver"),
    ]


def database(db):
    started = time.monotonic()
    db.table("project_submissions").select("id").limit(1).execute()
    ms = int((time.monotonic() - started) * 1000)
    return [_check("database_latency_ms", "warn" if ms > DB_SLOW_WARN_MS else "ok", ms,
                   note="One indexed read. Slow or failing reads point at the database plan's limits")]


def run(db, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    checks = []
    for name, fn in (("database", lambda: database(db)),
                     ("scoring_events", lambda: answer_events(db, now)),
                     ("review_queues", lambda: review_queues(db, now)),
                     ("deliveries", lambda: deliveries(db, now))):
        try:
            checks.extend(fn())
        except Exception as exc:
            checks.append(_check(name, "alert", 1, note=f"The check itself failed ({type(exc).__name__})"))
    worst = "alert" if any(c["status"] == "alert" for c in checks) else \
        "warn" if any(c["status"] == "warn" for c in checks) else "ok"
    return {"status": worst, "checked_at": now.isoformat(), "checks": checks}
