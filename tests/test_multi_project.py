"""
Runs split across several projects, and multi-step agent traces.

- /compare pools several projects per side, and refuses a case id that appears twice on
  one side rather than silently scoring whichever copy was read last.
- /benchmark/run seeds one run per task config, compares each against every project its
  cases came from, and starts clean: no inherited ingest keys, sign-off or webhook outcome.
- Agent traces sent as a list of steps reach the clinician as readable numbered text, and
  the first-failed-step field is summarised.

Run: PYTHONPATH=. pytest tests/test_multi_project.py
"""
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from _fakedb import FakeDB
from app.api.v1 import project as proj
from app.services import clinical_analytics as ca
from app.services import render
from app.services.scoring import parse_result as _parse_result
from app.services import templates as T

EMAIL = "c@x.com"
SEV = {"order": ["Minor", "Moderate", "High", "Critical"]}


def _item(pid, idx, case, verdict, severity=None, domain="Cardiology"):
    return {"project_id": pid, "idx": idx, "status": "done",
            "content": {"case_id": case, "clinical_domain": domain},
            "label": {"verdict": verdict, "severity": severity}}


def _sub(pid, ec=None, email=EMAIL):
    ec = ec or {"schema": {"case_id_field": "case_id"}, "analytics": {"severity": SEV}}
    return {"id": pid, "email": email, "stage": "delivered", "eval_config": ec}


def _call(fn, db, *a, **k):
    with patch.object(proj, "get_client", return_value=db), \
         patch.object(proj, "_api_client_email", return_value=EMAIL), \
         patch.object(proj, "_kick_sync"):
        return fn(*a, **k)


# ── compare ──────────────────────────────────────────────────────────────────

def _worlds_db():
    """v1 graded per specialty in two projects; v2 in one project."""
    return FakeDB({
        "project_submissions": [_sub("card1"), _sub("onc1"), _sub("all2")],
        "project_items": [
            _item("card1", 0, "CARD-1", "Correct"),
            _item("card1", 1, "CARD-2", "Incorrect", "High"),
            _item("onc1", 0, "ONC-1", "Incorrect", "Critical", "Oncology"),
            _item("all2", 0, "CARD-1", "Incorrect", "Critical"),
            _item("all2", 1, "CARD-2", "Correct"),
            _item("all2", 2, "ONC-1", "Correct", domain="Oncology"),
        ],
    })


def test_compare_pools_several_projects_on_one_side():
    out = _call(proj.api_compare, _worlds_db(), "card1,onc1", "all2", authorization="k")
    c = out["comparison"]
    assert c["matched"] == 3                                   # both worlds, not just the first
    assert {x["case_id"] for x in c["fixed"]["cases"]} == {"CARD-2", "ONC-1"}
    assert [x["case_id"] for x in c["regressed"]["cases"]] == ["CARD-1"]
    assert c["verdict"]["recommendation"] == "block"
    assert c["baseline"]["project_ids"] == ["card1", "onc1"]


def test_a_single_project_per_side_still_works_as_before():
    out = _call(proj.api_compare, _worlds_db(), "card1", "all2", authorization="k")
    assert out["comparison"]["matched"] == 2
    assert out["comparison"]["baseline"]["project_id"] == "card1"


def test_a_case_id_twice_on_one_side_is_refused():
    db = _worlds_db()
    db.rows["project_items"].append(_item("onc1", 1, "CARD-1", "Correct"))
    with pytest.raises(HTTPException) as e:
        _call(proj.api_compare, db, "card1,onc1", "all2", authorization="k")
    assert e.value.status_code == 422 and "CARD-1" in e.value.detail


def test_every_project_on_either_side_must_be_the_callers():
    db = _worlds_db()
    db.rows["project_submissions"].append(_sub("theirs", email="other@x.com"))
    with pytest.raises(HTTPException) as e:
        _call(proj.api_compare, db, "card1,theirs", "all2", authorization="k")
    assert e.value.status_code == 403


def test_the_same_project_cannot_be_on_both_sides():
    with pytest.raises(HTTPException) as e:
        _call(proj.api_compare, _worlds_db(), "card1,onc1", "onc1", authorization="k")
    assert e.value.status_code == 422


# ── benchmark run ────────────────────────────────────────────────────────────

def _failure(case, pid, severity="High"):
    return {"id": f"f-{case}", "client_email": EMAIL, "case_key": case, "project_id": pid,
            "status": "open", "severity": severity, "in_benchmark": True,
            "last_seen": "2026-09-21T00:00:00Z",
            "content": {"case_id": case, "scenario": "s", "prediction": "p"}}


def _stateful(template):
    ec = T.config_from_template(template)
    ec.update({"_ingest_keys": ["k1"], "_ready_for_delivery": "2026-09-01",
               "_webhook_last": {"delivered": False}, "_ls_review_project_id": 5,
               "_webhook_url": "https://client/hook", "_webhook_secret": "s"})
    return ec


def test_one_run_covers_every_project_graded_the_same_way():
    db = FakeDB({
        "project_submissions": [_sub("card1", _stateful("grounding_eval")),
                                _sub("onc1", _stateful("grounding_eval"))],
        "clinical_failures": [_failure("CARD-2", "card1"), _failure("ONC-1", "onc1")],
    })
    out = _call(proj.api_benchmark_run, db, proj.BenchmarkRunIn(model_version="v2"), authorization="k")
    assert len(out["runs"]) == 1 and out["cases"] == 2
    assert set(out["compare_with"].split(",")) == {"card1", "onc1"}
    new = [s for s in db.rows["project_submissions"] if s["id"] == out["project_id"]][0]["eval_config"]
    # Clean operational state; same client endpoint; the new version.
    for k in ("_ingest_keys", "_ready_for_delivery", "_webhook_last", "_ls_review_project_id"):
        assert k not in new, k
    assert new["_webhook_url"] == "https://client/hook" and new["model_version"] == "v2"


def test_cases_graded_differently_get_a_run_each():
    db = FakeDB({
        "project_submissions": [_sub("g1", _stateful("grounding_eval")),
                                _sub("t1", _stateful("triage_eval"))],
        "clinical_failures": [_failure("G-1", "g1"), _failure("G-2", "g1"), _failure("T-1", "t1")],
    })
    out = _call(proj.api_benchmark_run, db, proj.BenchmarkRunIn(model_version="v2"), authorization="k")
    runs = {r["compare_with"]: r for r in out["runs"]}
    assert set(runs) == {"g1", "t1"} and runs["g1"]["cases"] == 2 and runs["t1"]["cases"] == 1
    assert "project_id" not in out                  # no single run to name at the top level
    # Each run carries its own group's cases and its own group's rubric.
    for r in out["runs"]:
        items = [i for i in db.rows["project_items"] if i["project_id"] == r["project_id"]]
        assert len(items) == r["cases"]


def test_a_case_whose_source_project_was_deleted_stays_in_the_suite():
    db = FakeDB({
        "project_submissions": [_sub("g1", _stateful("grounding_eval"))],
        "clinical_failures": [_failure("G-1", "g1"), _failure("OLD-1", "gone")],
    })
    out = _call(proj.api_benchmark_run, db, proj.BenchmarkRunIn(model_version="v2"), authorization="k")
    assert out["cases"] == 2


# ── agent traces ─────────────────────────────────────────────────────────────

def test_a_trace_sent_as_steps_is_shown_as_numbered_text():
    trace = [{"type": "tool_call", "tool": "search", "args": {"q": "dose"}},
             {"type": "observation", "text": "Adult guideline"},
             "Final: give 500mg"]
    shown = render.render_value(trace)
    assert shown.startswith("Step 1\n   type: tool_call\n   tool: search\n   args: {\"q\": \"dose\"}")
    assert "Step 2\n   type: observation" in shown and shown.endswith("Step 3: Final: give 500mg")
    # The database reorders JSON keys; what a step is still leads what it contains.
    stored = [{"text": "Adult guideline", "type": "observation"}]
    assert render.render_value(stored) == "Step 1\n   type: observation\n   text: Adult guideline"
    # The stored content keeps the client's structure; only what is shown is rendered.
    data = render.render_content({"trace": trace, "case_id": "A-1", "_gold_expected": "x"})
    assert data == {"trace": shown, "case_id": "A-1"}


def test_a_number_answer_is_parsed():
    assert _parse_result([{"from_name": "first_failed_step", "value": {"number": 2}}])["first_failed_step"] == 2


def test_step_summary_reports_where_trajectories_first_break():
    items = [{"status": "done", "label": {"first_failed_step": s}} for s in (2, 2, 4, "3")]
    items += [{"status": "done", "label": {"first_failed_step": "abc"}},
              {"status": "pending", "label": {"first_failed_step": 9}},
              {"status": "done", "label": {}}]
    r = ca.step_summary(items, {"field": "first_failed_step"})
    assert r["n"] == 4 and r["median_first_failed_step"] == 2.5
    assert r["distribution"] == {"2": 2, "3": 1, "4": 1} and r["unusable"] == 1


def test_a_highlighted_span_keeps_its_tag_and_position():
    # A span arrives with the highlighted `text` alongside its `labels`. It was
    # read as a plain text answer, dropping the tag and position of every highlight.
    span = {"from_name": "error_spans",
            "value": {"start": 10, "end": 16, "text": "3 days", "labels": ["Wrong dose / guideline"]}}
    got = _parse_result([span])["error_spans"]
    assert got == {"labels": ["Wrong dose / guideline"], "start": 10, "end": 16, "text": "3 days"}
    # A plain text answer is still text.
    assert _parse_result([{"from_name": "fix", "value": {"text": ["Use 5 days"]}}])["fix"] == "Use 5 days"


def _config_db():
    return FakeDB({"project_submissions": [{"id": "p1", "task_store": "native",
                                            "eval_config": {"schema": {"fields": {}}}}],
                   "project_items": [], "audit_events": []})


def _set_config(db, **patch):
    cfg = {"purpose": "evaluate", "schema": {"input": "text",
           "context": [{"key": "q", "label": "Q"}],
           "fields": {"verdict": {"type": "single", "options": ["Correct", "Incorrect"], "required": True}}}}
    with patch_ctx(db):
        return proj.admin_set_eval_config(proj.EvalConfigIn(project_id="p1", eval_config=cfg),
                                          x_admin_key="k")


def patch_ctx(db):
    from contextlib import ExitStack
    st = ExitStack()
    st.enter_context(patch.object(proj, "get_client", return_value=db))
    st.enter_context(patch.object(proj.settings, "ADMIN_API_KEY", "k"))
    return st


def test_saving_a_config_is_what_the_clinicians_form_reads():
    # The clinician platform renders the form from this config, so there is no copy to
    # push and nothing that can be left showing the old questions.
    db = _config_db()
    with patch("httpx.post") as called:
        out = _set_config(db)
    called.assert_not_called()
    assert db.rows["project_submissions"][0]["eval_config"]["schema"]["context"] == [{"key": "q", "label": "Q"}]
    assert out.message == "Config saved. The clinicians' form now reads this config."
