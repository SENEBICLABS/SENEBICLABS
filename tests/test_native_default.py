"""
The task store is the only store. Every new project is created on it, and nothing in
HEALTH talks to Label Studio or keeps a path for it.
"""
import importlib
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from _fakedb import FakeDB

import test_native_tasks as N
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


# ── Label Studio is gone ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("module", ["app.services.labelstudio", "app.api.v1.ls"])
def test_the_label_studio_modules_are_gone(module):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


def test_no_route_reaches_label_studio():
    """Asked over HTTP rather than read from app.routes, whose shape differs between
    FastAPI versions (0.141 keeps an included router as one entry)."""
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    for path in ("/api/v1/ls/webhook", "/api/v1/project/admin/task-store",
                 "/api/v1/project/admin/held/194"):
        assert client.get(path).status_code == 404, path
    # The held list is asked for by project, and that route exists: refused for want of
    # a key, not missing.
    assert client.get("/api/v1/project/admin/held/project/p-1").status_code in (401, 403)


def test_no_application_code_keeps_a_label_studio_path():
    for path in [*(ROOT / "app").rglob("*.py"), ROOT / "scripts" / "native_e2e_stack.sh",
                 ROOT / "render.yaml", ROOT / "Dockerfile", ROOT / "Makefile"]:
        src = path.read_text()
        assert not re.search(r"labelstudio|label_studio|LS_URL|LS_TOKEN|LS_WEBHOOK_SECRET|/ls/|"
                             r"ls_project_id|_ls_task_id|is_native|LEGACY_PROJECT", src), path


def test_the_work_brief_carries_no_label_studio_link():
    db = FakeDB({"project_submissions": [{"id": "p", "company": "Co", "task_type": None, "rate_per_item": None}],
                 "project_items": []})
    with patch.object(proj, "get_client", return_value=db), \
            patch.object(proj, "_resolve_labeler", return_value={"id": "l", "name": "L"}), \
            patch.object(proj, "_labeler_can_access", return_value=True):
        out = proj.work_brief("p", x_work_code="c")
    assert "ls_link" not in out and out["company"] == "Co"
