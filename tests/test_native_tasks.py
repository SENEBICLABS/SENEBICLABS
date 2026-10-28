"""
The native task store, from HEALTH's side (services/tasks.py and the routes over it).

Answers recorded by the clinician platform are scored through the outbox into the same
label a direct scoring of the task gives, attributed to the clinicians who gave them, held
for a senior on a split, and delivered without any clinician identity.

The SQL functions themselves are tested against real Postgres in tests/sql.
"""
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from _fakedb import FakeDB
from app.services import scoring
from app.api.v1 import project as proj
from app.services import report as report_svc
from app.services import tasks
from app.services import templates as T

PID, POOL = "proj-n", "pool-n"
DOCS = {"d1": "one@x.com", "d2": "two@x.com", "d3": "three@x.com"}


def _ec(template="model_evaluation", **extra):
    ec = T.config_from_template(template, classes=["Normal", "Abnormal"])
    ec.update({"reviewers_per_item": 3, "adjudicate": True, **extra})
    return ec


def _verdict(v, correct=None, cm=None, notes=None):
    r = [{"from_name": "verdict", "to_name": "image", "type": "choices", "value": {"choices": [v]}}]
    if correct:
        r.append({"from_name": "correct_label", "to_name": "image", "type": "choices",
                  "value": {"choices": [correct]}})
    if cm is not None:
        r.append({"from_name": "critical_miss", "to_name": "image", "type": "choices",
                  "value": {"choices": [cm[0]]}})
        r.append({"from_name": "critical_miss_finding", "to_name": "image", "type": "choices",
                  "value": {"choices": [cm[1]] if cm[1] else []}})
    if notes:
        r.append({"from_name": "notes", "to_name": "image", "type": "textarea", "value": {"text": [notes]}})
    return r


def _db(ec=None, store="native", answers=(), status="queued", items=1):
    rows = {
        "project_submissions": [{"id": PID, "email": "c@x.com", "company": "Co", "stage": "submitted",
                                 "eval_config": ec or _ec(), "task_store": store}],
        "project_items": [{"id": f"item-{i}", "project_id": PID, "idx": i, "task_no": 1_000_000_000 + i,
                           "status": status, "content": {"prompt": "chest pain", "prediction": "Normal",
                                                         "case_id": f"C{i}", "_gold_expected": {"verdict": "Correct"}}}
                          for i in range(items)],
        "clinicians": [{"id": k, "email": v} for k, v in DOCS.items()],
        "pools": [{"id": POOL, "project_id": PID}],
        "task_answers": [],
        "answer_events": [],
        "review_items": [],
        "task_completions": [],
        "audit_events": [],
    }
    for n, (doc, result) in enumerate(answers, 1):
        rows["task_answers"].append({"id": n, "task_no": 1_000_000_000, "project_id": PID, "pool_id": POOL,
                                     "clinician_id": doc, "kind": "judgement", "result": result,
                                     "created_at": f"2026-10-24T10:0{n}:00Z", "withdrawn_at": None})
        rows["task_completions"].append({"clinician_id": doc, "pool_id": POOL, "ls_task_id": 1_000_000_000,
                                         "annotation_data": result, "kind": "judgement"})
    return FakeDB(rows, rpcs=_outbox_rpcs())


def _outbox_rpcs():
    """Python stand-ins for the outbox functions, enough to drive process_events. The real
    ones are proved against Postgres in tests/sql."""
    state = {}

    def claim(p):
        db = state["db"]
        out, seen = [], set()
        for e in sorted(db.rows["answer_events"], key=lambda e: e["id"]):
            if e["status"] == "pending" and e["task_no"] not in seen and len(out) < p["p_limit"]:
                seen.add(e["task_no"])
                e["status"] = "processing"
                e["attempts"] = e.get("attempts", 0) + 1
                upto = max(x["id"] for x in db.rows["answer_events"] if x["task_no"] == e["task_no"])
                out.append({"event_id": e["id"], "task_no": e["task_no"], "project_id": e["project_id"],
                            "upto_event_id": upto, "attempts": e["attempts"]})
        return out

    def finish(p):
        db = state["db"]
        ev = next(e for e in db.rows["answer_events"] if e["id"] == p["p_event_id"])
        if p["p_ok"]:
            for e in db.rows["answer_events"]:
                if e["task_no"] == ev["task_no"] and (e is ev or (e["status"] in ("pending", "failed")
                                                                  and e["id"] <= (p.get("p_upto_event_id") or ev["id"]))):
                    e["status"] = "done"
        else:
            ev["status"] = "failed" if ev["attempts"] >= 5 else "pending"
            ev["last_error"] = p.get("p_error")
        return None

    def rebuild(p):
        db = state["db"]
        n = 0
        for it in db.rows["project_items"]:
            if it["project_id"] == p["p_project"] and it["status"] != "pending":
                n += 1
                db.rows["answer_events"].append({"id": 1000 + n, "task_no": it["task_no"], "project_id": PID,
                                                 "kind": "rebuild", "status": "pending", "attempts": 0})
        return n

    rpcs = {"claim_answer_events": claim, "finish_answer_event": finish, "request_rebuild": rebuild}
    rpcs["_bind"] = lambda db: state.__setitem__("db", db)
    return rpcs


def _bound(db):
    db.rpcs["_bind"](db)
    return db


def _event(db, task_no=1_000_000_000, eid=None):
    eid = eid or len(db.rows["answer_events"]) + 1
    db.rows["answer_events"].append({"id": eid, "task_no": task_no, "project_id": PID,
                                     "kind": "answer_recorded", "status": "pending", "attempts": 0})


def _apply(db, task):
    ec = db.rows["project_submissions"][0]["eval_config"]
    t, adj, txt, prim = scoring.qa_from_ec(ec)
    with patch("app.services.audit.record"):
        return scoring.apply_task_annotations(db, task, t, PID, adj, txt, prim, ec)


SPLIT = [("d1", _verdict("Correct")), ("d2", _verdict("Incorrect", "Abnormal", ("Yes", "Pneumothorax"))),
         ("d3", _verdict("Partial", "Abnormal", ("No", None), notes="borderline"))]
AGREE = [("d1", _verdict("Incorrect", "Abnormal", ("Yes", "Pneumothorax"), "rib")),
         ("d2", _verdict("Incorrect", "Abnormal", ("Yes", "Pneumothorax"))),
         ("d3", _verdict("Incorrect", "Abnormal", ("No", None), "other words"))]


# ── scoring ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("answers", [SPLIT, AGREE, AGREE[:1]], ids=["split", "agree", "single"])
def test_the_outbox_scores_a_task_exactly_as_scoring_it_directly(answers):
    drained = _bound(_db(answers=answers))
    _event(drained)
    tasks.process_events(drained)
    direct = _db(answers=answers)
    _apply(direct, tasks.native_task(direct, direct.rows["project_items"][0]))
    a, b = drained.rows["project_items"][0], direct.rows["project_items"][0]
    assert a["status"] == b["status"] and a["label"] == b["label"]
    assert a["label"]["_task_no"] == 1_000_000_000 and "_ls_task_id" not in a["label"]


def test_the_client_deliverable_carries_no_clinician_or_task_reference(answers=AGREE):
    db = _bound(_db(answers=answers))
    _event(db)
    tasks.process_events(db)
    item = db.rows["project_items"][0]
    assert item["status"] == "done"
    delivered = [proj._client_item(r) for r in db.rows["project_items"]]
    assert "@" not in str(delivered) and "_task_no" not in str(delivered)
    for email in DOCS.values():
        assert email not in str(delivered)


def test_answers_are_attributed_to_the_clinicians_who_gave_them():
    db = _bound(_db(answers=SPLIT))
    _event(db)
    tasks.process_events(db)
    label = db.rows["project_items"][0]["label"]
    assert sorted(a["by"] for a in label["_annotations"]) == sorted(DOCS.values())
    # ...and none of it reaches the client.
    delivered = proj._client_item(db.rows["project_items"][0])
    assert "one@x.com" not in str(delivered)


def test_a_split_is_held_for_a_senior_and_a_clear_majority_is_done():
    split = _bound(_db(answers=SPLIT))
    _event(split)
    tasks.process_events(split)
    assert split.rows["project_items"][0]["status"] == "needs_adjudication"
    agree = _bound(_db(answers=AGREE))
    _event(agree)
    tasks.process_events(agree)
    assert agree.rows["project_items"][0]["status"] == "done"
    assert agree.rows["project_items"][0]["label"]["critical_miss"] == {"present": True, "finding": "Pneumothorax"}


def test_fewer_answers_than_the_ceiling_leave_the_task_in_progress():
    db = _bound(_db(answers=AGREE[:2]))
    _event(db)
    tasks.process_events(db)
    assert db.rows["project_items"][0]["status"] == "in_progress"


def test_a_withdrawn_answer_does_not_count():
    db = _bound(_db(answers=AGREE))
    db.rows["task_answers"][2]["withdrawn_at"] = "2026-10-24T11:00:00Z"
    _event(db)
    tasks.process_events(db)
    assert db.rows["project_items"][0]["status"] == "in_progress"
    assert db.rows["project_items"][0]["label"]["_reviewers"] == 2


def test_a_number_answer_survives_to_the_stored_label():
    ec = _ec("agent_trace_eval", reviewers_per_item=1)
    db = _bound(_db(ec=ec, answers=[("d1", [
        {"from_name": "verdict", "to_name": "image", "type": "choices", "value": {"choices": ["Incorrect"]}},
        {"from_name": "first_failed_step", "to_name": "image", "type": "number", "value": {"number": 3}},
        {"from_name": "rationale", "to_name": "image", "type": "textarea", "value": {"text": ["step 3"]}}])]))
    _event(db)
    tasks.process_events(db)
    assert db.rows["project_items"][0]["label"]["first_failed_step"] == 3


# ── the drain ───────────────────────────────────────────────────────────────────

def test_draining_twice_changes_nothing_the_second_time():
    db = _bound(_db(answers=AGREE))
    _event(db)
    first = tasks.process_events(db)
    label = dict(db.rows["project_items"][0]["label"])
    _event(db)                                       # a duplicate nudge for the same task
    second = tasks.process_events(db)
    assert first == {"processed": 1, "failed": 0} and second == {"processed": 1, "failed": 0}
    assert db.rows["project_items"][0]["label"] == label


def test_many_events_for_one_task_are_scored_once():
    db = _bound(_db(answers=AGREE))
    for eid in (1, 2, 3):
        _event(db, eid=eid)
    assert tasks.process_events(db) == {"processed": 1, "failed": 0}
    assert {e["status"] for e in db.rows["answer_events"]} == {"done"}


def test_a_failure_is_recorded_without_clinical_text_in_the_log(caplog):
    db = _bound(_db(answers=AGREE))
    _event(db)
    with patch.object(tasks, "process_task", side_effect=ValueError("chest pain, patient 1234")):
        out = tasks.process_events(db, budget_s=0.5)
    assert out["failed"] >= 1
    ev = db.rows["answer_events"][0]
    assert ev["status"] in ("pending", "failed") and "ValueError" in ev["last_error"]
    assert "chest pain" not in caplog.text


def test_finishing_the_batch_delivers_when_every_item_is_done_and_auto_deliver_is_on():
    ec = _ec(auto_deliver=True)
    db = _bound(_db(ec=ec, answers=AGREE))
    _event(db)
    with patch.object(proj, "_fire_webhook") as fired:
        tasks.process_events(db)
    assert db.rows["project_submissions"][0]["stage"] == "delivered"
    fired.assert_called_once()


def test_a_senior_decision_is_not_undone_by_a_rebuild():
    db = _bound(_db(answers=SPLIT))
    _event(db)
    tasks.process_events(db)
    item = db.rows["project_items"][0]
    item["label"] = {**item["label"], "verdict": "Incorrect", "_adjudicated": True}
    item["status"] = "done"
    db.rpc("request_rebuild", {"p_project": PID}).execute()
    tasks.process_events(db)
    assert item["label"]["_adjudicated"] is True and item["label"]["verdict"] == "Incorrect"


# ── second reading on the native store ──────────────────────────────────────────

def _authored_db(state):
    ec = T.config_from_template("benchmark_creation")
    ec.update({"reviewers_per_item": 1, "second_reading": True, "review_required": True})
    result = [{"from_name": "case", "to_name": "image", "type": "textarea", "value": {"text": ["Q?"]}},
              {"from_name": "expected_answer", "to_name": "image", "type": "textarea", "value": {"text": ["A."]}}]
    db = _bound(_db(ec=ec, answers=[("d1", result)]))
    db.rows["task_answers"][0]["kind"] = "approved_authoring"
    if state:
        db.rows["review_items"].append({"pool_id": POOL, "ls_task_id": 1_000_000_000, "state": state,
                                        "ls_annotation_id": 1, "revision": 1, "review_action": "edited",
                                        "reviewed_at": "2026-10-24T12:00:00Z"})
    return db


def test_approved_authored_work_is_done_with_its_reading_recorded():
    db = _authored_db("approved")
    _event(db)
    tasks.process_events(db)
    item = db.rows["project_items"][0]
    assert item["status"] == "done"
    assert item["label"]["_second_reading"]["approved"] is True
    assert item["label"]["_second_reading"]["rounds"] == 2 and item["label"]["_second_reading"]["edited"]


def test_authored_work_with_no_reading_goes_to_a_senior():
    db = _authored_db(None)
    _event(db)
    tasks.process_events(db)
    assert db.rows["project_items"][0]["status"] == "needs_adjudication"


# ── releasing, routes ──────────────────────────────────────────

def test_release_queues_only_pending_items_once():
    db = _db(status="pending", items=3)
    db.rows["project_items"][2]["status"] = "done"
    assert tasks.release(db, PID) == 2
    assert tasks.release(db, PID) == 0
    assert [i["status"] for i in db.rows["project_items"]] == ["queued", "queued", "done"]


def test_sync_pending_releases_without_any_outbound_call():
    db = _db(status="pending", items=2)
    with patch.object(proj, "get_client", return_value=db), \
            patch.object(proj, "_require_admin", return_value={"name": "op"}), \
            patch("httpx.post") as posted, patch("httpx.get") as got:
        res = proj.sync_pending(PID, x_admin_key="k")
    assert res.ok and "Released 2" in res.message
    posted.assert_not_called()
    got.assert_not_called()


def test_held_cases_by_project_carry_the_task_number_and_no_authors():
    db = _bound(_db(answers=SPLIT))
    _event(db)
    tasks.process_events(db)
    with patch.object(proj, "get_client", return_value=db), \
            patch.object(proj, "_require_admin", return_value={"name": "op"}):
        out = proj.admin_held_for_project(PID, x_admin_key="k")
    (case,) = out["held"]
    assert case["ls_task_id"] == 1_000_000_000
    assert "@" not in str(case["earlier_answers"])
    assert "_gold_expected" not in case["content"]


def test_reviewer_quality_joins_completions_by_task_number():
    db = _bound(_db(answers=AGREE))
    _event(db)
    tasks.process_events(db)
    with patch.object(proj, "get_client", return_value=db), \
            patch.object(proj, "_require_admin", return_value={"name": "op"}):
        out = proj.admin_reviewer_quality(PID, x_admin_key="k")
    assert set(out["reviewers"]) == set(DOCS.values())
    assert all(r["items_reviewed"] == 1 for r in out["reviewers"].values())


def test_release_rebuild_and_the_background_release_need_only_the_project():
    db = _bound(_db(status="pending", items=2))
    with patch.object(proj, "get_client", return_value=db), \
            patch.object(proj, "_require_admin", return_value={"id": "op", "name": "op"}), \
            patch.object(proj, "_kick_answers"):
        assert "Released 2" in proj.admin_release(proj.ProjectIdIn(project_id=PID), x_admin_key="k").message
        assert "Queued 2" in proj.admin_rebuild(proj.ProjectIdIn(project_id=PID), x_admin_key="k").message
        with pytest.raises(HTTPException) as e:
            proj.admin_release(proj.ProjectIdIn(project_id="no-such-project"), x_admin_key="k")
    assert e.value.status_code == 404


def test_a_label_is_read_back_by_its_task_number_only():
    assert tasks.task_ref({"_task_no": 1_000_000_007}) == 1_000_000_007
    assert tasks.task_ref({"_ls_task_id": 18193}) is None
    assert tasks.task_ref(None) is None
