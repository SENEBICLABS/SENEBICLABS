"""
Per-operator admin keys (services/operators.py).

One shared admin key could not say who adjudicated or delivered anything, and one leak
exposed everything. Each operator now has their own revocable key, and admin decisions are
recorded under their name. The deployment's ADMIN_API_KEY stays as the root key.

Run: PYTHONPATH=. pytest tests/test_operators.py
"""
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from _fakedb import FakeDB
from app.api.v1 import project as proj
from app.services import operators

ROOT_KEY = "root-secret"


@pytest.fixture
def db():
    d = FakeDB({"operators": [], "audit_events": [], "project_submissions": [], "project_items": []})
    with patch.object(operators.settings, "ADMIN_API_KEY", ROOT_KEY), \
         patch("app.services.supabase_client.get_client", return_value=d), \
         patch.object(proj, "get_client", return_value=d):
        yield d


def test_root_key_works_without_a_database():
    with patch.object(operators.settings, "ADMIN_API_KEY", ROOT_KEY):
        assert operators.authenticate(None, ROOT_KEY)["id"] == "root"


def test_an_operator_key_identifies_its_operator_and_only_its_hash_is_stored(db):
    row, key = operators.create(db, "Dr Amina", "amina@example.com")
    assert key.startswith("op_")
    stored = db.rows["operators"][0]
    assert key not in str(stored) and stored["key_hash"] == operators._hash(key)
    assert operators.authenticate(db, key)["name"] == "Dr Amina"


def test_a_revoked_key_stops_working_on_its_next_use(db):
    row, key = operators.create(db, "Dr Amina", None)
    operators.revoke(db, row["id"])
    assert operators.authenticate(db, key) is None
    with pytest.raises(HTTPException) as e:
        operators.require(key)
    assert e.value.status_code == 403


@pytest.mark.parametrize("key", [None, "", "wrong", "op_not-a-real-key", ROOT_KEY + "x"])
def test_anything_else_is_refused(db, key):
    with pytest.raises(HTTPException):
        operators.require(key)


@pytest.mark.parametrize("key", ["kéy", ROOT_KEY + "—", "op_é"])
def test_a_non_ascii_key_is_refused_rather_than_raising(db, key):
    """`hmac.compare_digest` raises TypeError on a non-ASCII str instead of returning
    False, so such a key used to leave authenticate as an exception rather than as a
    refusal. A key that can be nobody's is a refusal, by the same route as any other."""
    assert operators.authenticate(db, key) is None
    with pytest.raises(HTTPException) as e:
        operators.require(key)
    assert e.value.status_code == 403


def test_a_non_ascii_header_is_refused_rather_than_answering_500(db):
    """Where the TypeError reached a client. A header is carried as bytes and decoded as
    latin-1, which is how a byte above 0x7f becomes a non-ASCII str; httpx will not send
    such a value as a str, so this sends the bytes a real client sends. The refusal has to
    be the one an ordinary wrong key gets, not a server error."""
    from fastapi.testclient import TestClient

    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)
    route = "/api/v1/project/admin/ops-health"
    bad = client.get(route, headers={"X-Admin-Key": "Admin key — root".encode()})
    ascii_wrong = client.get(route, headers={"X-Admin-Key": b"wrong"})
    assert ascii_wrong.status_code == 403
    assert bad.status_code == 403 and bad.json() == ascii_wrong.json()


def test_the_guard_leaves_valid_keys_and_ordinary_refusals_alone(db):
    """The guard runs before both key paths, so this is what says it did not shorten
    either: the root key still needs no database, and an operator key is still looked up
    by hash and still stamps last_used_at."""
    _, op_key = operators.create(db, "Dr Amina", None)
    assert operators.authenticate(None, ROOT_KEY)["id"] == "root"
    assert operators.authenticate(db, op_key)["name"] == "Dr Amina"
    assert db.rows["operators"][0]["last_used_at"]
    assert operators.authenticate(db, "wrong") is None
    assert operators.authenticate(db, "op_not-a-real-key") is None


def test_only_the_root_key_manages_operators(db):
    out = proj.admin_create_operator(proj.OperatorIn(name="Dr Amina"), x_admin_key=ROOT_KEY)
    op_key = out["admin_key"]
    # An operator cannot mint more operators, list them, or revoke anyone.
    for call in (lambda: proj.admin_create_operator(proj.OperatorIn(name="X"), x_admin_key=op_key),
                 lambda: proj.admin_list_operators(x_admin_key=op_key),
                 lambda: proj.admin_revoke_operator(proj.OperatorRevokeIn(operator_id="x"), x_admin_key=op_key)):
        with pytest.raises(HTTPException) as e:
            call()
        assert e.value.status_code == 403
    listed = proj.admin_list_operators(x_admin_key=ROOT_KEY)["operators"]
    assert [o["name"] for o in listed] == ["Dr Amina"] and "key_hash" not in listed[0]


def test_an_adjudication_is_recorded_under_the_operators_name(db):
    _, key = operators.create(db, "Dr Amina", None)
    db.rows["project_items"].append({"id": "i1", "project_id": "p1", "idx": 0,
                                     "status": "needs_adjudication", "label": {"verdict": "Correct"}})
    with patch("app.services.delivery.maybe_auto_deliver"):
        proj.admin_adjudicate(proj.AdjudicateIn(project_id="p1", idx=0, final_label={"verdict": "Incorrect"}),
                              x_admin_key=key)
    assert db.rows["project_items"][0]["label"]["_adjudicated_by"] == "Dr Amina"
    ev = db.rows["audit_events"][-1]
    assert ev["actor_name"] == "Dr Amina" and ev["source"] == "adjudication"


def test_a_delivery_is_recorded_under_the_operators_name(db):
    _, key = operators.create(db, "Ops Lead", None)
    db.rows["project_submissions"].append({"id": "p1", "stage": "production", "eval_config": {}})
    db.rows["project_items"].append({"id": "i1", "project_id": "p1", "idx": 0, "status": "done"})
    with patch.object(proj, "_fire_webhook"):
        proj.admin_advance(proj.AdminAdvance(submission_id="p1", stage="delivered"), x_admin_key=key)
    ev = [e for e in db.rows["audit_events"] if e["action"] == "stage"][-1]
    assert ev["actor_name"] == "Ops Lead" and ev["value"]["stage"] == "delivered"


def test_root_key_use_is_visible_in_the_audit_trail(db):
    db.rows["project_submissions"].append({"id": "p1", "stage": "production", "eval_config": {}})
    db.rows["project_items"].append({"id": "i1", "project_id": "p1", "idx": 0, "status": "done"})
    with patch.object(proj, "_fire_webhook"):
        proj.admin_advance(proj.AdminAdvance(submission_id="p1", stage="delivered"), x_admin_key=ROOT_KEY)
    assert db.rows["audit_events"][-1]["actor_name"] == "root"


def test_the_deciding_clinician_is_recorded_and_never_reaches_the_client(db):
    """The operator key says who typed the decision. The clinical judgement belongs to a
    named clinician — recorded internally, and stripped from everything a client sees."""
    _, key = operators.create(db, "Godwin Yampoi", None)
    db.rows["project_items"].append({"id": "i1", "project_id": "p1", "idx": 0,
                                     "status": "needs_adjudication",
                                     "content": {"case_id": "LAB-07"},
                                     "label": {"verdict": "Partial"}})
    with patch("app.services.delivery.maybe_auto_deliver"):
        proj.admin_adjudicate(proj.AdjudicateIn(
            project_id="p1", idx=0, final_label={"verdict": "Partial"},
            decided_by="Dr A Okafor, MMed Pathology, KMPDC 12345",
            note="Panel: incomplete workup before deferral."), x_admin_key=key)
    label = db.rows["project_items"][0]["label"]
    assert label["_adjudicated_by"] == "Dr A Okafor, MMed Pathology, KMPDC 12345"
    assert label["_recorded_by"] == "Godwin Yampoi"
    client_view = proj._client_item(db.rows["project_items"][0])
    assert "Okafor" not in str(client_view) and "Godwin" not in str(client_view)
    assert client_view["label"]["verdict"] == "Partial"


def test_without_a_named_clinician_it_falls_back_to_the_operator(db):
    _, key = operators.create(db, "Godwin Yampoi", None)
    db.rows["project_items"].append({"id": "i2", "project_id": "p1", "idx": 1,
                                     "status": "needs_adjudication", "content": {}, "label": {}})
    with patch("app.services.delivery.maybe_auto_deliver"):
        proj.admin_adjudicate(proj.AdjudicateIn(project_id="p1", idx=1, final_label={"verdict": "Correct"}),
                              x_admin_key=key)
    assert db.rows["project_items"][0]["label"]["_adjudicated_by"] == "Godwin Yampoi"


def test_held_cases_are_offered_to_the_platform_without_saying_who_judged(db):
    """A senior reviewer must see the earlier answers and the reasoning, never the authors:
    knowing which colleague wrote which answer is the pressure independent review removes."""
    _, key = operators.create(db, "Godwin Yampoi", None)
    db.rows["project_submissions"].append({"id": "p1", "task_store": "native", "eval_config": {}})
    db.rows["project_items"].append({
        "id": "i1", "project_id": "p1", "idx": 6, "status": "needs_adjudication",
        "content": {"case_id": "LAB-07", "scenario": "ALT 62", "_gold_expected": "secret"},
        "label": {"verdict": "Partial", "_task_no": 1_000_004_242, "_agreement": 0.5,
                  "_annotations": [
                      {"by": "a@example.com", "at": "x", "label": {"verdict": "Partial", "severity": "Minor"}},
                      {"by": "b@example.com", "at": "y", "label": {"verdict": "Correct"}}]}})
    out = proj.admin_held_for_project("p1", x_admin_key=key)
    assert out["count"] == 1
    item = out["held"][0]
    assert item["ls_task_id"] == 1_000_004_242 and item["case_id"] == "LAB-07" and item["idx"] == 6
    assert item["earlier_answers"] == [{"verdict": "Partial", "severity": "Minor"}, {"verdict": "Correct"}]
    body = str(out)
    assert "example.com" not in body          # no authors
    assert "_gold_expected" not in body and "secret" not in body   # no internal content


def test_a_pool_with_nothing_held_returns_an_empty_list(db):
    _, key = operators.create(db, "Godwin Yampoi", None)
    db.rows["project_submissions"].append({"id": "p1", "task_store": "native", "eval_config": {}})
    db.rows["project_items"].append({"id": "i1", "project_id": "p1", "idx": 0, "status": "done",
                                     "content": {}, "label": {}})
    assert proj.admin_held_for_project("p1", x_admin_key=key)["held"] == []


def test_an_unknown_project_is_a_404(db):
    _, key = operators.create(db, "Godwin Yampoi", None)
    with pytest.raises(HTTPException) as e:
        proj.admin_held_for_project("no-such-project", x_admin_key=key)
    assert e.value.status_code == 404
