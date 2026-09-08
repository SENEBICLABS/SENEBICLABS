"""
Webhook delivery: retry policy and recorded outcome.

The rule that matters is what does NOT get retried. A 5xx or a refused connection means
the client's endpoint blipped, so try again. A 4xx means the client rejected the request
itself — repeating it just delivers the same rejection three times, and for a signed
webhook a 401 usually means a secret mismatch that a retry cannot fix.

Run: PYTHONPATH=. pytest tests/test_webhook_delivery.py
"""
import json
from unittest.mock import patch

import pytest

from app.api.v1 import project as proj


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


class _FakeTable:
    def __init__(self, db, name):
        self.db, self.name, self._filters = db, name, {}

    def select(self, *_a, **_k):
        return self

    def update(self, data):
        self.db.updates.append((self.name, data))
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def limit(self, _n):
        return self

    def order(self, *_a, **_k):
        return self

    def execute(self):
        return type("R", (), {"data": self.db.rows.get(self.name, [])})()


class _FakeDB:
    def __init__(self, rows=None):
        self.rows, self.updates = rows or {}, []

    def table(self, name):
        return _FakeTable(self, name)


EC = {"_webhook_url": "https://client.example/hook", "_webhook_secret": "s3cret"}


def _db():
    return _FakeDB({
        "project_submissions": [{"eval_config": EC, "company": "PearMedica"}],
        "project_items": [{"idx": 0, "content": {"case_id": "C1"}, "label": {"verdict": "Correct"},
                           "labeled_at": "2026-01-01T00:00:00Z"}],
    })


def _last(db):
    """The recorded outcome from the update the code made."""
    for table, data in reversed(db.updates):
        if table == "project_submissions" and "eval_config" in data:
            return data["eval_config"].get("_webhook_last")
    return None


def _run(db, responses):
    """Fire the webhook with a scripted sequence of httpx outcomes."""
    calls = {"n": 0}

    def _post(url, **kw):
        i = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        r = responses[i]
        if isinstance(r, Exception):
            raise r
        return _Resp(r)

    with patch("httpx.post", side_effect=_post), \
         patch("app.services.report.build_report", return_value={"kind": "evaluation"}), \
         patch("time.sleep"):                     # don't actually wait in tests
        proj._fire_webhook(db, "p1")
    return calls["n"]


def test_a_first_time_success_is_not_retried():
    db = _db()
    assert _run(db, [200]) == 1
    last = _last(db)
    assert last["delivered"] is True and last["attempts"] == 1 and last["status"] == 200


def test_a_5xx_is_retried_and_succeeds_on_a_later_attempt():
    db = _db()
    assert _run(db, [503, 502, 200]) == 3
    last = _last(db)
    assert last["delivered"] is True and last["attempts"] == 3


def test_a_connection_error_is_retried():
    db = _db()
    assert _run(db, [ConnectionError("refused"), ConnectionError("refused"), 200]) == 3
    assert _last(db)["delivered"] is True


def test_a_4xx_is_never_retried():
    # The client rejected the request. Repeating it delivers the same rejection.
    db = _db()
    assert _run(db, [401, 200, 200]) == 1
    last = _last(db)
    assert last["delivered"] is False and last["status"] == 401 and last["attempts"] == 1


def test_exhausted_retries_are_recorded_as_undelivered_with_the_error():
    db = _db()
    assert _run(db, [ConnectionError("refused")]) == 3
    last = _last(db)
    assert last["delivered"] is False and last["attempts"] == 3
    assert "ConnectionError" in (last["error"] or "")


def test_persistent_5xx_is_recorded_as_undelivered():
    db = _db()
    assert _run(db, [500]) == 3
    last = _last(db)
    assert last["delivered"] is False and last["status"] == 500


def test_no_webhook_url_means_no_request_and_no_record():
    db = _FakeDB({"project_submissions": [{"eval_config": {}, "company": "X"}]})
    assert _run(db, [200]) == 0
    assert _last(db) is None


def test_the_payload_is_signed_over_the_exact_bytes_sent():
    # The docs tell clients to verify over the raw body. If we signed a different
    # serialisation than we send, every client's verification would fail.
    import hashlib
    import hmac as _hmac
    sent = {}

    def _post(url, content=None, headers=None, **kw):
        sent["body"], sent["headers"] = content, headers
        return _Resp(200)

    db = _db()
    with patch("httpx.post", side_effect=_post), \
         patch("app.services.report.build_report", return_value={"kind": "evaluation"}), \
         patch("time.sleep"):
        proj._fire_webhook(db, "p1")

    expected = "sha256=" + _hmac.new(b"s3cret", sent["body"], hashlib.sha256).hexdigest()
    assert sent["headers"]["X-Senebiclabs-Signature"] == expected
    # And the body really is the JSON the client will parse.
    assert json.loads(sent["body"])["event"] == "results.delivered"
