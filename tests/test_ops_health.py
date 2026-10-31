"""
The operational health check: each failure mode is detected, reported by id and count
only, and turns the endpoint into a 503 when it needs action.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from _fakedb import FakeDB
from app.api.v1 import project as proj
from app.services import ops_health

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
AGO = lambda **kw: (NOW - timedelta(**kw)).isoformat()   # noqa: E731


def _db(**rows):
    # A healthy system includes a lease sweep that has succeeded recently: the schedule
    # from migration 011 is part of a working platform, so its absence is a fault, not a
    # baseline. Pass ops_sweep_state explicitly to say otherwise.
    #
    # Dated from the real clock rather than from NOW, because these rows are read two ways:
    # `run(db, NOW)` against the fixed clock, and through the endpoint, which uses the real
    # one. A timestamp relative to NOW is days stale by the time the endpoint reads it; one
    # relative to the real clock is simply "recent" to NOW as well.
    base = {"project_submissions": [], "project_items": [], "answer_events": [],
            "review_items": [], "item_reservations": [],
            "ops_sweep_state": [{"name": "reservations", "last_expired": 0, "runs": 7,
                                 "last_run_at": datetime.now(timezone.utc).isoformat()}]}
    health = rows.pop("_reservation_health", [])
    rpcs = {"reservation_health": lambda _params, out=health: out}
    base.update(rows)
    return FakeDB(base, rpcs=rpcs)


def _by_name(out):
    return {c["name"]: c for c in out["checks"]}


def test_a_quiet_system_is_ok():
    out = ops_health.run(_db(), NOW)
    assert out["status"] == "ok" and all(c["status"] == "ok" for c in out["checks"])


def test_unscored_and_failed_answers_raise_an_alert():
    out = ops_health.run(_db(answer_events=[
        {"id": 1, "task_no": 1, "project_id": "p", "status": "pending", "attempts": 0, "created_at": AGO(minutes=30)},
        {"id": 2, "task_no": 2, "project_id": "p", "status": "pending", "attempts": 0, "created_at": AGO(minutes=1)},
        {"id": 3, "task_no": 3, "project_id": "p", "status": "failed", "attempts": 5, "created_at": AGO(hours=1)},
        {"id": 4, "task_no": 4, "project_id": "p", "status": "processing", "attempts": 1,
         "created_at": AGO(hours=1), "locked_at": AGO(minutes=20)},
    ]), NOW)
    c = _by_name(out)
    assert out["status"] == "alert"
    assert (c["scoring_events_waiting"]["status"], c["scoring_events_waiting"]["ids"]) == ("alert", ["1"])
    assert (c["scoring_events_failed"]["status"], c["scoring_events_failed"]["ids"]) == ("alert", ["3"])
    assert c["scoring_events_abandoned"]["ids"] == ["4"]


def test_stuck_queues_and_deliveries_are_reported():
    out = ops_health.run(_db(
        review_items=[{"id": "r", "ls_task_id": 7, "state": "needs_review", "authored_at": AGO(days=2)}],
        project_items=[{"id": "i", "project_id": "p1", "task_no": 9, "status": "needs_adjudication",
                        "labeled_at": AGO(days=3)}],
        project_submissions=[
            {"id": "failing", "stage": "delivered", "eval_config": {
                "_webhook_url": "https://c/h?token=SECRET", "_webhook_secret": "S3",
                "_webhook_last": {"delivered": False, "status": 503, "rounds": 2}}},
            {"id": "refused", "stage": "delivered", "eval_config": {
                "_webhook_url": "u", "_webhook_last": {"delivered": False, "status": 410, "rounds": 1}}},
            {"id": "waiting", "stage": "pilot", "eval_config": {"_ready_for_delivery": AGO(days=3)}},
        ]), NOW)
    c = _by_name(out)
    assert c["second_readings_waiting"]["ids"] == ["7"]
    assert c["adjudication_waiting"]["ids"] == ["p1"]
    assert c["deliveries_failing"]["ids"] == ["failing"] and c["deliveries_failing"]["status"] == "warn"
    assert c["deliveries_given_up"]["ids"] == ["refused"] and out["status"] == "alert"
    assert c["deliveries_awaiting_sign_off"]["ids"] == ["waiting"]
    # Ids and counts only: no client URL, no secret.
    assert "SECRET" not in str(out) and "S3" not in str(out)


def test_a_check_that_could_not_run_is_reported_rather_than_passed():
    """A missing table used to read as "ok", which is the worst of the three answers.

    Before migration 008 an absent answer_events meant "this database is on Label Studio".
    There is no other store now, so the honest report is that the check did not run. It is
    a warning rather than an alert because during a migration the table is briefly absent
    and nobody should be paged for that.
    """
    class NoStore(FakeDB):
        def table(self, name):
            if name in ("answer_events", "item_reservations"):
                raise RuntimeError(f'relation "{name}" does not exist (42P01)')
            return super().table(name)

    # A healthy sweep, so this stays a test about missing tables: a sweep that has never
    # succeeded is an alert, and would otherwise be the thing setting the overall status.
    out = ops_health.run(NoStore({"project_submissions": [], "project_items": [],
                                  "review_items": [],
                                  "ops_sweep_state": [
                                      {"name": "reservations", "last_expired": 0, "runs": 1,
                                       "last_run_at": datetime.now(timezone.utc).isoformat()}]}),
                         NOW)
    assert out["status"] == "warn"
    c = _by_name(out)
    assert c["scoring_events"]["status"] == "warn"
    assert c["reservations"]["status"] == "warn"
    assert all(ch["status"] != "ok" or "could not" not in ch["note"] for ch in out["checks"])


def test_a_check_that_breaks_is_itself_an_alert():
    class Broken(FakeDB):
        def table(self, name):
            if name == "review_items":
                raise RuntimeError("connection reset")
            return super().table(name)
    out = ops_health.run(Broken({"project_submissions": [], "project_items": [], "answer_events": []}), NOW)
    assert out["status"] == "alert"
    assert {"name": "review_queues", "status": "alert"}.items() <= _by_name(out)["review_queues"].items()


def test_the_endpoint_answers_503_when_something_needs_action():
    db = _db(answer_events=[{"id": 3, "task_no": 3, "project_id": "p", "status": "failed", "attempts": 5,
                             "created_at": AGO(hours=1)}])
    with patch.object(proj, "get_client", return_value=db), patch.object(proj, "_require_admin"):
        assert proj.admin_ops_health(x_admin_key="k").status_code == 503
    with patch.object(proj, "get_client", return_value=_db()), patch.object(proj, "_require_admin"):
        assert proj.admin_ops_health(x_admin_key="k").status_code == 200


# ── leases (migration 009) ──────────────────────────────────────────────────────

def test_a_long_review_in_progress_is_never_an_alarm():
    """The thresholds sit clear of what a lease may legitimately do.

    A clinician three hours and three renewals into a hard case is working, not stuck, and
    a monitor that pages for that teaches an operator to ignore it.
    """
    out = ops_health.run(_db(item_reservations=[
        {"id": 1, "task_no": 1_000_000_000, "project_id": "p", "renewals": 3, "status": "active",
         "created_at": AGO(hours=3), "expires_at": (NOW + timedelta(minutes=40)).isoformat()},
    ]), NOW)
    assert out["status"] == "ok", _by_name(out)


def test_leases_past_their_expiry_but_still_active_are_a_warning():
    out = ops_health.run(_db(item_reservations=[
        {"id": 1, "task_no": 1_000_000_001, "project_id": "p", "renewals": 0, "status": "active",
         "created_at": AGO(hours=2), "expires_at": AGO(minutes=45)},
        {"id": 2, "task_no": 1_000_000_002, "project_id": "p", "renewals": 0, "status": "active",
         "created_at": AGO(minutes=20), "expires_at": AGO(minutes=2)},   # inside the grace
    ]), NOW)
    c = _by_name(out)
    assert out["status"] == "warn"
    assert c["reservations_lapsed_unswept"]["status"] == "warn"
    assert c["reservations_lapsed_unswept"]["count"] == 1
    assert c["reservations_lapsed_unswept"]["ids"] == ["1000000001"]
    assert "expire_reservations" in c["reservations_lapsed_unswept"]["note"]


def test_a_lease_that_outlived_its_absolute_bound_is_an_alert():
    """renew_reservation cannot produce this, so it means a write went around it."""
    out = ops_health.run(_db(item_reservations=[
        {"id": 1, "task_no": 1_000_000_003, "project_id": "p", "renewals": 4, "status": "active",
         "created_at": AGO(hours=11), "expires_at": (NOW + timedelta(minutes=30)).isoformat()},
    ]), NOW)
    c = _by_name(out)
    assert out["status"] == "alert"
    assert c["reservations_overlong"]["status"] == "alert"
    assert c["reservations_overlong"]["ids"] == ["1000000003"]


def test_a_pool_whose_every_case_is_held_for_too_long_is_an_alert():
    """The failure nothing else can see: clinicians are told there is no work, and there is.

    Being briefly blocked is ordinary, so the alert needs the blockage to have lasted
    longer than a lease legitimately would before anybody is told about it.
    """
    busy = {"project_id": "busy", "open_items": 4, "servable_items": 0,
            "lease_blocked_items": 4, "oldest_blocking_lease": AGO(minutes=20)}
    stuck = {"project_id": "stuck", "open_items": 4, "servable_items": 0,
             "lease_blocked_items": 4, "oldest_blocking_lease": AGO(hours=5)}
    finished = {"project_id": "finished", "open_items": 0, "servable_items": 0,
                "lease_blocked_items": 0, "oldest_blocking_lease": None}

    out = ops_health.run(_db(_reservation_health=[busy, finished]), NOW)
    assert _by_name(out)["reservations_starving_work"]["status"] == "ok", "a busy pool is not stuck"

    out = ops_health.run(_db(_reservation_health=[busy, stuck, finished]), NOW)
    c = _by_name(out)
    assert out["status"] == "alert"
    assert c["reservations_starving_work"]["ids"] == ["stuck"]
    assert c["reservations_starving_work"]["count"] == 1


def test_a_project_with_work_still_servable_is_not_starving():
    out = ops_health.run(_db(_reservation_health=[
        {"project_id": "p", "open_items": 10, "servable_items": 3,
         "lease_blocked_items": 7, "oldest_blocking_lease": AGO(hours=6)},
    ]), NOW)
    assert _by_name(out)["reservations_starving_work"]["status"] == "ok"


def test_the_starvation_check_reports_itself_when_it_cannot_run():
    """A check that fails must not read as a pass, and must say which kind of failure."""
    class NoFunction(FakeDB):
        def rpc(self, name, params=None):
            raise RuntimeError('function reservation_health() does not exist (42883)')

    out = ops_health.run(NoFunction({"project_submissions": [], "project_items": [],
                                     "review_items": [], "answer_events": [],
                                     "item_reservations": []}), NOW)
    c = _by_name(out)
    assert c["reservations_starving_work"]["status"] == "warn"
    assert "migration 009" in c["reservations_starving_work"]["note"]

    class Broken(FakeDB):
        def rpc(self, name, params=None):
            raise RuntimeError("connection reset by peer")

    out = ops_health.run(Broken({"project_submissions": [], "project_items": [],
                                 "review_items": [], "answer_events": [],
                                 "item_reservations": []}), NOW)
    c = _by_name(out)
    assert out["status"] == "alert"
    assert c["reservations_starving_work"]["status"] == "alert"
    assert "check itself failed" in c["reservations_starving_work"]["note"]


def _lease(**over):
    row = {"id": 1, "task_no": 1_000_000_010, "project_id": "p", "renewals": 4,
           "status": "active", "expires_at": (NOW + timedelta(minutes=10)).isoformat()}
    row.update(over)
    return row


def test_the_longest_legitimate_lease_is_five_hours_not_eight():
    """The bound is derived from the renewal rules, not quoted from the absolute cap.

    `renew_reservation` refuses a lease that has already expired, so each renewal adds at
    most one lease length to the expiry it found: four of them reach 60 x 5 = 300 minutes.
    Its 480 minute cap is never reached at the defaults, and a threshold set there would
    let a lease run three hours past the rules before anyone was told. Both bounds are
    measured against Postgres in tests/sql/test_item_reservations_sql.py.
    """
    assert ops_health.LEASE_MAX_LIFE == timedelta(minutes=300)
    assert ops_health.LEASE_MAX_LIFE == timedelta(
        minutes=ops_health.LEASE_MINUTES * (ops_health.LEASE_MAX_RENEWALS + 1))


def test_a_lease_at_its_contractual_maximum_is_not_an_alarm():
    """Five hours with every renewal spent is the longest one case may legitimately be
    held. A monitor that fires there teaches an operator that the limit is the alarm; the
    alarm is for a lease that has outlived the limit."""
    out = ops_health.run(_db(item_reservations=[_lease(created_at=AGO(hours=5))]), NOW)
    c = _by_name(out)
    assert c["reservations_overlong"]["status"] == "ok", c["reservations_overlong"]
    assert c["reservations_lapsed_unswept"]["status"] == "ok"
    assert out["status"] == "ok"


def test_a_lease_inside_the_grace_past_the_maximum_is_not_an_alarm():
    """The grace covers clock skew and the moment between deciding a renewal and writing
    the row, so the alarm does not flutter around the boundary."""
    out = ops_health.run(_db(item_reservations=[
        _lease(created_at=AGO(hours=5, minutes=10))]), NOW)
    assert _by_name(out)["reservations_overlong"]["status"] == "ok"


def test_a_lease_past_the_maximum_and_its_grace_is_an_alarm():
    """Five and a half hours: beyond anything the renewal rules can carry a lease to."""
    out = ops_health.run(_db(item_reservations=[
        _lease(created_at=AGO(hours=5, minutes=30))]), NOW)
    c = _by_name(out)
    assert c["reservations_overlong"]["status"] == "alert", c["reservations_overlong"]
    assert out["status"] == "alert"
    assert "five hours" in c["reservations_overlong"]["note"]


def test_a_six_hour_lease_was_missed_by_the_eight_hour_figure():
    """The regression this fix is for: six hours is anomalous and used to read as ok."""
    out = ops_health.run(_db(item_reservations=[_lease(created_at=AGO(hours=6))]), NOW)
    assert _by_name(out)["reservations_overlong"]["status"] == "alert"


def test_the_whole_reservation_group_failing_is_an_alert_not_a_gap():
    """A read that breaks for a reason other than a missing table is a real failure."""
    class Broken(FakeDB):
        def table(self, name):
            if name == "item_reservations":
                raise RuntimeError("connection reset by peer")
            return super().table(name)

    out = ops_health.run(Broken({"project_submissions": [], "project_items": [],
                                 "review_items": [], "answer_events": []}), NOW)
    c = _by_name(out)
    assert out["status"] == "alert"
    assert c["reservations"]["status"] == "alert"
    assert "check itself failed" in c["reservations"]["note"]
    # And it is named, so an operator knows which group did not run.
    assert c["reservations"]["count"] == 1


def test_no_check_ever_reports_ok_while_saying_it_could_not_run():
    """The invariant behind all of the above, asserted once over every check."""
    class Hostile(FakeDB):
        def table(self, name):
            if name in ("answer_events", "item_reservations", "review_items"):
                raise RuntimeError(f'relation "{name}" does not exist (42P01)')
            return super().table(name)

        def rpc(self, name, params=None):
            raise RuntimeError("function does not exist (42883)")

    out = ops_health.run(Hostile({"project_submissions": [], "project_items": []}), NOW)
    for check in out["checks"]:
        note = check["note"].lower()
        if "could not" in note or "not checked" in note or "itself failed" in note:
            assert check["status"] != "ok", check
    assert out["status"] in ("warn", "alert")


# ── the lease sweep's own schedule (migrations 010 and 011) ─────────────────────

def _sweep(out):
    return _by_name(out)["reservations_sweep_stalled"]


def test_a_sweep_that_ran_minutes_ago_is_ok():
    out = ops_health.run(_db(), NOW)
    assert _sweep(out)["status"] == "ok" and _sweep(out)["count"] == 0


def test_a_sweep_past_the_twenty_minute_bound_alerts():
    out = ops_health.run(_db(ops_sweep_state=[
        {"name": "reservations", "last_run_at": AGO(minutes=21),
         "last_expired": 0, "runs": 3}]), NOW)
    c = _sweep(out)
    assert c["status"] == "alert" and c["count"] == 1
    assert "21 minutes ago" in c["note"] and "20 minute bound" in c["note"]
    assert "cron.job" in c["note"], "the note must say where to look"
    assert out["status"] == "alert"


def test_a_stalled_sweep_turns_the_endpoint_into_a_503():
    """What makes this an alert rather than a line in a log nobody reads.

    A monitor pointed at this endpoint raises the alarm without parsing the body, and the
    scheduled run fails, which is what reaches a person.
    """
    stale = _db(ops_sweep_state=[{"name": "reservations", "last_run_at": AGO(minutes=45),
                                  "last_expired": 0, "runs": 3}])
    with patch.object(proj, "get_client", return_value=stale), patch.object(proj, "_require_admin"):
        assert proj.admin_ops_health(x_admin_key="k").status_code == 503
    # And a sweep that is running keeps it a 200, so the 503 is about the sweep.
    with patch.object(proj, "get_client", return_value=_db()), patch.object(proj, "_require_admin"):
        assert proj.admin_ops_health(x_admin_key="k").status_code == 200


def test_a_sweep_just_inside_the_bound_is_not_an_alarm():
    """Four missed ticks, not three: the threshold must not flutter."""
    out = ops_health.run(_db(ops_sweep_state=[
        {"name": "reservations", "last_run_at": AGO(minutes=19),
         "last_expired": 2, "runs": 3}]), NOW)
    assert _sweep(out)["status"] == "ok"


def test_a_sweep_that_has_never_succeeded_alerts():
    """The table is there, so 010 is applied and the schedule from 011 is not running."""
    out = ops_health.run(_db(ops_sweep_state=[]), NOW)
    c = _sweep(out)
    assert c["status"] == "alert"
    assert "No sweep has ever succeeded" in c["note"]
    assert "cron.job" in c["note"]
    assert out["status"] == "alert"


def test_a_missing_state_table_warns_rather_than_alerting():
    """Deliberately not an alert, and the reason matters.

    This is the state of production between deploying the code and applying 010. An alert
    would make ops-health answer 503 and fail the scheduled run during a release, about a
    migration that has not been applied yet. The module's rule holds: a check that could
    not run is reported, never passed, and never escalated.
    """
    class NoTable(FakeDB):
        def table(self, name):
            if name == "ops_sweep_state":
                raise RuntimeError(f'relation "{name}" does not exist (42P01)')
            return super().table(name)

    out = ops_health.run(NoTable({"project_submissions": [], "project_items": [],
                                  "answer_events": [], "review_items": [],
                                  "item_reservations": []}),
                         NOW)
    c = _sweep(out)
    assert c["status"] == "warn", "an unapplied migration must not be an alert"
    assert "migration 010" in c["note"]
    assert out["status"] == "warn"


def test_an_unreadable_timestamp_alerts_because_no_success_can_be_shown():
    out = ops_health.run(_db(ops_sweep_state=[
        {"name": "reservations", "last_run_at": "not a date",
         "last_expired": 0, "runs": 1}]), NOW)
    assert _sweep(out)["status"] == "alert"
    assert "no readable timestamp" in _sweep(out)["note"]


def test_a_zero_count_sweep_is_still_a_healthy_sweep():
    """Nothing to retire is the normal case, and must not read as a stall."""
    out = ops_health.run(_db(ops_sweep_state=[
        {"name": "reservations", "last_run_at": AGO(minutes=1),
         "last_expired": 0, "runs": 999}]), NOW)
    assert _sweep(out)["status"] == "ok"


def test_the_severity_follows_the_age_across_the_boundary():
    """The whole transition in one place, so the boundary cannot drift unnoticed."""
    seen = {}
    for minutes in (0, 5, 19, 20, 21, 60, 24 * 60):
        out = ops_health.run(_db(ops_sweep_state=[
            {"name": "reservations", "last_run_at": AGO(minutes=minutes),
             "last_expired": 0, "runs": 1}]), NOW)
        seen[minutes] = _sweep(out)["status"]
    assert seen == {0: "ok", 5: "ok", 19: "ok", 20: "ok",
                    21: "alert", 60: "alert", 24 * 60: "alert"}, seen


def test_a_failed_sweep_is_detected_once_the_preserved_timestamp_goes_stale():
    """How a failing sweep reaches this check.

    A failure rolls back, which is what preserves the last success, so it writes nothing of
    its own (proved against Postgres in tests/sql/test_reservation_sweep_sql.py). It arrives
    here as the preserved timestamp ageing out, which it does whether the schedule stopped
    or every run since has failed.
    """
    healthy = {"name": "reservations", "last_run_at": AGO(minutes=4),
               "last_expired": 3, "runs": 9}
    assert _sweep(ops_health.run(_db(ops_sweep_state=[healthy]), NOW))["status"] == "ok"

    # Runs keep failing: the row never moves, and NOW keeps moving.
    later = NOW + timedelta(minutes=25)
    c = _sweep(ops_health.run(_db(ops_sweep_state=[healthy]), later))
    assert c["status"] == "alert"
    assert "every run since has failed" in c["note"], \
        "the note should name the failing-run case, not only a stopped schedule"


def test_the_sweep_check_breaking_is_an_alert_and_is_named():
    class Broken(FakeDB):
        def table(self, name):
            if name == "ops_sweep_state":
                raise RuntimeError("connection reset by peer")
            return super().table(name)

    out = ops_health.run(Broken({"project_submissions": [], "project_items": [],
                                 "answer_events": [], "review_items": [],
                                 "item_reservations": []}),
                         NOW)
    c = _sweep(out)
    assert c["status"] == "alert" and "check itself failed" in c["note"]


def test_the_drain_must_not_be_the_thing_that_records_a_sweep():
    """A guard on the design, not on behaviour.

    `ops_sweep_state` is trustworthy only because the scoring drain does not write it. If
    the drain ever called `sweep_reservations()`, a drain that happened to run would refresh
    the timestamp and hide a dead schedule from the check above.
    """
    src = (Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "project.py").read_text()
    assert "sweep_reservations" not in src, \
        "the API calls sweep_reservations(), which would mask a stalled schedule"
    assert src.count('db.rpc("expire_reservations"') == 1, \
        "the drain's own fallback sweep should still be exactly one call"
