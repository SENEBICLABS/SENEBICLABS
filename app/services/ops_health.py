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

# Leases (migration 009). A case is leased for an hour when it is served and may be
# renewed up to four times, so eight hours is the longest a lease can legitimately live.
# These thresholds sit clear of that, so a clinician taking a long time over a hard case
# is never an alarm; only a lease that has outlived the rules is.
LEASE_MAX_LIFE = timedelta(hours=8)
LEASE_UNSWEPT_WARN = timedelta(minutes=15)      # expired but still marked active
LEASE_STARVATION_ALERT = timedelta(minutes=90)  # a project blocked on leases this long


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


def _missing_object(exc: Exception) -> bool:
    """A table or function this database does not have. Matches PostgREST and psycopg."""
    text = str(exc)
    return "42P01" in text or "42883" in text or "PGRST205" in text \
        or "PGRST202" in text or "does not exist" in text


def answer_events(db, now):
    try:
        rows = fetch_all(lambda: db.table("answer_events")
                         .select("id,task_no,project_id,status,attempts,created_at,locked_at")
                         .in_("status", ["pending", "processing", "failed"]).order("id"))
    except Exception as exc:
        if _missing_object(exc):
            # Before migration 008 this meant "not installed yet". There is no other store
            # now, so it means the store is gone, and a check that could not run must
            # never read as a pass.
            # Not "ok": this check did not run. Not an alert either, because during a
            # migration the table is legitimately absent for a few minutes.
            return [_check("scoring_events", "warn", 1,
                           note="answer_events could not be read, so nothing here was checked. "
                                "If migration 005 is applied, no answer can be scored")]
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
        if not _missing_object(exc):
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


def reservations(db, now):
    """Leases: swept, bounded, and not starving a pool of work.

    None of these affects whether serving is correct. A lapsed lease stops counting by
    predicate, so the platform recovers with or without the sweep. They are here because
    each one means something an operator can act on, and because a pool that looks empty
    to every clinician while it still holds work is invisible from any other angle.
    """
    try:
        live = fetch_all(lambda: db.table("item_reservations")
                         .select("id,task_no,project_id,created_at,expires_at,renewals")
                         .eq("status", "active").order("id"))
    except Exception as exc:
        if _missing_object(exc):
            # Same rule: a check that could not run is reported, not passed.
            return [_check("reservations", "warn", 1,
                           note="item_reservations could not be read, so nothing here was checked. "
                                "Until migration 009 is applied, cases are served without being "
                                "held and two clinicians can be given the same one")]
        raise

    unswept = [r for r in live
               if (_parse(r.get("expires_at")) or now) < now - LEASE_UNSWEPT_WARN]
    overlong = [r for r in live
                if (_parse(r.get("created_at")) or now) < now - LEASE_MAX_LIFE
                and (_parse(r.get("expires_at")) or now) > now]

    out = [
        _check("reservations_lapsed_unswept", "warn" if unswept else "ok", len(unswept),
               [r["task_no"] for r in unswept],
               "Leases past their expiry still marked active. Serving is unaffected, because an "
               "expired lease stops counting by itself; this says the tidy-up is not running. "
               "Run select expire_reservations()"),
        _check("reservations_overlong", "alert" if overlong else "ok", len(overlong),
               [r["task_no"] for r in overlong],
               "A lease has been alive longer than any review may run and has not been retired. "
               "renew_reservation bounds this, so these point at a write that went around it"),
    ]

    # Work that exists and cannot be served: every open case in the project is at its
    # ceiling, and a live lease rather than a recorded answer is why.
    try:
        rows = db.rpc("reservation_health", {}).execute().data or []
    except Exception as exc:
        if _missing_object(exc):
            return out + [_check("reservations_starving_work", "warn", 0,
                                 note="reservation_health is not in this database, so starvation "
                                      "was not checked. It comes with migration 009")]
        return out + [_check("reservations_starving_work", "alert", 1,
                             note=f"The starvation check itself failed ({type(exc).__name__})")]

    starved = []
    for r in rows:
        if int(r.get("servable_items") or 0) > 0 or int(r.get("lease_blocked_items") or 0) == 0:
            continue
        since = _parse(r.get("oldest_blocking_lease"))
        if since and since < now - LEASE_STARVATION_ALERT:
            starved.append(r["project_id"])

    out.append(_check("reservations_starving_work", "alert" if starved else "ok", len(starved),
                      starved,
                      "Every open case in these projects is held, and has been for over an hour "
                      "and a half, so clinicians are told there is no work while the work is "
                      "there (project ids). Check for abandoned sessions, then "
                      "select expire_reservations()"))
    return out


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
                     ("reservations", lambda: reservations(db, now)),
                     ("deliveries", lambda: deliveries(db, now))):
        try:
            checks.extend(fn())
        except Exception as exc:
            checks.append(_check(name, "alert", 1, note=f"The check itself failed ({type(exc).__name__})"))
    worst = "alert" if any(c["status"] == "alert" for c in checks) else \
        "warn" if any(c["status"] == "warn" for c in checks) else "ok"
    return {"status": worst, "checked_at": now.isoformat(), "checks": checks}
