"""
Numeric verification of consensus (app/api/v1/ls.py :: _consensus) with numpy as
an independent oracle. This is the OTHER computation that decides what ships: the
majority verdict across reviewers, the agreement fraction, whether they disagreed
(which gates adjudication), and structured-field (present/finding) majority.

numpy recomputes the mode/counts a different way and asserts _consensus matches,
across 50 random reviewer sets plus the tie/split edge cases.

Run: PYTHONPATH=. pytest tests/test_consensus_numeric.py
"""
import random

import numpy as np

from app.api.v1.ls import _consensus

VERDICTS = ["accurate", "inaccurate", "partial"]


def _agreement_oracle(verdicts):
    """(agreement rounded to 3, disagreed, set of values achieving the top count)."""
    n = len(verdicts)
    vals, counts = np.unique(np.array(verdicts), return_counts=True)
    top = int(counts.max())
    winners = set(vals[counts == top].tolist())   # every value tied for the top count
    return round(top / n, 3), (top * 2 <= n), winners


def test_agreement_and_disagreed_match_numpy_over_random_reviewer_sets():
    for seed in range(50):
        rng = random.Random(seed)
        n = rng.randint(2, 7)
        verdicts = [rng.choice(VERDICTS) for _ in range(n)]
        labels = [{"verdict": v} for v in verdicts]
        cons, agreement, disagreed = _consensus(labels)
        exp_agree, exp_dis, winners = _agreement_oracle(verdicts)
        assert agreement == exp_agree, (verdicts, agreement, exp_agree)
        assert disagreed == exp_dis, (verdicts, disagreed, exp_dis)
        assert cons["verdict"] in winners      # the consensus pick is a genuine top-count value


def test_agreement_edge_cases():
    # unanimous -> agreement 1.0, not disagreed
    c, a, d = _consensus([{"verdict": "x"}] * 3)
    assert a == 1.0 and d is False and c["verdict"] == "x"

    # 2 of 3 majority -> 0.667, not disagreed
    c, a, d = _consensus([{"verdict": "x"}, {"verdict": "x"}, {"verdict": "y"}])
    assert a == round(2 / 3, 3) and d is False and c["verdict"] == "x"

    # three-way split of 3 -> 0.333, disagreed (this is what triggers adjudication)
    c, a, d = _consensus([{"verdict": "x"}, {"verdict": "y"}, {"verdict": "z"}])
    assert a == round(1 / 3, 3) and d is True

    # even 2-2 split -> 0.5, disagreed (no STRICT majority: top*2 <= n)
    c, a, d = _consensus([{"verdict": "x"}, {"verdict": "x"}, {"verdict": "y"}, {"verdict": "y"}])
    assert a == 0.5 and d is True


def test_structured_present_and_finding_majority():
    for seed in range(30):
        rng = random.Random(seed)
        n = rng.randint(2, 7)
        flags = [rng.random() < 0.5 for _ in range(n)]
        labels = [{"verdict": "x",
                   "critical_miss": {"present": p, "finding": ("Nodule" if p else None)}}
                  for p in flags]
        cons, _a, _d = _consensus(labels)
        exp_present = (sum(flags) * 2) > n            # structured present needs a STRICT majority
        cm = cons["critical_miss"]
        assert cm["present"] == exp_present, (flags, cm)
        assert cm["finding"] == ("Nodule" if exp_present else None)


def test_free_text_is_surfaced_not_voted():
    # two reviewers write different corrections -> both kept (never majority-collapsed)
    labels = [{"verdict": "inaccurate", "correction": "It is on the right."},
              {"verdict": "inaccurate", "correction": "The side is wrong; it's right-sided."}]
    cons, a, d = _consensus(labels, text_fields={"correction"})
    assert isinstance(cons["correction"], list) and len(cons["correction"]) == 2
    assert a == 1.0 and d is False   # verdict itself is unanimous
