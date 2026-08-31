"""
Data-quality validators catch the defects they target and pass clean data.
Run:  PYTHONPATH=. python3 tests/test_data_quality.py   (self-running)
  or: pytest tests/test_data_quality.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
from scripts.data_quality import validate_batch, validate_deliverable

REQUIRED = ["case_id", "prompt", "output", "prediction"]
CLASSES  = ["Normal", "Airway", "Parenchyma", "Vascular", "Pleural"]
VERDICTS = ["correct", "incorrect", "partially correct"]


def _failed(report):
    return {c["expectation"] for c in report["checks"] if not c["passed"]}


def test_ingestion_clean_passes():
    df = pd.DataFrame([
        {"case_id": "C1", "prompt": "a", "output": "b", "prediction": "Normal"},
        {"case_id": "C2", "prompt": "c", "output": "d", "prediction": "Parenchyma"},
    ])
    rep = validate_batch(df, REQUIRED, classes=CLASSES, class_column="prediction", case_id_field="case_id")
    assert rep["passed"], _failed(rep)


def test_ingestion_catches_defects():
    df = pd.DataFrame([
        {"case_id": "C1", "prompt": "a", "output": "b", "prediction": "Normal"},
        {"case_id": "C1", "prompt": None, "output": "", "prediction": "Bogus"},  # dup, null, empty, bad class
    ])
    rep = validate_batch(df, REQUIRED, classes=CLASSES, class_column="prediction", case_id_field="case_id")
    failed = _failed(rep)
    assert not rep["passed"]
    assert "expect_column_values_to_not_be_null" in failed
    assert "expect_column_value_lengths_to_be_between" in failed
    assert "expect_column_values_to_be_in_set" in failed
    assert "expect_column_values_to_be_unique" in failed


def test_deliverable_clean_passes():
    df = pd.DataFrame([
        {"verdict": "correct", "status": "done", "agreement": 1.0, "correct_label": None},
        {"verdict": "incorrect", "status": "done", "agreement": 0.667, "correct_label": "Parenchyma"},
    ])
    rep = validate_deliverable(df, VERDICTS, CLASSES)
    assert rep["passed"], _failed(rep)


def test_deliverable_catches_defects():
    df = pd.DataFrame([
        {"verdict": "maybe", "status": "in_progress", "agreement": 1.3, "correct_label": "Bogus"},
        {"verdict": "correct", "status": "done", "agreement": 1.0, "correct_label": None},
    ])
    rep = validate_deliverable(df, VERDICTS, CLASSES)
    assert not rep["passed"]


def test_deliverable_blocks_internal_key_leak():
    # gold answers and reviewer-level detail/identities must never ship to a client
    df = pd.DataFrame([
        {"verdict": "correct", "status": "done", "agreement": 1.0,
         "_gold_expected": {"verdict": "correct"},
         "_annotations": [{"by": "clinicianA@example.com"}]},
    ])
    rep = validate_deliverable(df, VERDICTS, CLASSES)
    assert not rep["passed"]
    assert "no_internal_keys_leaked" in _failed(rep)


if __name__ == "__main__":
    for _n, _f in list(globals().items()):
        if _n.startswith("test_") and callable(_f):
            _f(); print("ok", _n)
    print("all data-quality tests passed")
