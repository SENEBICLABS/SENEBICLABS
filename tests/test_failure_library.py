"""
Clinical failure library: capture, status transitions, aggregation, replay.

The property that matters is the status history. `fixed` must only ever be written
because a later version actually passed the case, and a case that was fixed and fails
again must become `regressed`, not plain `open` — a fix that did not hold is the most
important thing the library can tell you, and collapsing it into `open` hides it.

Run: PYTHONPATH=. pytest tests/test_failure_library.py
"""
from app.services.failure_library import (
    FIXED, OPEN, REGRESSED, as_items, client_view, close, extract, merge, passed,
    patterns, resolved)


def _item(case_id, verdict, status="done", severity=None, category=None, domain=None, **content):
    c = {"case_id": case_id, "scenario": "s", "prediction": "p", **content}
    if domain:
        c["clinical_domain"] = domain
    label = {} if verdict is None else {"verdict": verdict, "rationale": f"why {case_id}"}
    if severity:
        label["severity"] = severity
    if category:
        label["error_category"] = category
    return {"idx": 0, "status": status, "content": c, "label": label}


# ── What counts as a failure ─────────────────────────────────────────────────────

def test_partial_is_a_failure_and_correct_is_not():
    assert passed(_item("C1", "Correct")) is True
    assert passed(_item("C2", "Incorrect")) is False
    # A clinician found something clinically meaningful wrong. The benchmark keeps it.
    assert passed(_item("C3", "Partial")) is False


def test_unscored_and_adjudication_items_are_neither():
    assert passed(_item("C1", None, status="pending")) is None
    assert passed(_item("C2", "Incorrect", status="needs_adjudication")) is None


def test_extract_records_only_scored_failures():
    items = [_item("C1", "Correct"),
             _item("C2", "Incorrect", severity="Critical", category="Missed red flag", domain="Neuro"),
             _item("C3", "Incorrect", status="needs_adjudication"),
             _item("C4", "Partial", severity="Minor")]
    got = extract(items, client_email="a@b.c", project_id="p1", model_version="v1")
    assert [f["case_key"] for f in got] == ["C2", "C4"]
    c2 = got[0]
    assert c2["severity"] == "Critical" and c2["error_category"] == "Missed red flag"
    assert c2["clinical_domain"] == "Neuro" and c2["model_version"] == "v1"
    # The full input is kept so the case can be replayed verbatim later.
    assert c2["content"]["scenario"] == "s"


def test_a_case_with_no_stable_id_cannot_enter_the_library():
    # Without a case id there is nothing to re-run or to join across versions.
    it = {"idx": 0, "status": "done", "content": {"scenario": "s"}, "label": {"verdict": "Incorrect"}}
    assert extract([it], client_email="a@b.c", project_id="p1") == []


def test_resolved_returns_only_passing_cases():
    items = [_item("C1", "Correct"), _item("C2", "Incorrect"), _item("C3", "Correct")]
    assert resolved(items) == {"C1", "C3"}


# ── Status transitions — the heart of it ─────────────────────────────────────────

def test_a_new_failure_starts_open_with_one_occurrence():
    inc = extract([_item("C1", "Incorrect", severity="High")],
                  client_email="a@b.c", project_id="p1", model_version="v1")[0]
    row = merge(None, inc, model_version="v1")
    assert row["status"] == OPEN and row["occurrences"] == 1
    assert row["first_seen"] == row["last_seen"]


def test_a_failure_seen_again_accumulates_rather_than_duplicating():
    inc = extract([_item("C1", "Incorrect", severity="High")],
                  client_email="a@b.c", project_id="p1", model_version="v2")[0]
    prev = {"status": OPEN, "occurrences": 3, "first_seen": "2026-01-01T00:00:00Z",
            "in_benchmark": True}
    row = merge(prev, inc, model_version="v2")
    assert row["occurrences"] == 4
    assert row["first_seen"] == "2026-01-01T00:00:00Z"   # original discovery preserved
    assert row["in_benchmark"] is True                    # promotion survives an update


def test_a_fixed_case_that_fails_again_is_regressed_not_open():
    # The single most important transition: losing this hides that a fix did not hold.
    inc = extract([_item("C1", "Incorrect", severity="Critical")],
                  client_email="a@b.c", project_id="p3", model_version="v3")[0]
    prev = {"status": FIXED, "occurrences": 1, "first_seen": "2026-01-01T00:00:00Z",
            "fixed_in_version": "v2", "in_benchmark": True}
    row = merge(prev, inc, model_version="v3")
    assert row["status"] == REGRESSED
    # The old "fixed in v2" claim is no longer true and must not linger.
    assert row["fixed_in_version"] is None


def test_closing_records_which_version_fixed_it_and_keeps_it_in_the_suite():
    row = close({"status": OPEN, "in_benchmark": True}, model_version="v4")
    assert row["status"] == FIXED and row["fixed_in_version"] == "v4"
    # in_benchmark is untouched: a fixed case is exactly the one to keep testing.
    assert "in_benchmark" not in row


# ── Aggregation ──────────────────────────────────────────────────────────────────

def _row(key, severity, category, domain, status=OPEN, occ=1, bench=False):
    return {"case_key": key, "severity": severity, "error_category": category,
            "clinical_domain": domain, "status": status, "occurrences": occ,
            "in_benchmark": bench}


def test_patterns_answers_the_cross_version_questions():
    rows = [
        _row("C1", "Critical", "Missed red flag", "Respiratory", occ=3, bench=True),
        _row("C2", "High", "Triage failure", "Respiratory"),
        _row("C3", "Minor", "Communication failure", "Cardiac", status=FIXED),
        _row("C4", "Critical", "Missed red flag", "Cardiac", status=REGRESSED, occ=2),
    ]
    p = patterns(rows)
    assert p["total"] == 4
    # "Most common high-severity failures in respiratory cases"
    assert p["serious_by_domain"]["Respiratory"] == 2
    assert list(p["by_error_category"])[0] == "Missed red flag"
    # Fixed cases drop out of what is still open.
    assert p["open_by_error_category"].get("Communication failure") is None
    assert p["by_status"] == {OPEN: 2, FIXED: 1, REGRESSED: 1}
    assert p["regressed"]["count"] == 1 and p["regressed"]["cases"][0]["case_key"] == "C4"
    assert p["in_benchmark"] == 1
    # Recurring cases, worst first — these are the entrenched problems.
    assert [r["case_key"] for r in p["recurring"]] == ["C1", "C4"]


def test_patterns_on_an_empty_library():
    assert patterns([]) == {"total": 0}


# ── Replay ───────────────────────────────────────────────────────────────────────

def test_benchmark_cases_replay_the_original_input_verbatim():
    rows = [{"case_key": "PMX-47",
             "content": {"case_id": "PMX-47", "scenario": "neck stiffness",
                         "expected_triage": "Emergency", "triage": "Self-care"}}]
    items = as_items(rows)
    assert items[0]["idx"] == 0 and items[0]["status"] == "pending"
    # A regression test is only a test if the input does not drift between runs.
    assert items[0]["content"]["scenario"] == "neck stiffness"
    assert items[0]["content"]["expected_triage"] == "Emergency"


def test_replay_supplies_a_case_id_when_the_stored_content_lacks_one():
    items = as_items([{"case_key": "PMX-9", "content": {"scenario": "x"}}])
    assert items[0]["content"]["case_id"] == "PMX-9"


def test_client_view_hides_internal_ids():
    row = {"id": "uuid-internal", "client_email": "a@b.c", "project_id": "p1",
           "case_key": "C1", "status": OPEN, "severity": "High"}
    v = client_view(row)
    assert v["case_key"] == "C1" and v["severity"] == "High"
    assert "id" not in v and "client_email" not in v and "project_id" not in v
