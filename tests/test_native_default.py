"""
New work runs on the native task store. Every new project is created native, and no new
Label Studio project is ever created: a project still on Label Studio without one is refused
with the way forward. A project that already has a Label Studio project keeps syncing there
until it is retired, so existing work is not broken.
"""
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from _fakedb import FakeDB

import test_native_tasks as N
from app.api.v1 import ls as ls_api
from app.api.v1 import project as proj
from app.services import tasks

ROOT = Path(__file__).resolve().parents[1]


# ── new projects are native ──────────────────────────────────────────────────────

def test_a_project_created_through_the_api_is_native():
    db = FakeDB({"project_submissions": []})
    with patch.object(proj, "get_client", return_value=db), \
            patch.object(proj, "_api_client_email", return_value="c@x.com"):
        proj.api_create_project(proj.CreateProjectIn(name="n", template="model_evaluation"), authorization="k")
    assert db.rows["project_submissions"][0]["task_store"] == tasks.NATIVE


def test_a_submitted_project_is_native():
    db = FakeDB({"project_submissions": []})
    with patch.object(proj, "get_client", return_value=db), patch.object(proj, "email_service", MagicMock()):
        proj.submit(proj.ProjectSubmission(name="A", email="a@x.com", company="Co", description="d"))
    assert db.rows["project_submissions"][0]["task_store"] == tasks.NATIVE


def test_every_project_insert_in_the_api_sets_the_native_store():
    """Covers the benchmark seeder too, and any insert added later."""
    src = (ROOT / "app/api/v1/project.py").read_text()
    inserts = [m.start() for m in re.finditer(r'table\("project_submissions"\)\.insert\(', src)]
    assert len(inserts) == 3
    for at in inserts:
        call = src[at:src.index(".execute()", at)]
        assert '"task_store": tasks.NATIVE' in call, src[at:at + 200]


# ── no new Label Studio project is created ───────────────────────────────────────

def _ls_db(ls_project_id):
    from app.services import labelstudio
    db = N._db(store="label_studio", status="pending", items=2)
    db.rows["project_submissions"][0]["ls_project_id"] = ls_project_id
    for key in labelstudio.required_data_keys(db.rows["project_submissions"][0]["eval_config"]):
        for item in db.rows["project_items"]:
            item["content"].setdefault(key, f"{key} text")
    db.rows["pools"] = [{"id": "pool-1", "name": "Pool", "ls_project_id": ls_project_id}]
    return db


def test_ingest_sync_refuses_to_create_a_label_studio_project():
    db = _ls_db(None)
    with patch.object(proj, "get_client", return_value=db), \
            patch.object(proj, "_require_admin", return_value={"name": "op"}), \
            patch("app.services.labelstudio.create_project") as created, \
            patch("app.services.labelstudio.push_tasks") as pushed:
        with pytest.raises(HTTPException) as e:
            proj.sync_pending(N.PID, x_admin_key="k")
    assert e.value.status_code == 409 and e.value.detail == tasks.NO_NEW_LABEL_STUDIO
    created.assert_not_called()
    pushed.assert_not_called()
    assert db.rows["project_submissions"][0]["ls_project_id"] is None


def test_the_label_studio_sync_route_refuses_to_create_a_project():
    db = _ls_db(None)
    with patch.object(ls_api, "get_client", return_value=db), \
            patch.object(ls_api, "_require_admin", return_value={"name": "op"}), \
            patch("app.services.labelstudio.is_configured", return_value=True), \
            patch("app.services.labelstudio.create_project") as created, \
            patch("app.services.labelstudio.push_tasks") as pushed:
        with pytest.raises(HTTPException) as e:
            ls_api.ls_sync(ls_api.SyncIn(project_id=N.PID), x_admin_key="k")
    assert e.value.status_code == 409 and e.value.detail == tasks.NO_NEW_LABEL_STUDIO
    created.assert_not_called()
    pushed.assert_not_called()


# ── existing Label Studio projects keep working ──────────────────────────────────

def test_a_project_with_a_label_studio_project_still_syncs_there():
    db = _ls_db(5)
    with patch.object(ls_api, "get_client", return_value=db), \
            patch.object(ls_api, "_require_admin", return_value={"name": "op"}), \
            patch("app.services.labelstudio.is_configured", return_value=True), \
            patch("app.services.labelstudio.create_project") as created, \
            patch("app.services.labelstudio.update_project_config") as updated, \
            patch("app.services.labelstudio.push_tasks", return_value=2) as pushed:
        ls_api.ls_sync(ls_api.SyncIn(project_id=N.PID), x_admin_key="k")
    created.assert_not_called()
    assert updated.call_args[0][0] == 5 and pushed.call_args[0][0] == 5


def test_the_unused_label_studio_auto_push_is_gone():
    assert not hasattr(ls_api, "push_new_items")
