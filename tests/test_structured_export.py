"""
Slice #2b test: a 'structured' field (e.g. critical_miss) exports as ONE structured
object, not two disconnected keys. Run: PYTHONPATH=. python3 tests/test_structured_export.py
(pytest is not installed in this env, so this is self-running.)
"""
from app.services.scoring import parse_result as _parse_result


def _choice(from_name, value):
    return {"from_name": from_name, "type": "choices", "value": {"choices": [value]}}


def test_critical_miss_collapses_to_one_object():
    # Reviewer: verdict Incorrect, and a critical miss of Pneumonia.
    result = [
        _choice("verdict", "Incorrect"),
        _choice("critical_miss", "Yes"),
        _choice("critical_miss_finding", "Pneumonia"),
        {"from_name": "rationale", "type": "textarea", "value": {"text": ["missed the consolidation"]}},
    ]
    label = _parse_result(result)

    # verdict + rationale untouched (non-structured fields still flat)
    assert label["verdict"] == "Incorrect"
    assert label["rationale"] == "missed the consolidation"

    # the two structured controls collapsed into one object...
    assert label["critical_miss"] == {"present": True, "finding": "Pneumonia"}
    # ...and the loose '_finding' sibling is gone
    assert "critical_miss_finding" not in label


def test_no_miss_reports_present_false():
    label = _parse_result([_choice("critical_miss", "No"), _choice("critical_miss_finding", "TB")])
    assert label["critical_miss"]["present"] is False
    assert "critical_miss_finding" not in label


def test_orphan_finding_without_flag():
    # Finding chosen but the Yes/No flag left blank -> present is None, finding preserved.
    label = _parse_result([_choice("critical_miss_finding", "Effusion")])
    assert label["critical_miss"] == {"present": None, "finding": "Effusion"}


def test_plain_results_unaffected():
    label = _parse_result([_choice("verdict", "Correct"),
                           {"from_name": "score", "type": "rating", "value": {"rating": 4}}])
    assert label["verdict"] == "Correct"
    assert label["score"] == 4
    assert not any(isinstance(v, dict) and "finding" in v for k, v in label.items() if k != "_result")


if __name__ == "__main__":
    test_critical_miss_collapses_to_one_object()
    test_no_miss_reports_present_false()
    test_orphan_finding_without_flag()
    test_plain_results_unaffected()
    print("PASS: tests/test_structured_export.py (structured field -> one object; plain results untouched)")
