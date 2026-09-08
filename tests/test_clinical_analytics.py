"""
Clinical analytics: triage direction/distance, severity, failure taxonomy, slices.

The property that matters clinically is DIRECTION: under-triage (the model was calmer
than the patient needed) and over-triage must never be summed into one "wrong" count,
and a missed emergency must be counted on its own. numpy recomputes the signed rank
differences a different way and asserts the module agrees.

Run: PYTHONPATH=. pytest tests/test_clinical_analytics.py
"""
import random

import numpy as np

from app.services.clinical_analytics import (
    compute, severity_summary, slice_summary, taxonomy_summary, triage_metrics)

ORDER = ["Self-care", "Routine", "Urgent", "Emergency"]
TRI_CFG = {"order": ORDER, "model_field": "triage", "expected_field": "expected_triage",
           "correct_field": "correct_triage"}
SEV_CFG = {"field": "severity", "order": ["Minor", "Moderate", "High", "Critical"]}


def _item(idx, model, expected, status="done", correct=None, **label):
    content = {"case_id": f"C{idx}", "triage": model, "expected_triage": expected}
    lbl = dict(label)
    if correct:
        lbl["correct_triage"] = correct
    return {"idx": idx, "status": status, "content": content, "label": lbl}


# ── Triage ───────────────────────────────────────────────────────────────────────

def test_triage_direction_and_distance_match_numpy():
    for seed in range(40):
        rng = random.Random(seed)
        pairs = [(rng.choice(ORDER), rng.choice(ORDER)) for _ in range(rng.randint(3, 25))]
        items = [_item(i, m, e) for i, (m, e) in enumerate(pairs)]
        got = triage_metrics(items, TRI_CFG)

        rank = {l: i for i, l in enumerate(ORDER)}
        d = np.array([rank[m] - rank[e] for m, e in pairs])
        assert got["compared"] == len(pairs)
        assert got["exact"] == int((d == 0).sum())
        assert got["under_triage"]["count"] == int((d < 0).sum())
        assert got["over_triage"]["count"] == int((d > 0).sum())
        assert got["accuracy"] == round(float((d == 0).mean()), 3)
        assert got["mean_levels_off"] == round(float(np.abs(d).mean()), 3)
        # A missed emergency is expected==top AND the model chose lower.
        exp_missed = sum(1 for m, e in pairs if e == "Emergency" and rank[m] < rank[e])
        assert got["missed_emergency"]["count"] == exp_missed


def test_under_and_over_triage_are_never_merged():
    # One case 2 levels too calm, one case 2 levels too alarmed. A plain accuracy score
    # reads these as "2 wrong"; they are opposite failures and must stay apart.
    items = [_item(0, "Self-care", "Urgent"), _item(1, "Emergency", "Routine")]
    r = triage_metrics(items, TRI_CFG)
    assert r["under_triage"]["count"] == 1 and r["over_triage"]["count"] == 1
    assert r["accuracy"] == 0.0
    assert r["mean_levels_off"] == 2.0


def test_missed_emergency_is_counted_separately_from_under_triage():
    items = [_item(0, "Routine", "Emergency"),   # missed emergency (also under-triage)
             _item(1, "Self-care", "Urgent")]    # under-triage only
    r = triage_metrics(items, TRI_CFG)
    assert r["under_triage"]["count"] == 2
    assert r["missed_emergency"]["count"] == 1
    assert r["missed_emergency"]["cases"][0]["case_id"] == "C0"


def test_reviewer_correction_overrides_the_authored_expected_level():
    # Case authored as Routine; the reviewer says it should have been Emergency.
    items = [_item(0, "Routine", "Routine", correct="Emergency")]
    r = triage_metrics(items, TRI_CFG)
    assert r["exact"] == 0 and r["missed_emergency"]["count"] == 1


def test_unfinished_and_unrankable_items_are_excluded_not_guessed():
    items = [_item(0, "Routine", "Routine"),
             _item(1, "Urgent", "Urgent", status="needs_adjudication"),
             _item(2, "Routine", "Routine", status="pending"),
             _item(3, "Wat", "Routine")]
    r = triage_metrics(items, TRI_CFG)
    assert r["compared"] == 1 and r["unrankable"] == 1


def test_triage_needs_a_declared_order():
    assert triage_metrics([_item(0, "a", "b")], {"order": []}) is None


# ── Severity ─────────────────────────────────────────────────────────────────────

def test_severity_distribution_keeps_scale_order_and_lists_serious_cases():
    items = [_item(i, "Routine", "Routine", severity=s, rationale=f"r{i}")
             for i, s in enumerate(["Minor", "Critical", "High", "Minor", "Moderate"])]
    r = severity_summary(items, SEV_CFG)
    assert list(r["distribution"]) == SEV_CFG["order"]      # scale order, not alphabetical
    assert r["distribution"]["Minor"] == 2 and r["distribution"]["Critical"] == 1
    assert r["serious"]["levels"] == ["High", "Critical"]
    assert {c["case_id"] for c in r["serious"]["cases"]} == {"C1", "C2"}


def test_off_scale_severity_is_surfaced_not_silently_counted():
    items = [_item(0, "Routine", "Routine", severity="Catastrophic")]
    r = severity_summary(items, SEV_CFG)
    assert r["off_scale"] == {"Catastrophic": 1} and r["graded"] == 0


# ── Taxonomy ─────────────────────────────────────────────────────────────────────

def test_taxonomy_counts_every_category_on_a_multi_select_item():
    items = [_item(0, "Routine", "Routine",
                   error_category=["Triage failure", "Missed red flag"], severity="Critical"),
             _item(1, "Routine", "Routine", error_category="Triage failure", severity="Minor")]
    r = taxonomy_summary(items, {"field": "error_category"}, SEV_CFG)
    assert r["distribution"] == {"Triage failure": 2, "Missed red flag": 1}
    assert r["ranked"][0] == "Triage failure"
    assert r["by_severity"]["Triage failure"] == {"Critical": 1, "Minor": 1}
    # The actionable number: how many of this mode were serious.
    assert r["serious_by_category"]["Missed red flag"] == 1
    assert r["serious_by_category"]["Triage failure"] == 1


# ── Slices ───────────────────────────────────────────────────────────────────────

def test_slices_split_accuracy_by_a_content_field():
    items = []
    for i in range(6):
        it = _item(i, "Routine", "Routine", verdict="Correct" if i % 2 else "Incorrect")
        it["content"]["clinical_domain"] = "Respiratory" if i < 4 else "Cardiac"
        items.append(it)
    r = slice_summary(items, ["clinical_domain"])
    assert r["clinical_domain"]["Respiratory"]["n"] == 4
    assert r["clinical_domain"]["Respiratory"]["accuracy"] == 0.5
    assert r["clinical_domain"]["Cardiac"]["n"] == 2


# ── Entry point ──────────────────────────────────────────────────────────────────

def test_a_project_that_declares_nothing_gets_nothing():
    items = [_item(0, "Routine", "Routine")]
    assert compute(items, None) is None
    assert compute(items, {}) is None


def test_compute_runs_only_the_declared_sections():
    items = [_item(0, "Self-care", "Emergency", severity="Critical",
                   error_category="Missed red flag")]
    only_triage = compute(items, {"triage": TRI_CFG})
    assert set(only_triage) == {"triage"}
    everything = compute(items, {"triage": TRI_CFG, "severity": SEV_CFG,
                                 "taxonomy": {"field": "error_category"},
                                 "slice_by": ["clinical_domain"]})
    # slices absent: no item carries clinical_domain, so the section is omitted rather
    # than reported as an empty table.
    assert set(everything) == {"triage", "severity", "taxonomy"}
    assert everything["triage"]["missed_emergency"]["count"] == 1


# ── Version comparison / regression ──────────────────────────────────────────────
# The asymmetry is the point: a release that fixes three cases and newly breaks one
# critical case must NOT read as a net improvement.

from app.services.clinical_analytics import compare_runs   # noqa: E402


def _run(cases):
    """cases: {case_id: (verdict, severity)} -> items. verdict None = unscored."""
    out = []
    for i, (cid, (verdict, sev)) in enumerate(cases.items()):
        label = {} if verdict is None else {"verdict": verdict, "severity": sev}
        out.append({"idx": i, "status": "pending" if verdict is None else "done",
                    "content": {"case_id": cid, "triage": "Routine"}, "label": label})
    return out


def test_regression_and_fix_are_classified_by_direction():
    a = _run({"C1": ("Correct", None), "C2": ("Incorrect", "High"),
              "C3": ("Correct", None), "C4": ("Incorrect", "Minor")})
    b = _run({"C1": ("Incorrect", "Critical"), "C2": ("Correct", None),
              "C3": ("Correct", None), "C4": ("Incorrect", "Minor")})
    r = compare_runs(a, b, "case_id", {"severity": SEV_CFG})
    assert r["regressed"]["count"] == 1 and r["regressed"]["cases"][0]["case_id"] == "C1"
    assert r["fixed"]["count"] == 1 and r["fixed"]["cases"][0]["case_id"] == "C2"
    assert r["still_passing"]["count"] == 1
    assert r["still_failing"]["count"] == 1


def test_a_serious_regression_blocks_even_when_the_pass_rate_improves():
    # Baseline 1/4 pass. Candidate 3/4 pass — a clear headline improvement — but a case
    # that used to pass now fails Critically. The gate must still say block.
    a = _run({"C1": ("Correct", None), "C2": ("Incorrect", "Minor"),
              "C3": ("Incorrect", "Minor"), "C4": ("Incorrect", "Minor")})
    b = _run({"C1": ("Incorrect", "Critical"), "C2": ("Correct", None),
              "C3": ("Correct", None), "C4": ("Correct", None)})
    r = compare_runs(a, b, "case_id", {"severity": SEV_CFG})
    assert r["pass_rate"]["delta"] > 0                      # headline improved
    assert r["verdict"]["recommendation"] == "block"        # gate still blocks
    assert r["verdict"]["serious_regressions"] == 1


def test_non_serious_regression_asks_for_review_and_clean_run_passes():
    a = _run({"C1": ("Correct", None), "C2": ("Correct", None)})
    minor = _run({"C1": ("Incorrect", "Minor"), "C2": ("Correct", None)})
    assert compare_runs(a, minor, "case_id", {"severity": SEV_CFG})["verdict"]["recommendation"] == "review"
    clean = _run({"C1": ("Correct", None), "C2": ("Correct", None)})
    assert compare_runs(a, clean, "case_id", {"severity": SEV_CFG})["verdict"]["recommendation"] == "pass"


def test_partial_counts_as_a_failure_for_regression_purposes():
    # A clinician found something clinically meaningful wrong. A benchmark must not pass it.
    a = _run({"C1": ("Correct", None)})
    b = _run({"C1": ("Partial", "Moderate")})
    r = compare_runs(a, b, "case_id", {"severity": SEV_CFG})
    assert r["regressed"]["count"] == 1


def test_cases_missing_from_a_run_are_listed_not_silently_dropped():
    # A benchmark that quietly loses a case can manufacture a clean scorecard.
    a = _run({"C1": ("Correct", None), "C2": ("Incorrect", "High")})
    b = _run({"C1": ("Correct", None), "C3": ("Correct", None)})
    r = compare_runs(a, b, "case_id", {"severity": SEV_CFG})
    assert r["matched"] == 1
    assert r["only_in_baseline"] == ["C2"] and r["only_in_candidate"] == ["C3"]


def test_unscored_cases_are_neither_fixed_nor_regressed():
    a = _run({"C1": ("Correct", None)})
    b = _run({"C1": (None, None)})
    r = compare_runs(a, b, "case_id", {"severity": SEV_CFG})
    assert r["regressed"]["count"] == 0 and r["fixed"]["count"] == 0
    assert r["unscored"] == [{"case_id": "C1", "baseline": "pass", "candidate": "unscored"}]


def test_regressions_are_ordered_worst_first():
    a = _run({f"C{i}": ("Correct", None) for i in range(3)})
    b = _run({"C0": ("Incorrect", "Minor"), "C1": ("Incorrect", "Critical"),
              "C2": ("Incorrect", "Moderate")})
    r = compare_runs(a, b, "case_id", {"severity": SEV_CFG})
    assert [c["severity"] for c in r["regressed"]["cases"]] == ["Critical", "Moderate", "Minor"]


def test_clinical_deltas_report_triage_movement_between_versions():
    def tri(cases):
        out = []
        for i, (cid, (model, exp)) in enumerate(cases.items()):
            out.append({"idx": i, "status": "done",
                        "content": {"case_id": cid, "triage": model, "expected_triage": exp},
                        "label": {"verdict": "Correct" if model == exp else "Incorrect"}})
        return out
    a = tri({"C1": ("Routine", "Emergency"), "C2": ("Routine", "Routine")})
    b = tri({"C1": ("Emergency", "Emergency"), "C2": ("Routine", "Routine")})
    r = compare_runs(a, b, "case_id", {"triage": TRI_CFG, "severity": SEV_CFG})
    d = r["clinical_deltas"]
    assert d["missed_emergency"] == {"baseline": 1, "candidate": 0, "delta": -1}
    assert d["triage_accuracy"]["delta"] == 0.5
