"""
Slice #3 (LIVE): the generated config for a project with a structured critical_miss field
is accepted by the running Label Studio and renders both controls; the verdict!=Correct
conditional is preserved on the tags LS stores. Talks to the live LS, cleans up after
itself. Run: PYTHONPATH=. python3 tests/test_ls_render.py

Boundary: this confirms LS *accepts and parses* the config and *preserves* the conditional
attributes it needs to show/hide correct_label + critical_miss. It does NOT drive a browser,
so the actual show-on-Incorrect interaction and a human annotation are not exercised here.
"""
import httpx

from app.services import labelstudio as ls

SAMPLE = {
    "title": "Chest X-ray classification review",
    "schema": {
        "classes": ["Normal", "Pneumonia", "TB", "Effusion"],
        "multi_label": False,
        "fields": {
            "verdict": {"type": "single", "options": ["Correct", "Incorrect", "Partially correct"], "required": True},
            "correct_label": {"type": "from_classes", "visible_when": "verdict!=Correct"},
            "critical_miss": {"type": "structured", "visible_when": "verdict!=Correct"},
            "radiologist_confidence": {"type": "scale", "min": 1, "max": 5},
            "cannot_assess": {"type": "flag", "label": "Cannot assess"},
            "rationale": {"type": "text"},
        },
    },
}

EXPECTED_CONTROLS = {
    "verdict", "correct_label", "critical_miss", "critical_miss_finding",
    "radiologist_confidence", "cannot_assess", "rationale",
}


def main():
    if not ls.is_configured():
        raise SystemExit("Label Studio not configured (LS_URL / LS_TOKEN) — cannot run the live render test.")

    label_config = ls.build_label_config(SAMPLE)

    # 1. LS accepts the generated config (invalid XML/config -> 400 here).
    try:
        pid = ls.create_project(title="RENDER TEST — delete me", label_config=label_config)
    except httpx.HTTPStatusError as e:
        raise SystemExit(f"LS REJECTED the generated config ({e.response.status_code}): {e.response.text[:400]}")

    try:
        proj = ls.get_project(pid)
        parsed = proj.get("parsed_label_config") or {}
        stored = proj.get("label_config") or ""

        # 2. Both structured controls (and the rest) actually render as controls.
        missing = EXPECTED_CONTROLS - set(parsed.keys())
        assert not missing, f"LS did not render these controls: {missing}\nparsed keys: {list(parsed.keys())}"

        # critical_miss + its finding picker are both present and bound to the image.
        assert parsed["critical_miss"]["type"].lower() == "choices"
        assert parsed["critical_miss_finding"]["type"].lower() == "choices"
        finding_choices = parsed["critical_miss_finding"].get("labels") or []
        assert "Pneumonia" in finding_choices, f"finding picker missing classes: {finding_choices}"

        # 3. The verdict!=Correct conditional is preserved by LS (so the UI will act on it).
        for needle in ('visibleWhen="choice-selected"', 'whenTagName="verdict"',
                       'whenChoiceValue="Incorrect,Partially correct"'):
            assert needle in stored, f"LS did not preserve conditional attribute: {needle}"

        print(f"  live LS: PASS (project {pid} accepted; {len(EXPECTED_CONTROLS)} controls render; conditional preserved)")
    finally:
        try:
            ls.delete_project(pid)
        except Exception as exc:
            print(f"  (cleanup warning: could not delete project {pid}: {exc})")


if __name__ == "__main__":
    main()
    print("PASS: tests/test_ls_render.py (generated config accepted + rendered by the running LS)")


def test_one_reviewer_in_three_cannot_set_a_critical_miss():
    """A structured field only becomes a dict when the reviewer picked a finding, so
    a reviewer answering "No" leaves a bare string. That mixture used to skip the
    majority rule and fall through to a string vote, where a one-to-one tie went to
    whichever answer was read first. One clinician in three could therefore put
    `critical_miss: present` on a delivered result."""
    from app.api.v1.ls import _consensus, _collapse_structured

    def ans(**kw):
        return _collapse_structured(dict(kw))

    yes = ans(verdict="Incorrect", critical_miss="Yes", critical_miss_finding="effusion")
    no = ans(verdict="Partial", critical_miss="No")
    silent = ans(verdict="Correct")

    cons, _, _ = _consensus([yes, no, silent], text_fields=set(), primary="verdict")
    assert cons["critical_miss"]["present"] is False, "one of three is not a majority"

    cons, _, _ = _consensus([yes, yes, no], text_fields=set(), primary="verdict")
    assert cons["critical_miss"]["present"] is True, "two of three is a majority"

    cons, _, _ = _consensus([yes, no, no], text_fields=set(), primary="verdict")
    assert cons["critical_miss"]["present"] is False, "one of three is not a majority"
