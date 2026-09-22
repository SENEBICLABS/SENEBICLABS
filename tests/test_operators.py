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
    with patch("app.api.v1.ls._maybe_auto_deliver"):
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
