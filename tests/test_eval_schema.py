"""
eval_schema replaces "a config is valid if Label Studio XML can be built from it" and "an
item's required keys are whatever $keys the XML mentions". These tests hold the new rules
to exactly the old answers, for every template and a wide set of hand-made configs, so the
move changes nothing for a client. The one deliberate difference, audio and video being
refused, is tested on its own.
"""
import copy

import pytest

from app.services import eval_schema
from app.services import labelstudio as ls
from app.services import templates as T


def _old_verdict(cfg):
    try:
        ls.build_label_config(cfg)
        return "ok"
    except ValueError:
        return "invalid"


def _new_verdict(cfg):
    try:
        eval_schema.validate(cfg)
        return "ok"
    except ValueError:
        return "invalid"


def _templates():
    return [(t["name"], T.config_from_template(t["name"])) for t in T.list_templates()]


TEXT = {"input": "text", "context": [{"key": "scenario", "label": "Q"}, {"key": "prediction", "label": "A"}]}

CASES = [
    {},
    {"schema": {}},
    {"schema": {"fields": {}}},
    {"schema": {"fields": {"v": {"type": "nonsense"}}}},
    {"schema": {"fields": {"v": {"type": "single"}}}},
    {"schema": {"fields": {"v": {"type": "single", "options": ["A"]}}}},
    {"schema": {"fields": {"v": {"type": "from_classes"}}}},
    {"schema": {"classes": ["A", "B"], "fields": {"v": {"type": "from_classes", "multi": True}}}},
    {"schema": {"fields": {"s": {"type": "spans", "options": ["E"]}}}},
    {"schema": {**TEXT, "fields": {"s": {"type": "spans"}}}},
    {"schema": {**TEXT, "fields": {"s": {"type": "spans", "options": ["E"]}}}},
    {"schema": {**TEXT, "classes": ["E"], "fields": {"s": {"type": "spans"}}}},
    {"schema": {"input": "text", "context": [{"label": "no key"}], "fields": {"t": {"type": "text"}}}},
    {"schema": {"input": "text", "context": [], "fields": {"t": {"type": "text"}}}},
    {"schema": {"input": "text", "fields": {"t": {"type": "text"}}}},
    {"schema": {"fields": {"r": {"type": "scale", "max": 10}}}},
    {"schema": {"fields": {"r": {"type": "scale", "max": "ten"}}}},
    {"schema": {"fields": {"n": {"type": "number", "min": 1, "max": 40}}}},
    {"schema": {"fields": {"n": {"type": "number", "min": "one"}}}},
    {"schema": {"fields": {"t": {"type": "text", "rows": "many"}}}},
    {"schema": {"fields": {"c": {"type": "structured"}}}},
    {"schema": {"classes": ["X"], "fields": {"c": {"type": "structured", "finding_from_classes": True}}}},
    {"schema": {"fields": {"f": {"type": "flag", "label": "Cannot assess"}}}},
    {"schema": {"fields": {"v": {"type": "single", "options": ["A", "B"]},
                           "w": {"type": "text", "visible_when": "v!=A"}}}},
    {"schema": {"fields": {"v": {"type": "single", "options": ["A", "B"]},
                           "w": {"type": "text", "visible_when": "v ~ A"}}}},
    {"schema": {"input": "text", "context": [{"key": "model-output", "label": "Odd key"},
                                             {"key": "ok_key", "label": "Fine"}],
                "fields": {"t": {"type": "text"}}}},
    {"input": "text", "schema": {"context": [{"key": "q", "label": "Q"}], "fields": {"t": {"type": "text"}}}},
    {"schema": {"input": "IMAGE", "fields": {"t": {"type": "text"}}}},
]


@pytest.mark.parametrize("name,cfg", _templates())
def test_every_template_is_judged_exactly_as_before(name, cfg):
    assert _new_verdict(cfg) == _old_verdict(cfg) == "ok"
    assert eval_schema.required_data_keys(cfg) == ls.required_data_keys(cfg)


@pytest.mark.parametrize("name,cfg", _templates())
def test_every_template_as_an_image_task_needs_the_same_keys(name, cfg):
    cfg = copy.deepcopy(cfg)
    cfg["schema"]["input"] = "image"
    if any(f.get("type") == "spans" for f in cfg["schema"]["fields"].values()):
        pytest.skip("spans need text input, refused by both")
    assert _new_verdict(cfg) == _old_verdict(cfg)
    assert eval_schema.required_data_keys(cfg) == ls.required_data_keys(cfg) == ["image", "prediction"]


@pytest.mark.parametrize("cfg", CASES)
def test_hand_made_configs_are_judged_exactly_as_before(cfg):
    assert _new_verdict(cfg) == _old_verdict(cfg)
    assert eval_schema.required_data_keys(cfg) == ls.required_data_keys(cfg)


@pytest.mark.parametrize("kind", ["audio", "video"])
def test_audio_and_video_are_refused_until_clinicians_have_a_player(kind):
    cfg = {"schema": {"input": kind, "fields": {"t": {"type": "text"}}}}
    assert _old_verdict(cfg) == "ok"            # what used to be accepted
    with pytest.raises(ValueError, match=f"input '{kind}' is not supported yet"):
        eval_schema.validate(cfg)
    assert eval_schema.required_data_keys(cfg) == []


def test_a_field_that_is_not_an_object_is_refused_with_a_sentence():
    with pytest.raises(ValueError, match="must be an object"):
        eval_schema.validate({"schema": {"fields": {"v": None}}})
    with pytest.raises(ValueError, match="must be an object of field name"):
        eval_schema.validate({"schema": {"fields": [{"name": "v", "type": "text"}]}})


def test_field_order_matches_the_order_the_xml_asked_in():
    for _, cfg in _templates():
        xml = ls.build_label_config(cfg)
        order = eval_schema.field_order(cfg)
        positions = [xml.index(f'name="{n}"') for n in order]
        assert positions == sorted(positions)
