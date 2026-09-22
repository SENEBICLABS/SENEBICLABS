"""
Second reading (services/second_reading.py): authored work counts only once the clinician
platform records that a DIFFERENT clinician approved it.

The platform (the workforce app) runs the author -> reviewer flow and enforces that the
reviewer is not the author; it keeps its state in `review_items` and publishes an answer to
Label Studio only on approval. These tests drive the real annotation path
(ls._apply_task_annotations, shared by the webhook and the pull) against an in-memory
database holding those platform tables.

Run: PYTHONPATH=. pytest tests/test_second_reading.py
"""
from unittest.mock import patch

import pytest

from _fakedb import FakeDB
from app.api.v1 import ls as ls_api
from app.api.v1 import project as proj
from app.services import labelstudio as ls
from app.services import second_reading as sr
from app.services import templates as T

PID, IID, LS_PROJECT, TASK, POOL = "proj-1", "item-1", 40, 101, "pool-1"


def _ec():
    ec = T.config_from_template("benchmark_creation")
    ec.update({"reviewers_per_item": 1, "second_reading": True, "review_required": True})
    return ec


def _db(review=None, status="queued"):
    rows = {
        "project_submissions": [{"id": PID, "email": "c@x.com", "stage": "submitted",
                                 "ls_project_id": LS_PROJECT, "eval_config": _ec()}],
        "project_items": [{"id": IID, "project_id": PID, "idx": 0, "status": status,
                           "content": {"case_id": "CARD-Q1", "topic": "Cardiology"}}],
        "pools": [{"id": POOL, "ls_project_id": LS_PROJECT}],
        "review_items": [],
    }
    if review:
        rows["review_items"].append({"pool_id": POOL, "ls_task_id": TASK, **review})
    return FakeDB(rows)


def _ann(ann_id, answer):
    return {"id": ann_id, "created_at": "2026-09-22T10:00:00Z", "result": [
        {"from_name": "case", "value": {"text": ["Q?"]}},
        {"from_name": "expected_answer", "value": {"text": [answer]}}]}


def _task(*anns):
    return {"id": TASK, "project": LS_PROJECT, "data": {"_item_id": IID}, "annotations": list(anns)}


def _apply(db, task, wait=0.0):
    ec = db.rows["project_submissions"][0]["eval_config"]
    t, adj, txt, prim = ls_api._qa_from_ec(ec)
    with patch("app.services.audit.record"):
        return ls_api._apply_task_annotations(db, task, t, PID, adj, txt, prim, ec, sr_wait=wait)


def _item(db):
    return db.rows["project_items"][0]


APPROVED = {"state": "approved", "revision": 0, "review_action": "approved",
            "ls_annotation_id": 7, "reviewed_at": "2026-09-22T10:00:01Z"}


def test_an_approved_answer_is_done_and_records_the_reading():
    db = _db(APPROVED)
    assert _apply(db, _task(_ann(7, "Gold."))) == "done"
    it = _item(db)
    assert it["label"]["expected_answer"] == "Gold."
    assert it["label"]["_second_reading"] == {"approved": True, "rounds": 1, "edited": False,
                                              "at": "2026-09-22T10:00:01Z"}


def test_only_the_approved_annotation_is_the_answer():
    # Two reviewers racing: the loser's annotation is on the task until it is withdrawn.
    db = _db(APPROVED)
    assert _apply(db, _task(_ann(6, "Loser's text."), _ann(7, "Approved text."))) == "done"
    assert _item(db)["label"]["expected_answer"] == "Approved text."


def test_sent_back_then_edited_and_approved_is_recorded_as_such():
    db = _db({**APPROVED, "revision": 2, "review_action": "edited"})
    _apply(db, _task(_ann(7, "Third draft, edited by the reader.")))
    rec = _item(db)["label"]["_second_reading"]
    assert rec["rounds"] == 3 and rec["edited"] is True


def test_an_answer_that_was_never_read_is_held_for_a_senior_reviewer():
    # Annotated straight in Label Studio, or a pool without review_required: no review row.
    db = _db(None)
    assert _apply(db, _task(_ann(7, "Unread."))) == "needs_adjudication"
    rec = _item(db)["label"]["_second_reading"]
    assert rec["approved"] is False and "without a second reading" in rec["reason"]


def test_a_senior_reviewer_can_approve_an_unread_answer_and_a_pull_keeps_it():
    db = _db(None)
    _apply(db, _task(_ann(7, "Unread.")))
    with patch.object(proj, "get_client", return_value=db), patch("app.services.audit.record"), \
         patch("app.api.v1.ls._maybe_auto_deliver"), patch.object(proj.settings, "ADMIN_API_KEY", "k"):
        proj.admin_adjudicate(proj.AdjudicateIn(project_id=PID, idx=0, final_label={}), x_admin_key="k")
    it = _item(db)
    assert it["status"] == "done"
    assert it["label"]["_second_reading"]["approved"] is True
    assert it["label"]["_second_reading"]["by_senior_reviewer"] is True
    assert _apply(db, _task(_ann(7, "Unread."))) == "done"      # a later pull changes nothing


def test_the_webhook_waits_for_an_approval_that_lands_a_moment_later():
    # The platform publishes the annotation, THEN records the approval.
    db = _db({"state": "needs_review", "revision": 0})
    ticks = {"n": 0}

    def _sleep(_s):
        ticks["n"] += 1
        db.rows["review_items"][0].update(APPROVED)
    with patch("app.services.second_reading.time.sleep", _sleep):
        assert _apply(db, _task(_ann(7, "Gold.")), wait=5.0) == "done"
    assert ticks["n"] >= 1


def test_an_approval_after_the_event_is_settled_by_reconcile():
    db = _db({"state": "needs_review", "revision": 0})
    task = _task(_ann(7, "Gold."))
    assert _apply(db, task) == sr.STATUS                       # approval not recorded yet
    db.rows["review_items"][0].update(APPROVED)
    with patch.object(ls, "get_task", return_value=task), patch("app.services.audit.record"):
        assert sr.reconcile(db, PID, _ec()) == 1
    assert _item(db)["status"] == "done"


def test_results_poll_and_delivery_settle_waiting_readings():
    db = _db({"state": "needs_review", "revision": 0})
    task = _task(_ann(7, "Gold."))
    _apply(db, task)
    with patch.object(proj, "get_client", return_value=db), \
         patch.object(proj, "_api_client_email", return_value="c@x.com"), \
         patch.object(proj, "_kick_sync"), patch.object(ls, "get_task", return_value=task), \
         patch("app.services.audit.record"):
        r = proj.api_results(PID, authorization="k")
        assert r["done"] == 0 and r["status"] == "in_review"
        db.rows["review_items"][0].update(APPROVED)
        assert proj.api_results(PID, authorization="k")["done"] == 1


def test_an_unread_project_cannot_be_delivered():
    db = _db({"state": "needs_review", "revision": 0})
    task = _task(_ann(7, "Gold."))
    _apply(db, task)
    with patch.object(proj, "get_client", return_value=db), \
         patch.object(ls, "get_task", return_value=task), patch("app.services.audit.record"), \
         patch.object(proj, "_fire_webhook"), patch.object(proj.settings, "ADMIN_API_KEY", "k"):
        with pytest.raises(proj.HTTPException) as e:
            proj.admin_advance(proj.AdminAdvance(submission_id=PID, stage="delivered"), x_admin_key="k")
        assert e.value.status_code == 422
        db.rows["review_items"][0].update(APPROVED)          # approved on the platform since
        proj.admin_advance(proj.AdminAdvance(submission_id=PID, stage="delivered"), x_admin_key="k")
    assert db.rows["project_submissions"][0]["stage"] == "delivered"


def test_projects_without_second_reading_are_untouched():
    db = _db(None)
    ec = db.rows["project_submissions"][0]["eval_config"]
    ec.update({"second_reading": False, "review_required": False})
    assert _apply(db, _task(_ann(7, "Gold."))) == "done"
    assert "_second_reading" not in _item(db)["label"]


def test_summary_counts_readings():
    items = [
        {"status": "done", "label": {"_second_reading": {"approved": True, "rounds": 1}}},
        {"status": "done", "label": {"_second_reading": {"approved": True, "rounds": 2, "edited": True}}},
        {"status": "done", "label": {"_second_reading": {"approved": True, "rounds": 1, "by_senior_reviewer": True}}},
        {"status": sr.STATUS, "label": {}},
        {"status": "needs_adjudication", "label": {"_second_reading": {"approved": False}}},
    ]
    assert sr.summary(items) == {"approved": 3, "approved_first_reading": 2,
                                 "sent_back_at_least_once": 1, "edited_by_reader": 1,
                                 "approved_by_senior_reviewer": 1, "awaiting_reading": 1,
                                 "not_read": 1}


def test_the_client_sees_the_reading_but_not_who_read_it():
    row = {"idx": 0, "content": {"case_id": "C"}, "label": {
        "expected_answer": "A", "_second_reading": {"approved": True, "rounds": 2, "edited": True}}}
    out = proj._client_item(row)
    assert out["second_reading"] == {"approved": True, "rounds": 2, "edited_by_reader": True,
                                     "by_senior_reviewer": False}
    assert "_second_reading" not in out["label"]


@pytest.mark.parametrize("template,expected", [
    ("benchmark_creation", True), ("gold_answers", True), ("contradiction_creation", True),
    ("grounding_eval", False), ("rubric_creation", False), ("agent_trace_eval", False),
])
def test_authoring_projects_turn_on_the_platform_review(template, expected):
    db = FakeDB({"project_submissions": []})
    with patch.object(proj, "get_client", return_value=db), \
         patch.object(proj, "_api_client_email", return_value="c@x.com"):
        proj.api_create_project(proj.CreateProjectIn(name="n", template=template), authorization="k")
    ec = db.rows["project_submissions"][0]["eval_config"]
    assert ec["second_reading"] is expected and ec["review_required"] is expected


def test_a_client_can_turn_second_reading_off():
    db = FakeDB({"project_submissions": []})
    with patch.object(proj, "get_client", return_value=db), \
         patch.object(proj, "_api_client_email", return_value="c@x.com"):
        proj.api_create_project(proj.CreateProjectIn(name="n", template="gold_answers",
                                                     second_reading=False), authorization="k")
    ec = db.rows["project_submissions"][0]["eval_config"]
    assert ec["second_reading"] is False and ec["review_required"] is False


def test_an_adjudicated_item_survives_a_later_pull():
    db = FakeDB({"project_items": [{"id": IID, "project_id": PID, "status": "done",
                                    "content": {}, "label": {"verdict": "Correct", "_adjudicated": True}}]})
    split = {"id": 7, "data": {"_item_id": IID}, "annotations": [
        {"result": [{"from_name": "verdict", "value": {"choices": [v]}}]}
        for v in ("Correct", "Incorrect", "Partial")]}
    with patch("app.services.audit.record"):
        assert ls_api._apply_task_annotations(db, split, 3, PID, True) == "done"
    assert _item(db)["label"] == {"verdict": "Correct", "_adjudicated": True}
