"""
The operational health check: each failure mode is detected, reported by id and count
only, and turns the endpoint into a 503 when it needs action.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from _fakedb import FakeDB
from app.api.v1 import project as proj
from app.services import ops_health

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
AGO = lambda **kw: (NOW - timedelta(**kw)).isoformat()   # noqa: E731


def _db(**rows):
    base = {"project_submissions": [], "project_items": [], "answer_events": [], "review_items": []}
    base.update(rows)
    return FakeDB(base)


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


def test_before_the_native_store_is_installed_events_are_not_an_alarm():
    class NoEvents(FakeDB):
        def table(self, name):
            if name == "answer_events":
                raise RuntimeError('relation "answer_events" does not exist (42P01)')
            return super().table(name)
    out = ops_health.run(NoEvents({"project_submissions": [], "project_items": [], "review_items": []}), NOW)
    assert out["status"] == "ok"


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
