"""
Second reading (services/second_reading.py): authored work is read by a second clinician
before it counts, and a "Revise" goes back to the author with the note.

Drives the real annotation path (ls._apply_task_annotations, the one the webhook and the
manual pull share) against an in-memory database, with Label Studio calls recorded.

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

PID, IID = "proj-1", "item-1"


def _ec():
    ec = T.config_from_template("benchmark_creation")
    ec.update({"reviewers_per_item": 1, "second_reading": True})
    return ec


class _LS:
    """Records what would have been sent to Label Studio."""

    def __init__(self):
        self.created, self.imported, self.deleted = [], [], []

    def create_project(self, title, label_config, reviewers=1):
        self.created.append({"title": title, "config": label_config})
        return 900 + len(self.created)

    def import_tasks(self, ls_pid, tasks, chunk=500):
        self.imported.append((ls_pid, tasks))
        return len(tasks)

    def delete_task(self, task_id):
        self.deleted.append(task_id)


@pytest.fixture
def env():
    db = FakeDB({
        "project_submissions": [{"id": PID, "email": "c@x.com", "eval_config": _ec()}],
        "project_items": [{"id": IID, "project_id": PID, "idx": 0, "status": "queued",
                           "content": {"case_id": "CARD-Q1", "topic": "Cardiology"}}],
    })
    fake = _LS()
    with patch.object(ls, "create_project", fake.create_project), \
         patch.object(ls, "import_tasks", fake.import_tasks), \
         patch.object(ls, "delete_task", fake.delete_task), \
         patch("app.services.audit.record"):
        yield db, fake


def _author(db, task_id, case="Q?", answer="Gold answer."):
    task = {"id": task_id, "data": {"_item_id": IID}, "annotations": [{
        "created_at": "2026-09-21T10:00:00Z",
        "result": [
            {"from_name": "case", "value": {"text": [case]}},
            {"from_name": "expected_answer", "value": {"text": [answer]}},
        ]}]}
    ec = db.rows["project_submissions"][0]["eval_config"]
    t, adj, txt, prim = ls_api._qa_from_ec(ec)
    return ls_api._apply_task_annotations(db, task, t, PID, adj, txt, prim, ec)


def _read(db, rnd, decision, fix=None):
    result = [{"from_name": "decision", "value": {"choices": [decision]}}]
    if fix:
        result.append({"from_name": "fix", "value": {"text": [fix]}})
    task = {"id": 5000 + rnd, "data": {"_item_id": IID, "_sr_round": rnd},
            "annotations": [{"created_at": "2026-09-21T11:00:00Z", "result": result}]}
    return sr.on_review(db, task, PID)


def _item(db):
    return db.rows["project_items"][0]


def test_finished_authoring_goes_to_a_second_reader_not_to_done(env):
    db, fake = env
    assert _author(db, 101) == sr.STATUS
    assert _item(db)["status"] == sr.STATUS
    # One review project, recorded on the project, and one review task showing the work.
    assert len(fake.created) == 1
    assert db.rows["project_submissions"][0]["eval_config"]["_ls_review_project_id"] == 901
    (_pid, tasks), = fake.imported
    data = tasks[0]["data"]
    assert data["authored_case"] == "Q?" and data["authored_expected_answer"] == "Gold answer."
    assert data["topic"] == "Cardiology" and data["_sr_round"] == 1 and data["_item_id"] == IID


def test_the_review_form_is_valid_for_label_studio(env):
    xml = ls.build_label_config(sr.review_config(_ec()))
    assert 'name="decision"' in xml and 'value="$authored_expected_answer"' in xml


def test_a_repeated_event_or_pull_does_not_send_it_twice(env):
    db, fake = env
    _author(db, 101)
    _author(db, 101)
    assert len(fake.imported) == 1 and len(fake.created) == 1


def test_approval_makes_it_done_and_a_later_pull_keeps_it_done(env):
    db, fake = env
    _author(db, 101)
    assert _read(db, 1, sr.APPROVE) == "done"
    assert _item(db)["label"]["_second_reading"]["approved"] is True
    assert _author(db, 101) == "done"               # a pull re-applying the same text
    assert len(fake.imported) == 1                  # not sent again
    assert _item(db)["label"]["_second_reading"]["approved"] is True


def test_revise_sends_it_back_with_the_note_and_the_previous_draft(env):
    db, fake = env
    _author(db, 101)
    assert _read(db, 1, sr.REVISE, fix="Add the weight-based dose.") == "pending"
    it = _item(db)
    assert it["status"] == "pending" and it["label"] is None
    assert fake.deleted == [101]                    # the finished draft cannot be re-pulled
    assert it["content"]["_revision"]["note"] == "Add the weight-based dose."
    # What the author sees next: the note on the first block, their draft pre-filled.
    task = ls._task_for(it, ls.revision_note_key(_ec()))
    assert task["data"]["topic"].startswith("REVISION REQUESTED BY THE SECOND READER:\nAdd the weight-based dose.")
    assert task["data"]["topic"].endswith("Cardiology")
    assert task["predictions"][0]["result"][1]["value"]["text"] == ["Gold answer."]
    assert "_revision" not in task["data"] and "_sr" not in task["data"]


def test_the_revised_draft_is_read_again_and_a_stale_decision_is_ignored(env):
    db, fake = env
    _author(db, 101)
    _read(db, 1, sr.REVISE, fix="Fix it.")
    assert _author(db, 102, answer="Better answer.") == sr.STATUS
    assert len(fake.imported) == 2 and fake.imported[1][1][0]["data"]["_sr_round"] == 2
    assert "_revision" not in _item(db)["content"]
    # A late decision on round 1 must not settle round 2.
    assert _read(db, 1, sr.APPROVE) is None
    assert _item(db)["status"] == sr.STATUS
    assert _read(db, 2, sr.APPROVE) == "done"


def test_an_edit_after_approval_is_read_again(env):
    db, fake = env
    _author(db, 101)
    _read(db, 1, sr.APPROVE)
    assert _author(db, 101, answer="Edited after approval.") == sr.STATUS
    assert len(fake.imported) == 2


def test_endless_disagreement_goes_to_a_senior_reviewer(env):
    db, fake = env
    for rnd in range(1, sr.MAX_ROUNDS + 1):
        _author(db, 100 + rnd, answer=f"draft {rnd}")
        status = _read(db, rnd, sr.REVISE, fix=f"note {rnd}")
    assert status == "needs_adjudication"
    it = _item(db)
    assert it["label"]["_second_reading"]["escalated"] is True
    assert [h["note"] for h in it["label"]["_second_reading"]["history"]] == ["note 1", "note 2", "note 3"]
    # A pull while it waits for the senior reviewer does not restart the loop.
    assert _author(db, 100 + sr.MAX_ROUNDS, answer=f"draft {sr.MAX_ROUNDS}") == "needs_adjudication"
    assert len(fake.imported) == sr.MAX_ROUNDS


def test_summary_counts_what_was_read_and_sent_back(env):
    db, _ = env
    _author(db, 101)
    _read(db, 1, sr.REVISE, fix="x")
    _author(db, 102, answer="v2")
    _read(db, 2, sr.APPROVE)
    s = sr.summary(db.rows["project_items"])
    assert s == {"items_read": 1, "approved": 1, "approved_first_reading": 0,
                 "sent_back_at_least_once": 1, "awaiting_reading": 0, "being_revised": 0,
                 "escalated": 0}


def test_the_client_sees_that_an_item_was_read_but_not_who_read_it(env):
    db, _ = env
    _author(db, 101)
    _read(db, 1, sr.APPROVE)
    out = proj._client_item(_item(db))
    assert out["second_reading"] == {"approved": True, "rounds": 1}
    assert "_second_reading" not in out["label"] and "_sr" not in out["content"]


def test_an_adjudicated_item_survives_a_later_pull():
    # A pull rebuilds the label from the reviewers' annotations. Without the guard it put a
    # settled split straight back in the adjudication queue.
    db = FakeDB({"project_items": [{"id": IID, "project_id": PID, "status": "done",
                                    "content": {}, "label": {"verdict": "Correct", "_adjudicated": True}}]})
    split = {"id": 7, "data": {"_item_id": IID}, "annotations": [
        {"result": [{"from_name": "verdict", "value": {"choices": [v]}}]}
        for v in ("Correct", "Incorrect", "Partial")]}
    with patch("app.services.audit.record"):
        assert ls_api._apply_task_annotations(db, split, 3, PID, True) == "done"
    assert _item(db)["label"] == {"verdict": "Correct", "_adjudicated": True}


@pytest.mark.parametrize("template,expected", [
    ("benchmark_creation", True), ("gold_answers", True), ("contradiction_creation", True),
    ("grounding_eval", False), ("rubric_creation", False), ("agent_trace_eval", False),
])
def test_second_reading_is_on_by_default_for_authoring_only(template, expected):
    db = FakeDB({"project_submissions": []})
    with patch.object(proj, "get_client", return_value=db), \
         patch.object(proj, "_api_client_email", return_value="c@x.com"):
        proj.api_create_project(proj.CreateProjectIn(name="n", template=template), authorization="k")
    assert db.rows["project_submissions"][0]["eval_config"]["second_reading"] is expected


def test_a_client_can_turn_second_reading_off():
    db = FakeDB({"project_submissions": []})
    with patch.object(proj, "get_client", return_value=db), \
         patch.object(proj, "_api_client_email", return_value="c@x.com"):
        proj.api_create_project(proj.CreateProjectIn(name="n", template="gold_answers",
                                                     second_reading=False), authorization="k")
    assert db.rows["project_submissions"][0]["eval_config"]["second_reading"] is False


def test_two_callers_racing_on_one_item_send_it_to_a_reader_once(env):
    # The webhook and a pull both read the item before either writes. Only the one whose
    # status transition lands sends a review task; the other takes the winner's state.
    db, fake = env
    ec = db.rows["project_submissions"][0]["eval_config"]
    stale = dict(_item(db))                         # both callers read this
    label = {"case": "Q?", "expected_answer": "A."}
    first = sr.on_authored(db, stale, label, ec, PID)
    second = sr.on_authored(db, stale, label, ec, PID)
    assert first[0] == second[0] == sr.STATUS
    assert len(fake.imported) == 1
    assert second[1]["_sr"]["round"] == 1


def test_a_failed_send_releases_the_item(env):
    db, fake = env

    def _boom(*_a, **_k):
        raise RuntimeError("Label Studio down")
    with patch.object(ls, "import_tasks", _boom), pytest.raises(RuntimeError):
        _author(db, 101)
    it = _item(db)
    assert it["status"] == "queued" and "_sr" not in it["content"]
    assert _author(db, 101) == sr.STATUS            # and the next attempt goes through
