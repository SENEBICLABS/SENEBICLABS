"""
Scoring and delivery live in services (services/scoring.py, services/delivery.py). Scoring
answers must not need the API layer at all, and every delivery safeguard must hold
whoever calls it.
"""
import os
import subprocess
import sys
import textwrap
from unittest.mock import patch

from _fakedb import FakeDB

import test_native_tasks as N
from app.api.v1 import project as proj
from app.services import delivery, tasks

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


# ── scoring does not import the API layer ───────────────────────────────────────

def test_scoring_and_delivery_never_load_the_api_layer():
    """In a fresh interpreter: drain native answers, score the item, decide delivery."""
    script = textwrap.dedent("""
        import sys
        sys.path.insert(0, %r)
        from _fakedb import FakeDB
        from app.services import tasks, templates
        ec = templates.config_from_template("model_evaluation", classes=["Normal", "Abnormal"])
        ec["reviewers_per_item"] = 1
        answer = [{"from_name": "verdict", "to_name": "image", "type": "choices",
                   "value": {"choices": ["Correct"]}}]
        db = FakeDB({
            "project_submissions": [{"id": "p", "stage": "submitted", "eval_config": ec,
                                     "task_store": "native"}],
            "project_items": [{"id": "i0", "project_id": "p", "idx": 0, "task_no": 1000000000,
                               "status": "queued", "content": {"prompt": "x", "prediction": "Normal"}}],
            "task_answers": [{"id": 1, "task_no": 1000000000, "project_id": "p", "pool_id": "pool",
                              "clinician_id": "d1", "kind": "judgement", "result": answer,
                              "created_at": "2026-10-25T10:00:00Z", "withdrawn_at": None}],
            "clinicians": [{"id": "d1", "email": "one@x.com"}],
            "answer_events": [], "review_items": [], "task_completions": [], "audit_events": [],
        }, rpcs={
            "claim_answer_events": lambda p: [{"event_id": 1, "task_no": 1000000000,
                                               "project_id": "p", "upto_event_id": 1}] if not claimed.append(1) and len(claimed) == 1 else [],
            "finish_answer_event": lambda p: None,
        })
        claimed = []
        out = tasks.process_events(db)
        item = db.rows["project_items"][0]
        ready = db.rows["project_submissions"][0]["eval_config"].get("_ready_for_delivery")
        print(out["processed"], item["status"], item["label"].get("_task_no"), bool(ready),
              any(m.startswith("app.api") for m in sys.modules))
    """ % HERE)
    r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=ROOT,
                       env={**os.environ, "PYTHONPATH": ROOT}, timeout=60)
    assert r.returncode == 0, r.stderr[-800:]
    processed, status, task_no, ready, any_api_loaded = r.stdout.split()
    assert (processed, status, task_no, ready) == ("1", "done", "1000000000", "True")
    assert any_api_loaded == "False"


# ── processing goes through the services ─────────────────────────────────

def test_native_processing_scores_through_scoring_and_delivers_through_delivery():
    db = N._bound(N._db(answers=N.AGREE))
    N._event(db)
    with patch.object(delivery, "maybe_auto_deliver") as delivered, \
            patch("app.services.audit.record") as audited:
        assert tasks.process_events(db) == {"processed": 1, "failed": 0}
    delivered.assert_called_once_with(db, N.PID)
    label = db.rows["project_items"][0]["label"]
    assert db.rows["project_items"][0]["status"] == "done"
    assert label["_task_no"] == 1_000_000_000 and "_ls_task_id" not in label
    assert label["_reviewers"] == 3 and label["_disagreed"] is False
    assert audited.call_args.kwargs["source"] == "native"


def test_a_native_split_is_held_for_adjudication_and_does_not_deliver():
    db = N._bound(N._db(ec=N._ec(auto_deliver=True), answers=N.SPLIT))
    N._event(db)
    with patch.object(proj, "_fire_webhook") as fired:
        tasks.process_events(db)
    assert db.rows["project_items"][0]["status"] == "needs_adjudication"
    assert db.rows["project_submissions"][0]["stage"] == "submitted"
    fired.assert_not_called()


# ── delivery safeguards ─────────────────────────────────────────────────────────

PID = "proj-s"


def _ddb(statuses, stage="submitted", **ec):
    return FakeDB({
        "project_submissions": [{"id": PID, "stage": stage, "eval_config": dict(ec)}],
        "project_items": [{"id": f"i{n}", "project_id": PID, "idx": n, "status": s, "content": {}, "label": {}}
                          for n, s in enumerate(statuses)],
    })


def _deliver(db):
    with patch.object(proj, "_fire_webhook") as fired:
        delivery.maybe_auto_deliver(db, PID)
    return fired, db.rows["project_submissions"][0]


def test_an_empty_project_is_never_delivered():
    fired, sub = _deliver(_ddb([], auto_deliver=True))
    assert sub["stage"] == "submitted" and "_ready_for_delivery" not in sub["eval_config"]
    fired.assert_not_called()


def test_any_item_not_done_holds_delivery():
    for other in ("pending", "queued", "in_progress", "needs_adjudication", "needs_second_reading"):
        fired, sub = _deliver(_ddb(["done", other], auto_deliver=True))
        assert sub["stage"] == "submitted" and "_ready_for_delivery" not in sub["eval_config"], other
        fired.assert_not_called()


def test_a_superseded_version_does_not_hold_or_count_toward_delivery():
    fired, sub = _deliver(_ddb(["done", "superseded"], auto_deliver=True))
    assert sub["stage"] == "delivered"
    fired.assert_called_once()
    fired, sub = _deliver(_ddb(["superseded"], auto_deliver=True))     # nothing current: not deliverable
    assert sub["stage"] == "submitted"
    fired.assert_not_called()


def test_human_sign_off_is_the_default_and_marks_ready_once():
    db = _ddb(["done", "done"])
    fired, sub = _deliver(db)
    first = sub["eval_config"]["_ready_for_delivery"]
    assert sub["stage"] == "submitted" and first
    fired.assert_not_called()
    fired, sub = _deliver(db)
    assert sub["eval_config"]["_ready_for_delivery"] == first and sub["stage"] == "submitted"
    fired.assert_not_called()


def test_auto_deliver_sends_once_and_only_on_the_transition():
    db = _ddb(["done"], auto_deliver=True)
    fired, sub = _deliver(db)
    assert sub["stage"] == "delivered"
    fired.assert_called_once_with(db, PID)
    fired, sub = _deliver(db)                                          # already delivered
    fired.assert_not_called()


def test_a_lost_race_to_delivered_sends_nothing():
    db = _ddb(["done"], auto_deliver=True)
    real_table = db.table

    def table(name):
        q = real_table(name)
        if name == "project_submissions":
            real_update = q.update

            def update(values):
                if values.get("stage") == "delivered":
                    db.rows["project_submissions"][0]["stage"] = "delivered"   # another worker won
                return real_update(values)
            q.update = update
        return q
    db.table = table
    fired, _ = _deliver(db)
    fired.assert_not_called()


def test_a_delivery_failure_is_logged_and_never_raised(caplog):
    db = _ddb(["done"], auto_deliver=True)
    with patch.object(proj, "_fire_webhook", side_effect=RuntimeError("boom")):
        delivery.maybe_auto_deliver(db, PID)
    assert db.rows["project_submissions"][0]["stage"] == "delivered"
    assert "Auto-deliver failed" in caplog.text
