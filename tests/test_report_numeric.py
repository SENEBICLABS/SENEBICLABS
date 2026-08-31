"""
Numeric verification of the scoring math (app/services/report.py) with numpy as an
INDEPENDENT oracle. report.py is deliberately numpy-free at runtime; numpy lives
here in the tests only, recomputing accuracy / confusion / precision-recall-f1 a
different way (vectorised) and asserting the pure-Python report matches — across 50
random datasets plus edge cases the single hand-fixture in test_report.py can't reach.

Run: PYTHONPATH=. pytest tests/test_report_numeric.py
"""
import random

import numpy as np
import pytest

from app.services import report as R

CLASSES = ["Normal", "Pneumonia", "TB", "Effusion"]
VERDICTS = ["Correct", "Incorrect", "Partially correct"]


def _item(i, pred, verdict, correct=None):
    label = {"verdict": verdict}
    if correct is not None:
        label["correct_label"] = correct
    return {"idx": i, "status": "done",
            "content": {"image": f"img{i}", "prediction": pred}, "label": label}


def _rand_items(n, seed, classes=CLASSES):
    rng = random.Random(seed)
    items = []
    for i in range(n):
        pred = rng.choice(classes)
        verdict = rng.choice(VERDICTS)
        correct = None if verdict == "Correct" else rng.choice(classes)  # incorrect/partial need a corrected label
        items.append(_item(i, pred, verdict, correct))
    return items


def _numpy_oracle(items, classes):
    """Recompute the scorecard the numpy way. Ground truth: Correct -> the model's
    own prediction; Incorrect/Partial -> the corrected label."""
    cls = sorted(set(classes))
    idx = {c: k for k, c in enumerate(cls)}
    K = len(cls)

    def gt(it):
        v = it["label"]["verdict"].lower()
        return it["content"]["prediction"] if v.startswith("correct") else it["label"]["correct_label"]

    preds = np.array([idx[it["content"]["prediction"]] for it in items], dtype=int)
    gts = np.array([idx[gt(it)] for it in items], dtype=int)
    conf = np.zeros((K, K), dtype=int)
    np.add.at(conf, (preds, gts), 1)                      # conf[pred, true]

    diag = np.diag(conf)
    row = conf.sum(axis=1)    # predicted as c
    col = conf.sum(axis=0)    # true class c  (= support)
    tp, fp, fn, support = diag, row - diag, col - diag, col

    per_class = {}
    for c, i in idx.items():
        p = tp[i] / (tp[i] + fp[i]) if (tp[i] + fp[i]) else None
        r = tp[i] / (tp[i] + fn[i]) if (tp[i] + fn[i]) else None
        f = None if (p is None or r is None) else (0.0 if p + r == 0 else 2 * p * r / (p + r))
        per_class[c] = {"support": int(support[i]), "tp": int(tp[i]), "fp": int(fp[i]),
                        "fn": int(fn[i]), "precision": p, "recall": r, "f1": f}

    n = len(items)
    n_correct = sum(1 for it in items if it["label"]["verdict"].lower().startswith("correct"))
    accuracy = n_correct / n if n else None
    return cls, conf, accuracy, per_class


def _assert_matches(rep, cls, conf, accuracy, per_class):
    assert rep["accuracy"]["value"] == pytest.approx(accuracy)
    assert rep["accuracy"]["assessable"] == int(conf.sum())
    assert rep["confusion_matrix"]["labels"] == cls
    M = rep["confusion_matrix"]["matrix"]
    for i in range(len(cls)):
        for j in range(len(cls)):
            assert M[i][j] == int(conf[i, j])
    for c in cls:
        got, exp = rep["per_class"][c], per_class[c]
        assert (got["support"], got["tp"], got["fp"], got["fn"]) == \
               (exp["support"], exp["tp"], exp["fp"], exp["fn"])
        for k in ("precision", "recall", "f1"):
            if exp[k] is None:
                assert got[k] is None, f"{c}.{k}: expected None, got {got[k]}"
            else:
                assert got[k] == pytest.approx(exp[k]), f"{c}.{k}"


def test_scoring_matches_numpy_oracle_over_random_datasets():
    for seed in range(50):
        n = random.Random(seed).randint(1, 40)
        items = _rand_items(n, seed)
        rep = R.compute_report(items, CLASSES)
        _assert_matches(rep, *_numpy_oracle(items, CLASSES))


def test_all_correct_is_accuracy_one():
    items = [_item(i, c, "Correct") for i, c in enumerate(["Normal", "Pneumonia", "Normal"])]
    rep = R.compute_report(items, CLASSES)
    assert rep["accuracy"]["value"] == 1.0
    _assert_matches(rep, *_numpy_oracle(items, CLASSES))


def test_all_wrong_is_accuracy_zero():
    items = [_item(0, "Normal", "Incorrect", "TB"),
             _item(1, "Pneumonia", "Incorrect", "Normal")]
    rep = R.compute_report(items, CLASSES)
    assert rep["accuracy"]["value"] == 0.0
    _assert_matches(rep, *_numpy_oracle(items, CLASSES))


def test_absent_class_has_none_metrics():
    # Effusion never appears -> support 0, precision/recall/f1 all None (n/a, not zero)
    items = [_item(0, "Normal", "Correct"), _item(1, "Pneumonia", "Correct")]
    rep = R.compute_report(items, CLASSES)
    assert rep["per_class"]["Effusion"]["support"] == 0
    assert rep["per_class"]["Effusion"]["precision"] is None
    _assert_matches(rep, *_numpy_oracle(items, CLASSES))
