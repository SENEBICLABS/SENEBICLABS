"""
What a valid eval_config is, and which keys every item must carry for it.

These rules used to be a side effect of building Label Studio's labelling XML. When they
moved into eval_schema, every answer below was checked against that generator; the
generator is gone now, so its answers are pinned here as literals. The one deliberate
difference, audio and video being refused, is tested on its own.
"""
import copy

import pytest

from app.services import eval_schema
from app.services import templates as T


def _verdict(cfg):
    try:
        eval_schema.validate(cfg)
        return "ok"
    except ValueError:
        return "invalid"


def _templates():
    return [(t["name"], T.config_from_template(t["name"])) for t in T.list_templates()]


TEXT = {"input": "text", "context": [{"key": "scenario", "label": "Q"}, {"key": "prediction", "label": "A"}]}

# (config, verdict, required keys), as the Label Studio generator answered them.
CASES = [
    ({}, 'invalid', []),
    ({'schema': {}}, 'invalid', []),
    ({'schema': {'fields': {}}}, 'invalid', []),
    ({'schema': {'fields': {'v': {'type': 'nonsense'}}}}, 'invalid', []),
    ({'schema': {'fields': {'v': {'type': 'single'}}}}, 'invalid', []),
    ({'schema': {'fields': {'v': {'type': 'single', 'options': ['A']}}}}, 'ok', ['image', 'prediction']),
    ({'schema': {'fields': {'v': {'type': 'from_classes'}}}}, 'invalid', []),
    ({'schema': {'classes': ['A', 'B'], 'fields': {'v': {'type': 'from_classes', 'multi': True}}}}, 'ok', ['image', 'prediction']),
    ({'schema': {'fields': {'s': {'type': 'spans', 'options': ['E']}}}}, 'invalid', []),
    ({'schema': {'input': 'text', 'context': [{'key': 'scenario', 'label': 'Q'}, {'key': 'prediction', 'label': 'A'}], 'fields': {'s': {'type': 'spans'}}}}, 'invalid', []),
    ({'schema': {'input': 'text', 'context': [{'key': 'scenario', 'label': 'Q'}, {'key': 'prediction', 'label': 'A'}], 'fields': {'s': {'type': 'spans', 'options': ['E']}}}}, 'ok', ['prediction', 'scenario']),
    ({'schema': {'input': 'text', 'context': [{'key': 'scenario', 'label': 'Q'}, {'key': 'prediction', 'label': 'A'}], 'classes': ['E'], 'fields': {'s': {'type': 'spans'}}}}, 'ok', ['prediction', 'scenario']),
    ({'schema': {'input': 'text', 'context': [{'label': 'no key'}], 'fields': {'t': {'type': 'text'}}}}, 'invalid', []),
    ({'schema': {'input': 'text', 'context': [], 'fields': {'t': {'type': 'text'}}}}, 'ok', ['output', 'prompt']),
    ({'schema': {'input': 'text', 'fields': {'t': {'type': 'text'}}}}, 'ok', ['output', 'prompt']),
    ({'schema': {'fields': {'r': {'type': 'scale', 'max': 10}}}}, 'ok', ['image', 'prediction']),
    ({'schema': {'fields': {'r': {'type': 'scale', 'max': 'ten'}}}}, 'invalid', []),
    ({'schema': {'fields': {'n': {'type': 'number', 'min': 1, 'max': 40}}}}, 'ok', ['image', 'prediction']),
    ({'schema': {'fields': {'n': {'type': 'number', 'min': 'one'}}}}, 'invalid', []),
    ({'schema': {'fields': {'t': {'type': 'text', 'rows': 'many'}}}}, 'invalid', []),
    ({'schema': {'fields': {'c': {'type': 'structured'}}}}, 'ok', ['image', 'prediction']),
    ({'schema': {'classes': ['X'], 'fields': {'c': {'type': 'structured', 'finding_from_classes': True}}}}, 'ok', ['image', 'prediction']),
    ({'schema': {'fields': {'f': {'type': 'flag', 'label': 'Cannot assess'}}}}, 'ok', ['image', 'prediction']),
    ({'schema': {'fields': {'v': {'type': 'single', 'options': ['A', 'B']}, 'w': {'type': 'text', 'visible_when': 'v!=A'}}}}, 'ok', ['image', 'prediction']),
    ({'schema': {'fields': {'v': {'type': 'single', 'options': ['A', 'B']}, 'w': {'type': 'text', 'visible_when': 'v ~ A'}}}}, 'invalid', []),
    ({'schema': {'input': 'text', 'context': [{'key': 'model-output', 'label': 'Odd key'}, {'key': 'ok_key', 'label': 'Fine'}], 'fields': {'t': {'type': 'text'}}}}, 'ok', ['ok_key']),
    ({'input': 'text', 'schema': {'context': [{'key': 'q', 'label': 'Q'}], 'fields': {'t': {'type': 'text'}}}}, 'ok', ['q']),
    ({'schema': {'input': 'IMAGE', 'fields': {'t': {'type': 'text'}}}}, 'ok', ['image', 'prediction']),
]

# Each template's required keys, and its fields in the order the old form asked them.
TEMPLATES = {
    "model_evaluation": (["prediction", "scenario"], ["verdict", "correct_label", "critical_miss", "notes"]),
    "data_labeling": (["text"], ["label", "notes"]),
    "rlhf_preference": (["prompt", "response_a", "response_b"], ["preference", "reason"]),
    "gold_answers": (["prompt"], ["answer"]),
    "case_review": (["ai_involvement", "case"], ["ai_effect", "quality", "notes"]),
    "benchmark_creation": (["topic"], ["case", "expected_answer", "difficulty", "notes"]),
    "rubric_creation": (["task_description"], ["criteria", "critical_failures", "scoring", "weighting", "confidence", "notes"]),
    "contradiction_creation": (["topic"], ["record", "source", "contradiction", "safe_behaviour", "contradiction_type", "harm_if_missed", "notes"]),
    "adversarial_prompts": (["target_area"], ["prompt", "failure_mode", "expected_correct", "notes"]),
    "fact_checking": (["prediction", "topic"], ["verdict", "error_spans", "correction", "citation"]),
    "dialogue_creation": (["scenario"], ["dialogue", "notes"]),
    "response_ranking": (["prompt", "response_a", "response_b"], ["preference", "accuracy", "empathy", "clarity", "safety", "rationale"]),
    "clinical_safety_eval": (["prediction", "scenario", "triage"], ["verdict", "severity", "correct_triage", "red_flag_missed", "error_category", "correct_label", "critical_miss", "clinical_correctness", "reasoning", "completeness", "communication", "rationale"]),
    "triage_eval": (["scenario", "triage"], ["correct_triage", "verdict", "severity", "error_category", "rationale"]),
    "reasoning_eval": (["prediction", "scenario"], ["verdict", "symptom_identification", "differential", "follow_up_questions", "recognised_missing_info", "conclusion_justified", "error_category", "severity", "rationale"]),
    "grounding_eval": (["evidence", "prediction", "scenario"], ["verdict", "retrieval_relevant", "grounding", "citations_support_claims", "contradiction_handling", "hallucination", "error_category", "severity", "rationale"]),
    "agent_trace_eval": (["prediction", "scenario", "trace"], ["verdict", "step_errors", "first_failed_step", "error_category", "severity", "task_completed", "efficiency", "rationale"]),
}


def test_every_template_is_pinned():
    assert sorted(name for name, _ in _templates()) == sorted(TEMPLATES)


@pytest.mark.parametrize("name,cfg", _templates())
def test_every_template_is_valid_and_needs_the_same_keys(name, cfg):
    assert _verdict(cfg) == "ok"
    assert eval_schema.required_data_keys(cfg) == TEMPLATES[name][0]


@pytest.mark.parametrize("name,cfg", _templates())
def test_every_template_as_an_image_task_needs_the_image_and_prediction(name, cfg):
    cfg = copy.deepcopy(cfg)
    cfg["schema"]["input"] = "image"
    if any(f.get("type") == "spans" for f in cfg["schema"]["fields"].values()):
        assert _verdict(cfg) == "invalid"          # spans need text input
        return
    assert _verdict(cfg) == "ok"
    assert eval_schema.required_data_keys(cfg) == ["image", "prediction"]


@pytest.mark.parametrize("cfg,verdict,keys", CASES)
def test_hand_made_configs_are_judged_exactly_as_before(cfg, verdict, keys):
    assert _verdict(cfg) == verdict
    assert eval_schema.required_data_keys(cfg) == keys


@pytest.mark.parametrize("kind", ["audio", "video"])
def test_audio_and_video_are_refused_until_clinicians_have_a_player(kind):
    cfg = {"schema": {"input": kind, "fields": {"t": {"type": "text"}}}}
    with pytest.raises(ValueError, match=f"input '{kind}' is not supported yet"):
        eval_schema.validate(cfg)
    assert eval_schema.required_data_keys(cfg) == []


def test_a_field_that_is_not_an_object_is_refused_with_a_sentence():
    with pytest.raises(ValueError, match="must be an object"):
        eval_schema.validate({"schema": {"fields": {"v": None}}})
    with pytest.raises(ValueError, match="must be an object of field name"):
        eval_schema.validate({"schema": {"fields": [{"name": "v", "type": "text"}]}})


@pytest.mark.parametrize("name,cfg", _templates())
def test_field_order_matches_the_order_the_old_form_asked_in(name, cfg):
    assert eval_schema.field_order(cfg) == TEMPLATES[name][1]
