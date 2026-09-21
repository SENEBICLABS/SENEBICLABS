"""
Failure capture across runs, and the status a client sees while review is under way.

Capture: the second evaluation of a model is the normal case — some failures are new, some
are repeats already in the library. Those go in one upsert, and PostgREST sends a bulk
write with the union of every row's keys, filling any key a row lacks with NULL unless told
to use the column default. A new failure has no id yet, so it got a NULL id and the whole
capture failed. The fake below enforces that PostgREST rule, so this fails if it returns.

Run: PYTHONPATH=. pytest tests/test_capture_and_status.py
"""
import uuid
from unittest.mock import patch

from app.api.v1 import project as proj


class _Res:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.filters, self.window = db, name, {}, None

    def select(self, *_a, **_k): return self
    def order(self, *_a, **_k): return self
    def limit(self, _n): return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def range(self, s, e):
        self.window = (s, e)
        return self

    def upsert(self, rows, on_conflict="", default_to_null=True):
        self.db.upsert(self.name, rows, on_conflict, default_to_null)
        return self

    def execute(self):
        rows = [r for r in self.db.rows.get(self.name, [])
                if all(r.get(k) == v for k, v in self.filters.items())]
        if self.window:
            rows = rows[self.window[0]: self.window[1] + 1]
        return _Res(rows)


class _DB:
    """A Postgres table behind PostgREST, for the two behaviours that matter here."""

    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        return _Q(self, name)

    def upsert(self, name, rows, on_conflict, default_to_null):
        keys = set().union(*(r.keys() for r in rows))
        table = self.rows.setdefault(name, [])
        conflict = on_conflict.split(",")
        for r in rows:
            r = dict(r)
            for k in keys - set(r):
                if default_to_null:
                    r[k] = None                    # PostgREST's default for a missing key
            if r.get("id", "default") is None:
                raise Exception('null value in column "id" violates not-null constraint')
            r.setdefault("id", str(uuid.uuid4()))  # the column default
            match = next((t for t in table if all(t.get(c) == r.get(c) for c in conflict)), None)
            if match:
                match.update(r)
            else:
                table.append(r)


EMAIL = "client@example.com"
EC = {"schema": {"case_id_field": "case_id"}, "model_version": "v2"}


def _item(idx, case, verdict, severity=None):
    return {"project_id": "p2", "idx": idx, "status": "done",
            "content": {"case_id": case, "clinical_domain": "Cardiology"},
            "label": {"verdict": verdict, "severity": severity}}


def _capture(db):
    with patch.object(proj, "get_client", return_value=db), \
         patch.object(proj, "_api_client_email", return_value=EMAIL):
        return proj.api_capture_failures(proj.CaptureIn(project_id="p2"), authorization="Bearer k")


def test_a_run_with_new_and_repeat_failures_is_captured():
    known = {"id": "f-1", "client_email": EMAIL, "case_key": "ONC-X1", "status": "open",
             "severity": "Critical", "occurrences": 1, "project_id": "p1", "model_version": "v1",
             "first_seen": "2026-09-01T00:00:00Z", "last_seen": "2026-09-01T00:00:00Z",
             "content": {}, "output": {}, "assessment": {}}
    db = _DB({
        "project_submissions": [{"id": "p2", "email": EMAIL, "eval_config": EC}],
        "project_items": [_item(0, "ONC-X1", "Incorrect", "Critical"),    # repeat
                          _item(1, "CARD-Q1", "Incorrect", "Critical")],  # new
        "clinical_failures": [known],
    })
    out = _capture(db)
    assert out["recorded"] == 2
    lib = {r["case_key"]: r for r in db.rows["clinical_failures"]}
    assert lib["ONC-X1"]["id"] == "f-1" and lib["ONC-X1"]["occurrences"] == 2
    assert lib["CARD-Q1"]["id"] and lib["CARD-Q1"]["severity"] == "Critical"


def _results_status(statuses, stage="submitted"):
    db = _DB({
        "project_submissions": [{"id": "p", "email": EMAIL, "company": "X", "stage": stage,
                                 "eval_config": {}}],
        "project_items": [{"project_id": "p", "id": str(i), "idx": i, "status": s}
                          for i, s in enumerate(statuses)],
    })
    with patch.object(proj, "get_client", return_value=db), \
         patch.object(proj, "_api_client_email", return_value=EMAIL), \
         patch.object(proj, "_kick_sync"):
        return proj.api_results("p", authorization="Bearer k")


def test_status_is_received_until_a_clinician_starts():
    r = _results_status(["pending", "queued"])
    assert r["status"] == "received" and r["done"] == 0


def test_status_is_in_review_once_work_starts_without_a_manual_stage_move():
    # The live gap: 15 of 16 reviewed, and the client was still told "received".
    assert _results_status(["done", "pending"])["status"] == "in_review"
    assert _results_status(["in_progress", "pending"])["status"] == "in_review"
    assert _results_status(["needs_adjudication", "pending"])["status"] == "in_review"


def test_an_operator_stage_move_still_means_in_review():
    assert _results_status(["pending"], stage="production")["status"] == "in_review"
