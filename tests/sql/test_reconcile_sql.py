"""
scripts/reconcile_ls_identity.py against real Postgres: its plan applies, remaps every
reference to the right item, and its rollback restores the database exactly.

The fixture is built to be awkward. Label Studio task id 1 exists in BOTH instances, for
different items in different projects, so a match on the bare number would be wrong; the
script must resolve it through the referencing row's own project.
"""
import json
import uuid
from unittest.mock import patch

import psycopg

from _fakedb import FakeDB
from app.api.v1 import ls as ls_api
from app.services import templates as T
from conftest import apply_native
from scripts import reconcile_ls_identity as R

EC = {**T.config_from_template("model_evaluation", classes=["Normal", "Abnormal"]), "reviewers_per_item": 2}


def _verdict(v, at):
    return {"result": [{"from_name": "verdict", "to_name": "image", "type": "choices", "value": {"choices": [v]}}],
            "created_at": at}


def _label(item_id, ls_task_id, answers):
    """The label HEALTH stored from Label Studio, computed by HEALTH's own code."""
    fake = FakeDB({"project_submissions": [{"id": "p", "eval_config": EC}],
                   "project_items": [{"id": item_id, "project_id": "p"}], "pools": [], "review_items": [],
                   "audit_events": []})
    task = {"id": ls_task_id, "project": 1, "data": {"_item_id": item_id},
            "annotations": [{"id": n, "completed_by": {"email": "svc"}, **a} for n, a in enumerate(answers)]}
    t, adj, txt, prim = ls_api._qa_from_ec(EC)
    with patch("app.services.audit.record"):
        ls_api._apply_task_annotations(fake, task, t, "p", adj, txt, prim, EC)
    row = fake.rows["project_items"][0]
    return row["label"], row["status"]


def _tables(c):
    out = {}
    for t in ("project_submissions", "project_items", "pools", "task_completions", "clinicians"):
        cur = c.execute(f"select * from {t} order by id")
        cols = [d.name for d in cur.description]
        out[t] = [{k: v for k, v in zip(cols, r) if k != "task_no"} for r in cur.fetchall()]
    return out


def test_plan_remaps_by_item_and_rollback_restores_everything(dsn_before_native, tmp_path):
    with psycopg.connect(dsn_before_native, autocommit=True) as c:
        P, Q = str(uuid.uuid4()), str(uuid.uuid4())
        A, B, X = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        d1, d2 = str(uuid.uuid4()), str(uuid.uuid4())
        pool_p, pool_q = str(uuid.uuid4()), str(uuid.uuid4())
        answers = {A: [_verdict("Correct", "2026-09-01T10:00:00Z"), _verdict("Correct", "2026-09-01T11:00:00Z")],
                   B: [_verdict("Incorrect", "2026-09-01T12:00:00Z")]}
        # Project P lived on the old instance as tasks 500/501, and was re-imported into
        # the new one as tasks 1/2. Project Q's item X is task 1 on the OLD instance.
        old_id = {A: 500, B: 501}
        for pid, ls_pid in ((P, 3), (Q, 900)):
            c.execute("insert into project_submissions (id, name, email, company, description, eval_config, ls_project_id)"
                      " values (%s, 'P', 'c@x.com', 'Co', 'd', %s, %s)", (pid, json.dumps(EC), ls_pid))
        for item, pid, idx in ((A, P, 0), (B, P, 1), (X, Q, 0)):
            label, status = (_label(item, old_id[item], answers[item]) if item in answers
                             else _label(item, 1, [_verdict("Correct", "2026-08-01T10:00:00Z")]))
            c.execute("insert into project_items (id, project_id, idx, content, label, status) values (%s, %s, %s, %s, %s, %s)",
                      (item, pid, idx, json.dumps({"prompt": "q", "prediction": "Normal"}), json.dumps(label), status))
        for d in (d1, d2):
            c.execute("insert into clinicians (id, name, email, access_code) values (%s, 'Dr', %s, %s)", (d, f"{d[:6]}@x", d))
        c.execute("insert into pools (id, name, ls_project_id, calibration_items) values (%s, 'P', 3, '[]'), (%s, 'Q', 900, '[]')",
                  (pool_p, pool_q))
        for doc, item, n in ((d1, A, 0), (d2, A, 1), (d1, B, 0)):
            c.execute("insert into task_completions (clinician_id, pool_id, ls_task_id, annotation_data, kind, completed_at)"
                      " values (%s, %s, %s, %s, 'judgement', %s)",
                      (doc, pool_p, old_id[item], json.dumps(answers[item][n]["result"]),
                       answers[item][n]["created_at"]))
        c.execute("insert into task_completions (clinician_id, pool_id, ls_task_id, annotation_data, kind, completed_at)"
                  " values (%s, %s, 1, %s, 'judgement', '2026-08-01T10:00:00Z')",
                  (d2, pool_q, json.dumps(_verdict("Correct", "")["result"])))

        # The backups, in backup.sh's layout.
        def task(tid, project, item, anns):
            return {"id": tid, "project": project, "data": {"_item_id": item},
                    "annotations": [{"id": tid * 10 + n, "result": a["result"], "created_at": a["created_at"]}
                                    for n, a in enumerate(anns)]}
        old, new = tmp_path / "old", tmp_path / "new"
        for d in (old / "labelstudio", new / "labelstudio", new / "supabase"):
            d.mkdir(parents=True)
        (old / "labelstudio" / "project-191-tasks.json").write_text(json.dumps(
            [task(500, 191, A, answers[A]), task(501, 191, B, answers[B])]))
        (old / "labelstudio" / "project-900-tasks.json").write_text(json.dumps(
            [task(1, 900, X, [_verdict("Correct", "2026-08-01T10:00:00Z")])]))
        (new / "labelstudio" / "project-3-tasks.json").write_text(json.dumps(
            [task(1, 3, A, answers[A]), task(2, 3, B, answers[B])]))
        for t in ("project_submissions", "project_items", "pools", "task_completions", "clinicians"):
            cur = c.execute(f"select * from {t}")
            cols = [d.name for d in cur.description]
            (new / "supabase" / f"{t}.json").write_text(json.dumps(
                [dict(zip(cols, r)) for r in cur.fetchall()], default=str))

        out = tmp_path / "out"
        rc = R.main(["--ls", f"gcp-2026={old}", "--ls", f"azure-2026={new}",
                     "--supabase", str(new), "--out", str(out)])
        report = (out / "report.md").read_text()
        assert rc == 0 and "items that differ: 0" in report
        assert "ambiguous (more than one item could match): 0" in report

        apply_native(c)
        before = _tables(c)
        c.execute((out / "forward.sql").read_text())

        task_no = {i: c.execute("select task_no from project_items where id = %s", (i,)).fetchone()[0] for i in (A, B, X)}
        refs = c.execute("select pool_id, ls_task_id from task_completions order by completed_at").fetchall()
        assert sorted(r[1] for r in refs if str(r[0]) == pool_p) == sorted([task_no[A], task_no[A], task_no[B]])
        # Q's completion said "task 1", which on the new instance is A. It belongs to Q's
        # pool, so it must have resolved to Q's item X, through the old instance.
        q_ref = [r[1] for r in refs if str(r[0]) == pool_q]
        assert q_ref == [task_no[X]]
        assert c.execute("select label->>'_task_no', label ? '_ls_task_id' from project_items where id = %s",
                         (A,)).fetchone() == (str(task_no[A]), False)
        assert c.execute("select count(*) from task_answers where project_id = %s", (P,)).fetchone()[0] == 3
        assert c.execute("select task_store from project_submissions where id = %s", (P,)).fetchone()[0] == "native"
        assert c.execute("select count(*) from ls_task_map where item_id = %s", (A,)).fetchone()[0] == 2  # both instances

        c.execute((out / "rollback.sql").read_text())
        assert _tables(c) == before
        assert c.execute("select count(*) from task_answers").fetchone()[0] == 0
        assert c.execute("select count(*) from ls_task_map").fetchone()[0] == 0
