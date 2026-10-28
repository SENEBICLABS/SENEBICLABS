"""
Client delivery is sent once per delivery and retried safely: the project moves to
delivered once however many workers race to it, every retry carries the same event id, a
failure is retried with growing gaps and then stops, and a refusal (4xx) is never repeated.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from _fakedb import FakeDB
from app.services import delivery
from app.api.v1 import project as proj

PID = "proj-d"
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def _db(stage="submitted", **ec):
    return FakeDB({
        "project_submissions": [{"id": PID, "stage": stage, "company": "Co",
                                 "eval_config": {"auto_deliver": True, "_webhook_url": "https://client/hook",
                                                 "_webhook_secret": "s", **ec}}],
        "project_items": [{"id": "i", "project_id": PID, "idx": 0, "status": "done",
                           "content": {}, "label": {"verdict": "Correct"}, "labeled_at": "t"}],
    })


class _Resp:
    def __init__(self, code):
        self.status_code = code


def test_two_finishers_deliver_once():
    db = _db()
    with patch.object(proj, "_fire_webhook") as fired:
        delivery.maybe_auto_deliver(db, PID)
        delivery.maybe_auto_deliver(db, PID)
    assert fired.call_count == 1
    assert db.rows["project_submissions"][0]["stage"] == "delivered"


def test_a_lost_race_sends_nothing():
    """The second worker read 'not delivered', then lost the conditional update."""
    db = _db()
    real_update = db.table

    calls = {"n": 0}

    def table(name):
        q = real_update(name)
        if name == "project_submissions":
            orig = q.execute

            def execute():
                if q.op == "update" and "stage" in (q.payload or {}):
                    calls["n"] += 1
                    db.rows["project_submissions"][0]["stage"] = "delivered"   # the other worker won
                return orig()
            q.execute = execute
        return q
    db.table = table
    with patch.object(proj, "_fire_webhook") as fired:
        delivery.maybe_auto_deliver(db, PID)
    assert calls["n"] == 1 and fired.call_count == 0


def _fire(db, codes):
    sent = []

    def post(url, content, headers, timeout):
        sent.append(dict(headers))
        return _Resp(codes.pop(0))
    with patch("httpx.post", side_effect=post), patch("time.sleep"), \
            patch.object(proj.report_svc, "build_report", return_value=None):
        proj._fire_webhook(db, PID)
    return sent


def test_every_retry_carries_the_same_event_id():
    db = _db(stage="delivered")
    first = _fire(db, [503, 503, 503])
    second = _fire(db, [200])
    ids = {h["X-Senebiclabs-Event-Id"] for h in first + second}
    assert len(ids) == 1
    last = db.rows["project_submissions"][0]["eval_config"]["_webhook_last"]
    assert last["delivered"] is True and last["rounds"] == 2


def test_the_webhook_url_is_not_logged(caplog):
    db = _db(stage="delivered", _webhook_url="https://client/hook?token=SECRET123")
    _fire(db, [200])
    assert "SECRET123" not in caplog.text


def _last(**kw):
    return {"delivered": False, "status": 503, "rounds": 1, "at": (NOW - timedelta(minutes=3)).isoformat(), **kw}


def test_a_failed_delivery_is_retried_after_its_backoff_only():
    assert proj._delivery_due(_last(), NOW) is True                         # round 1 waits 2 minutes
    assert proj._delivery_due(_last(rounds=2), NOW) is False                # round 2 waits 4
    assert proj._delivery_due(_last(rounds=2, at=(NOW - timedelta(minutes=5)).isoformat()), NOW) is True


def test_a_refusal_is_never_repeated_and_retries_stop():
    assert proj._delivery_due(_last(status=410), NOW) is False
    assert proj._delivery_due(_last(rounds=proj.DELIVERY_MAX_ROUNDS,
                                    at=(NOW - timedelta(days=9)).isoformat()), NOW) is False
    assert proj._delivery_due(_last(delivered=True), NOW) is False
    assert proj._delivery_due(_last(status=None), NOW) is True             # connection refused


def test_the_scheduled_pass_resends_only_what_is_due():
    db = FakeDB({"project_submissions": [
        {"id": "due", "stage": "delivered", "eval_config": {"_webhook_url": "u", "_webhook_last": _last()}},
        {"id": "wait", "stage": "delivered", "eval_config": {"_webhook_url": "u", "_webhook_last": _last(rounds=3)}},
        {"id": "ok", "stage": "delivered", "eval_config": {"_webhook_url": "u", "_webhook_last": _last(delivered=True)}},
        {"id": "dead", "stage": "delivered", "eval_config": {"_webhook_url": "u",
                                                             "_webhook_last": _last(rounds=proj.DELIVERY_MAX_ROUNDS)}},
        {"id": "open", "stage": "in_review", "eval_config": {"_webhook_url": "u", "_webhook_last": _last()}},
    ]})
    with patch.object(proj, "_fire_webhook") as fired:
        out = proj.retry_failed_deliveries(db, NOW)
    assert [c.args[1] for c in fired.call_args_list] == ["due"]
    assert out == {"deliveries_retried": 1, "deliveries_given_up": 1}
