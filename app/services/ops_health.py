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

# Leases (migration 009), with the longest legitimate life derived rather than quoted.
#
# `renew_reservation` has three bounds and only two of them bind at the defaults. A renewal
# is refused once the lease has expired, so each one can add at most one lease length to
# the expiry it found: four renewals reach 60 x 5 = 300 minutes and stop there. The
# function's 480 minute absolute cap is never reached, and a threshold set to that figure
# would let a lease run three hours past anything the rules allow before saying so.
# tests/sql/test_item_reservations_sql.py measures both bounds against Postgres.
LEASE_MINUTES = 60                              # reserve_next_item / renew_reservation default
LEASE_MAX_RENEWALS = 4                          # renew_reservation default
LEASE_MAX_LIFE = timedelta(minutes=LEASE_MINUTES * (LEASE_MAX_RENEWALS + 1))
# Clock skew between this process and the database, and the moment between a renewal being
# decided and the row being written. A lease at its exact maximum is legitimate; this is
# only so the alarm does not flutter around the boundary.
LEASE_LIFE_GRACE = timedelta(minutes=15)
LEASE_UNSWEPT_WARN = timedelta(minutes=15)      # expired but still marked active
LEASE_STARVATION_ALERT = timedelta(minutes=90)  # a project blocked on leases this long
# The sweep is scheduled every five minutes (migration 011), so this is four missed ticks:
# late enough that one slow run is not an alarm, early enough to be the first thing that
# says so rather than LEASE_UNSWEPT_WARN five minutes later.
#
# An alert, which makes the endpoint answer 503 and fails the scheduled run. The sweep is
# the only thing keeping `reservations_lapsed_unswept` honest, and a warning here would be
# printed in a log nobody reads rather than sent to anyone.
SWEEP_STALLED_ALERT = timedelta(minutes=20)

# The in-database monitor (migrations 012 and 013), which probes this endpoint every ten
# minutes from inside Postgres and records what it got.
#
# 35 minutes is three missed ticks plus most of a fourth. The extra half tick is not slack
# for its own sake: every result the monitor records is one tick old by design, because
# pg_net cannot return a reply inside the call that sent the request, so the heartbeat
# legitimately lags.
MONITOR_STALLED_ALERT = timedelta(minutes=35)
# How many probes in a row may fail before it stops being bad luck. At ten minutes apart,
# three is half an hour of the endpoint not answering.
MONITOR_FAILURES_ALERT = 3


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
                if (_parse(r.get("created_at")) or now) < now - LEASE_MAX_LIFE - LEASE_LIFE_GRACE
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
               "The renewal count bounds it at five hours, so these point at a write that went "
               "around renew_reservation"),
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


def reservation_sweep(db, now):
    """Is the scheduled lease sweep still running?

    `ops_sweep_state` is written only by `sweep_reservations()`, on the schedule migration
    011 installs. The scoring drain calls `expire_reservations()` directly and deliberately
    does not touch that row: if it did, a drain that happened to run would hide a dead
    schedule from this check.

    An alert when the sweep has never succeeded or has not succeeded in
    SWEEP_STALLED_ALERT. Serving stays correct either way, because a lapsed lease stops
    counting by predicate, so this is not about cases being served wrongly. It is an alert
    because a stalled sweep silently removes the meaning of `reservations_lapsed_unswept`,
    and a warning would be printed in a run log rather than sent to anybody.

    A failed sweep arrives here as staleness rather than as its own state: the failure
    rolls back, which is what preserves the last genuine success, so there is nothing for
    it to write. It shows up when that preserved timestamp ages past the bound, and
    immediately in `cron.job_run_details`.

    Not being able to read the table at all stays a warning, which is the rule everywhere
    else in this module: a check that could not run is reported, never passed, and never
    escalated. It is also the state of a database where 010 has not been applied yet, and a
    release must not be able to make this endpoint answer 503 on its way through.

    Separate from `reservations` above so that an unreadable `item_reservations` cannot take
    this answer with it. Whether the tidy-up is alive is worth knowing precisely when the
    rest of the group could not be read.
    """
    try:
        # One row, looked up by primary key, so this is not paged: `fetch_all` sets its own
        # range and there is nothing here to page through.
        rows = (db.table("ops_sweep_state").select("name,last_run_at,last_expired")
                .eq("name", "reservations").limit(1).execute().data) or []
    except Exception as exc:
        if _missing_object(exc):
            # Same rule as everywhere else here: a check that could not run is reported,
            # never passed.
            return [_check("reservations_sweep_stalled", "warn", 1,
                           note="ops_sweep_state could not be read, so nothing here says whether "
                                "the lease sweep is running. It comes with migration 010")]
        raise

    bound = int(SWEEP_STALLED_ALERT.total_seconds() // 60)
    # `status` and not `return_message`: pg_cron stores the row count of a SELECT, so
    # `return_message` reads "1 row" on every run of `select public.sweep_reservations()`
    # whatever it did. What the sweep did is in `ops_sweep_state`.
    where_to_look = ("Check select jobname, schedule, active from cron.job, then "
                     "select status, start_time from cron.job_run_details order by "
                     "start_time desc limit 5. return_message reads '1 row' for a SELECT "
                     "and says nothing; what each sweep did is in ops_sweep_state")

    if not rows:
        return [_check("reservations_sweep_stalled", "alert", 1,
                       note="No sweep has ever succeeded. The table exists, so migration 010 is "
                            "applied and the schedule from 011 is either missing or has never "
                            f"completed a run. {where_to_look}")]

    last = _parse(rows[0].get("last_run_at"))
    if last is None:
        # The row is there and its timestamp is not readable, so no successful run can be
        # established. That is the same risk as never having run, not a check that failed.
        return [_check("reservations_sweep_stalled", "alert", 1,
                       note="The recorded sweep has no readable timestamp, so no successful sweep "
                            f"could be established. {where_to_look}")]

    if last < now - SWEEP_STALLED_ALERT:
        return [_check("reservations_sweep_stalled", "alert", 1,
                       note=f"The lease sweep last succeeded {int((now - last).total_seconds() // 60)} "
                            f"minutes ago, past the {bound} minute bound. Either the schedule has "
                            "stopped or every run since has failed, which leaves this timestamp "
                            "where it was. Leases will sit expired but still marked active, and "
                            f"reservations_lapsed_unswept follows. {where_to_look}")]

    return [_check("reservations_sweep_stalled", "ok", 0,
                   note=f"The lease sweep succeeded within the last {bound} minutes")]


def ops_monitor(db, now):
    """Is anything actually watching this platform, and is what watches it working?

    Everything else in this module answers "is the platform healthy". This one answers "did
    anybody ask", which until migrations 012 and 013 nothing could. The scheduled GitHub
    workflow had been skipping every run for want of three secrets, and because nothing
    recorded whether the check had run, every alarm in this file was being raised to nobody
    and there was no way to see that from inside the system.

    `ops_monitor_state` is written only by `ops_monitor_tick()`, on the schedule 013
    installs, which calls this very endpoint from inside Postgres. Two checks come out of it:

      ops_monitor_stalled  when did the endpoint last answer the monitor
      ops_monitor_probe    what the recent probes got, when they did not answer

    The circularity is real and is the point. This endpoint reporting on its own monitor is
    useless to the monitor and useful to everything else: an uptime monitor pointed here
    with the key sees a 503 when the in-database schedule dies, and the record in Postgres
    survives the API being down, which is the one failure an external poller cannot
    distinguish from its own network. Each half covers the other's blind spot; neither is
    asked to cover its own.

    Not switched on is a **warning**, not an alert, and not a pass. 012 seeds the row with
    `enabled = false` because switching it on needs two owner actions it deliberately does
    not take, so an alert here would make this endpoint answer 503 about its own unfinished
    installation. A pass would be worse: it would report "ok" for a platform nothing is
    watching, which is the exact condition this check exists to end.

    A probe that reaches the endpoint counts as a success whatever the endpoint said. 503 is
    `ops-health` doing its job; the alert it carries belongs to the check that raised it, not
    to the monitor for having heard it. Counting it as a probe failure would make any alert
    anywhere self-sustaining.
    """
    missing = ("ops_monitor_state could not be read, so nothing here says whether anything "
               "is watching this platform. It comes with migration 012")
    try:
        # One row, looked up by primary key, so this is not paged.
        rows = (db.table("ops_monitor_state")
                .select("name,enabled,last_run_at,last_success_at,last_status_code,"
                        "last_reported_status,last_error,consecutive_failures,runs")
                .eq("name", "ops-health").limit(1).execute().data) or []
    except Exception as exc:
        if _missing_object(exc):
            # Same rule as everywhere else here: a check that could not run is reported,
            # never passed, and never escalated. It is also the state of production between
            # deploying this code and applying 012.
            return [_check("ops_monitor_stalled", "warn", 1, note=missing)]
        raise

    if not rows:
        return [_check("ops_monitor_stalled", "warn", 1,
                       note="The table exists but holds no ops-health row, so the monitor has "
                            "no URL and no secret name to work from. Re-run migration 012, "
                            "which seeds it")]

    row = rows[0]
    if not row.get("enabled"):
        return [_check("ops_monitor_stalled", "warn", 1,
                       note="The in-database monitor is installed but not switched on, so "
                            "nothing inside the platform is reading this endpoint. Enable "
                            "pg_net, put a dedicated operator key in Vault and apply "
                            "migration 013: see docs/operations.md")]

    out = []
    bound = int(MONITOR_STALLED_ALERT.total_seconds() // 60)
    # Same correction as the sweep's above, and for the same reason: the tick returns a
    # description of what it did, pg_cron discards it, and `ops_monitor_state` is the record.
    where_to_look = ("Check select jobname, schedule, active from cron.job where jobname = "
                     "'ops-monitor', then select status, start_time from cron.job_run_details "
                     "order by start_time desc limit 5. return_message reads '1 row' for a "
                     "SELECT and says nothing; what each tick found is in ops_monitor_state")
    last = _parse(row.get("last_success_at"))

    if last is None:
        out.append(_check("ops_monitor_stalled", "alert", 1,
                          note="The monitor is switched on and has never had an answer from this "
                               f"endpoint. {where_to_look}"))
    elif last < now - MONITOR_STALLED_ALERT:
        out.append(_check("ops_monitor_stalled", "alert", 1,
                          note=f"The monitor last got an answer "
                               f"{int((now - last).total_seconds() // 60)} minutes ago, past the "
                               f"{bound} minute bound. Either the schedule has stopped or every "
                               "probe since has failed, so nothing inside the platform is "
                               f"watching it. {where_to_look}"))
    else:
        out.append(_check("ops_monitor_stalled", "ok", 0,
                          note=f"The monitor got an answer within the last {bound} minutes "
                               f"(probe {row.get('runs') or 0})"))

    # What the probes got. Separate from the heartbeat because the two fail differently: a
    # revoked key fails every probe while the schedule runs perfectly, and the remedy is a
    # new key rather than a look at cron.
    fails = int(row.get("consecutive_failures") or 0)
    err = str(row.get("last_error") or "").strip()[:300]
    # How long it has been failing comes from the clock, not from the count.
    #
    # Multiplying the count by the ten minute cadence was wrong, and wrong in the direction
    # that matters: a lost reply makes the tick return without sending a new probe, so those
    # failures arrive one per twenty minutes, and "about 30 minutes" would have been said
    # about an hour. `last_success_at` is what actually knows, and when there has never been
    # a success there is no duration to state rather than one to guess.
    since = _parse(row.get("last_success_at"))
    how_long = (f", and nothing inside the platform has read this endpoint for "
                f"{int((now - since).total_seconds() // 60)} minutes" if since else
                ", and it has never read this endpoint successfully")
    if fails >= MONITOR_FAILURES_ALERT:
        out.append(_check("ops_monitor_probe", "alert", fails,
                          note=f"{fails} probes in a row failed{how_long}. {err}"))
    elif fails:
        out.append(_check("ops_monitor_probe", "warn", fails,
                          note=f"{fails} probe(s) failed since the last success. {err}"))
    else:
        code, said = row.get("last_status_code"), row.get("last_reported_status")
        out.append(_check("ops_monitor_probe", "ok", 0,
                          note=f"The last probe reached this endpoint"
                               + (f" (HTTP {code}" + (f", reporting {said}" if said else "") + ")"
                                  if code else "")
                               + ". Anything it reported belongs to the checks above"))
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
                     ("reservations_sweep_stalled", lambda: reservation_sweep(db, now)),
                     ("ops_monitor", lambda: ops_monitor(db, now)),
                     ("deliveries", lambda: deliveries(db, now))):
        try:
            checks.extend(fn())
        except Exception as exc:
            checks.append(_check(name, "alert", 1, note=f"The check itself failed ({type(exc).__name__})"))
    worst = "alert" if any(c["status"] == "alert" for c in checks) else \
        "warn" if any(c["status"] == "warn" for c in checks) else "ok"
    return {"status": worst, "checked_at": now.isoformat(), "checks": checks}
