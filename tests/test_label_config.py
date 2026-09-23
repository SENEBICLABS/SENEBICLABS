"""
Slice #1 test: build_label_config generates a valid, per-project Label Studio config
from an eval_config schema. Run: python tests/test_label_config.py
(pytest is not installed in this env, so this is self-running.)
"""
import re
import xml.dom.minidom as minidom

from app.services.labelstudio import build_label_config, required_data_keys

SAMPLE = {
    "title": "Chest X-ray classification review",
    "schema": {
        "classes": ["Normal", "Pneumonia", "TB", "Effusion"],
        "multi_label": False,
        "fields": {
            "verdict": {"type": "single", "options": ["Correct", "Incorrect", "Partially correct"], "required": True},
            "correct_label": {"type": "from_classes", "multi": False, "visible_when": "verdict!=Correct"},
            "critical_miss": {"type": "structured", "visible_when": "verdict!=Correct"},
            "radiologist_confidence": {"type": "scale", "min": 1, "max": 5},
            "cannot_assess": {"type": "flag", "label": "Cannot assess"},
            "rationale": {"type": "text"},
        },
    },
}


def test_generates_valid_config_from_schema():
    xml = build_label_config(SAMPLE)

    # 1. valid XML
    minidom.parseString(xml)

    # 2. per-project classes are present (kills the Normal/Abnormal hardcode)
    for cls in ("Pneumonia", "TB", "Effusion"):
        assert f'<Choice value="{cls}"/>' in xml, f"class {cls} missing from generated config"

    # 3. every configured field is emitted
    for field in ("verdict", "correct_label", "critical_miss", "radiologist_confidence", "cannot_assess", "rationale"):
        assert f'name="{field}"' in xml, f"field {field} missing"

    # 4. structured critical_miss produced its finding sub-field
    assert 'name="critical_miss_finding"' in xml

    # 5. conditional visibility derived from "verdict!=Correct"
    assert 'visibleWhen="choice-selected"' in xml
    assert 'whenTagName="verdict"' in xml
    assert 'whenChoiceValue="Incorrect,Partially correct"' in xml, "!= Correct not expanded to non-Correct options"


def test_invalid_configs_fail_loudly():
    for bad, why in [
        ({}, "missing schema"),
        ({"schema": {"classes": [], "fields": {}}}, "empty fields"),
        ({"schema": {"classes": [], "fields": {"x": {"type": "bogus"}}}}, "unknown field type"),
        ({"schema": {"classes": [], "fields": {"x": {"type": "single"}}}}, "single without options"),
        ({"schema": {"classes": [], "fields": {"x": {"type": "from_classes"}}}}, "from_classes without classes"),
    ]:
        try:
            build_label_config(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for: {why}")


TEXT_SAMPLE = {
    "title": "Response review",
    "schema": {
        "input": "text",
        "context": [{"key": "prompt", "label": "Prompt"}, {"key": "output", "label": "Model output"}],
        "classes": ["Correct", "Incorrect"],
        "fields": {
            "verdict": {"type": "single", "options": ["Correct", "Incorrect"], "required": True},
            "quality": {"type": "scale", "max": 5},
            "notes": {"type": "text"},
        },
    },
}


def test_text_mode_binds_controls_to_an_anchor():
    xml = build_label_config(TEXT_SAMPLE)
    minidom.parseString(xml)  # valid XML
    # No <Image> in text mode, but exactly one object tag named "image" so the
    # field controls (toName="image") still bind.
    assert "<Image" not in xml, "text config must not emit an Image tag"
    assert 'name="image"' in xml, "text config needs an 'image'-named anchor for controls"
    # context keys are shown; the primary output is the anchor
    assert 'value="$prompt"' in xml and 'value="$output"' in xml


def test_required_data_keys_by_input_type():
    # Derived from the config's object tags: image mode needs the image + prediction
    # it displays; text mode needs the prompt + output it displays.
    assert required_data_keys(SAMPLE) == ["image", "prediction"]
    assert required_data_keys(TEXT_SAMPLE) == ["output", "prompt"]
    assert required_data_keys(None) == []


_EMPTY_CHOICES = re.compile(r"<Choices\b[^>]*></Choices>")


def test_structured_field_without_classes_is_written_not_picked():
    # A free-text task (grounding) has no class list, so the finding cannot be a picker.
    # An empty <Choices> is rejected by Label Studio and takes the whole project down.
    cfg = {"schema": {"input": "text", "context": [{"key": "scenario", "label": "Q"}],
                      "fields": {"hallucination": {"type": "structured",
                                                   "finding_label": "Name the claim"}}}}
    xml = build_label_config(cfg)
    minidom.parseString(xml)
    assert '<TextArea name="hallucination_finding"' in xml
    assert 'value="Name the claim"' in xml
    assert not _EMPTY_CHOICES.search(xml)


def test_every_template_renders_without_an_empty_choice_list():
    # Label Studio rejects a <Choices> with no <Choice>. Every template must render
    # with no client-supplied classes, since that is how a client first creates one.
    from app.services import templates as T
    for t in T.list_templates():
        xml = build_label_config(T.config_from_template(t["name"]))
        minidom.parseString(xml)
        assert not _EMPTY_CHOICES.search(xml), f"{t['name']} renders an empty choice list"


if __name__ == "__main__":
    test_generates_valid_config_from_schema()
    test_invalid_configs_fail_loudly()
    test_text_mode_binds_controls_to_an_anchor()
    test_required_data_keys_by_input_type()
    print("PASS: tests/test_label_config.py (schema -> valid config, conditionals, loud failures)")


def test_fields_are_asked_in_the_order_they_were_authored():
    # eval_config is stored as jsonb and Postgres returns its keys sorted, so without an
    # explicit order a clinician was asked for the rationale before the verdict it explains.
    from app.services import templates as T
    ec = T.config_from_template("clinical_safety_eval")
    authored = ec["schema"]["field_order"]
    assert authored[:3] == ["verdict", "severity", "correct_triage"]
    scrambled = {**ec, "schema": {**ec["schema"], "fields": dict(sorted(ec["schema"]["fields"].items()))}}
    order = re.findall(r'<(?:Choices|Rating|TextArea|Number|Labels) name="(\w+)"',
                       build_label_config(scrambled))
    order = [n for n in order if not n.endswith("_finding")]
    assert order == authored


def test_a_field_missing_from_the_order_is_still_asked():
    cfg = {"schema": {"input": "text", "context": [{"key": "q", "label": "Q"}],
                      "field_order": ["b"],
                      "fields": {"a": {"type": "text"}, "b": {"type": "text"}}}}
    order = re.findall(r'<TextArea name="(\w+)"', build_label_config(cfg))
    assert order == ["b", "a"]


def test_every_template_names_its_fields_clearly():
    """A field with no label is rendered from its name, which leaves a clinician reading
    'Correct label'. Anything whose prettified name is not a clear question needs one."""
    from app.services import templates as T
    unclear = {"correct_label", "critical_miss", "red_flag_missed", "clinical_correctness",
               "citations_support_claims", "recognised_missing_info", "conclusion_justified",
               "symptom_identification", "follow_up_questions", "task_completed",
               "first_failed_step", "contradiction_handling", "harm_if_missed"}
    for t in T.list_templates():
        for name, f in (t["eval_config"]["schema"].get("fields") or {}).items():
            if name in unclear:
                assert (f or {}).get("label"), f"{t['name']}.{name} needs a label"
