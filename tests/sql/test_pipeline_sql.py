"""
One answer's whole journey, for every template, with the real code at each step:

  1. the workforce app's real translator wrote it (its golden file, tests/wire-format.test.ts)
  2. the real submit function stored it, in Postgres, from three clinicians
  3. the real outbox functions handed it to HEALTH's real drain
  4. HEALTH's real parser, consensus, report and client delivery read it

The only thing simulated is PostgREST: the drain reads rows that were first read out of
Postgres, the way the API would return them.
"""
import copy
import json
import uuid
from pathlib import Path

import psycopg
import pytest

from _fakedb import FakeDB
from conftest import WORKFORCE
from app.api.v1 import project as proj
from app.services import report as report_svc
from app.services import tasks

GOLDEN = WORKFORCE / "tests" / "fixtures" / "wire-answers.json"
TEMPLATES = Path(__file__).resolve().parents[1] / "fixtures" / "template-configs.json"

if not GOLDEN.exists():
    pytest.skip(f"workforce golden file not found at {GOLDEN}", allow_module_level=True)

WIRE = json.loads(GOLDEN.read_text())["templates"]
CONFIGS = json.loads(TEMPLATES.read_text())["templates"]
CASES = [(name, s) for name in sorted(WIRE) for s in ("full", "minimal")]


def _rows(c, sql, *args):
    cur = c.execute(sql, args)
    cols = [d.name for d in cur.description]
    # Through JSON, as PostgREST hands rows over: uuids and timestamps as strings.
    return json.loads(json.dumps([dict(zip(cols, r)) for r in cur.fetchall()], default=str))


def _seed(c, ec, content):
    pid = str(uuid.uuid4())
    c.execute("insert into project_submissions (id, name, email, company, description, eval_config, task_store)"
              " values (%s, 'P', 'c@x.com', 'Co', 'd', %s, 'native')", (pid, json.dumps(ec)))
    item = c.execute("insert into project_items (project_id, idx, content, status) values (%s, 0, %s, 'queued')"
                     " returning task_no", (pid, json.dumps(content))).fetchone()[0]
    pool = str(uuid.uuid4())
    c.execute("insert into pools (id, name, ls_project_id, calibration_items, project_id)"
              " values (%s, 'Pool', 0, '[]', %s)", (pool, pid))
    docs = []
    for n in range(3):
        d = str(uuid.uuid4())
        c.execute("insert into clinicians (id, name, email, access_code) values (%s, 'Dr', %s, %s)",
                  (d, f"doc{n}@x.com", d))
        c.execute("insert into pool_eligibility (clinician_id, pool_id, eligible) values (%s, %s, true)", (d, pool))
        docs.append(d)
    return pid, item, pool, docs


def _snapshot(c, conn_rpcs):
    tables = ["project_submissions", "project_items", "task_answers", "clinicians", "pools",
              "review_items", "task_completions", "answer_events"]
    db = FakeDB({t: _rows(c, f"select * from {t}") for t in tables}, rpcs=conn_rpcs)
    db.rows["audit_events"] = []
    return db


def _real_outbox(c):
    """The drain's database calls go to the real functions in Postgres."""
    return {
        "claim_answer_events": lambda p: _rows(
            c, "select * from claim_answer_events(%s, %s)", p["p_limit"], p["p_stale_seconds"]),
        "finish_answer_event": lambda p: c.execute(
            "select finish_answer_event(%s, %s, %s, %s)",
            (p["p_event_id"], p["p_ok"], p.get("p_upto_event_id"), p.get("p_error"))) and None,
    }


READERS = 3


def _expected_value(ftype, answer, finding=None):
    """What consensus makes of three identical answers, by HEALTH's existing rules
    (scoring.consensus): highlights are never voted, every reader's are kept, so three
    identical spans are a list of three; everything else is the agreed value."""
    if ftype == "spans":
        s = answer[0]
        one = {"labels": [s["label"]], "start": s["start"], "end": s["end"], "text": s["text"]}
        return [one] * READERS
    if ftype == "structured":
        return {"present": answer == "Yes", "finding": finding}
    return answer


@pytest.mark.parametrize("name,which", CASES)
def test_an_answer_survives_from_the_form_to_the_client(db, name, which):
    ec = copy.deepcopy(CONFIGS[name])
    ec.update({"reviewers_per_item": 3})
    wire = WIRE[name]
    case = wire["sets"][which]
    pid, task_no, pool, docs = _seed(db, ec, wire["content"])

    # 2. Three clinicians submit the translator's exact output.
    for d in docs:
        out = db.execute("select native_submit_judgement(%s, %s, %s, %s, %s)",
                         (d, pool, task_no, json.dumps(case["result"]), json.dumps(case["answers"]))).fetchone()[0]
        assert out == "claimed"
    stored = _rows(db, "select result from task_answers where task_no = %s order by id", task_no)
    assert [s["result"] for s in stored] == [case["result"]] * 3       # byte for byte

    # 3 and 4. HEALTH drains the real outbox and scores what Postgres stored.
    native = _snapshot(db, _real_outbox(db))
    assert tasks.process_events(native) == {"processed": 1, "failed": 0}
    assert _rows(db, "select status from answer_events") == [{"status": "done"}] * 3
    item = native.rows["project_items"][0]
    assert item["status"] == "done"
    label = item["label"]

    # Every visible answered field is in the stored label, with its value intact.
    for field, ftype in case["visible"]:
        if field not in case["answers"]:
            if ftype == "structured":
                # Left blank, the form records "No", and consensus
                # makes a not-present answer's finding null.
                assert label[field] == {"present": False, "finding": None}, field
            continue
        expected = _expected_value(ftype, case["answers"][field], case["answers"].get(f"{field}_finding"))
        assert label[field] == expected, field

    # The report reads it, and the client deliverable names no clinician.
    classes = (ec.get("schema") or {}).get("classes")
    report_svc.compute_report([item], classes)
    assert label["_task_no"] == task_no
    assert "doc0@x.com" not in json.dumps(proj._client_item(item))


def test_the_agent_trace_step_number_reaches_the_report_inputs(db):
    """The audit's headline loss, end to end: first_failed_step from the form to the label."""
    ec = copy.deepcopy(CONFIGS["agent_trace_eval"])
    ec["reviewers_per_item"] = 1
    wire = WIRE["agent_trace_eval"]
    _, task_no, pool, docs = _seed(db, ec, wire["content"])
    db.execute("select native_submit_judgement(%s, %s, %s, %s, %s)",
               (docs[0], pool, task_no, json.dumps(wire["sets"]["full"]["result"]),
                json.dumps(wire["sets"]["full"]["answers"])))
    native = _snapshot(db, _real_outbox(db))
    tasks.process_events(native)
    assert native.rows["project_items"][0]["label"]["first_failed_step"] == 3
