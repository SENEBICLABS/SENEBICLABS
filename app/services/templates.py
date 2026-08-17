"""
Outcome templates — the "pick what you want to achieve" entry point.

Instead of asking a client to hand-author a config (or to reason about an abstract
`purpose`), they pick a named outcome and we start their project from a ready config.
The purpose is set by the template, never asked. This mirrors how Scale-style platforms
expose task types: the client chooses an outcome; the plumbing follows.

Each template is a complete starter `eval_config`. A client edits the specifics that are
theirs — their `classes` (label set) and, if needed, their `context` keys — then creates
the project. The `POST /projects` endpoint can expand a template by name and apply a
`classes` override, so the common case needs no config authoring at all.
"""
import copy

TEMPLATES: dict[str, dict] = {
    "model_evaluation": {
        "title": "Evaluate my model",
        "description": "Clinicians grade your model's outputs. You get an accuracy and "
                       "safety scorecard with per-class metrics and critical misses.",
        "needs": "Each item carries your model's output as `prediction`, plus the input "
                 "it responded to. Set `classes` to your label set.",
        "eval_config": {
            "title": "Model evaluation",
            "purpose": "evaluate",
            "schema": {
                "input": "text",
                "context": [
                    {"key": "scenario", "label": "Input"},
                    {"key": "prediction", "label": "Model output"},
                ],
                "classes": ["ClassA", "ClassB"],
                "case_id_field": "case_id",
                "fields": {
                    "verdict": {"type": "single", "options": ["Correct", "Incorrect", "Partial"], "required": True},
                    "correct_label": {"type": "from_classes", "visible_when": "verdict!=Correct"},
                    "critical_miss": {"type": "structured"},
                    "notes": {"type": "text"},
                },
            },
        },
    },
    "data_labeling": {
        "title": "Label my data",
        "description": "Clinicians assign a label to each item. You get consensus-labelled "
                       "data plus a class distribution and agreement summary.",
        "needs": "Each item carries the `text` to label. Set `classes` to your categories.",
        "eval_config": {
            "title": "Data labeling",
            "purpose": "label",
            "schema": {
                "input": "text",
                "context": [{"key": "text", "label": "Item"}],
                "classes": ["ClassA", "ClassB"],
                "case_id_field": "case_id",
                "fields": {
                    "label": {"type": "from_classes", "required": True},
                    "notes": {"type": "text"},
                },
            },
        },
    },
    "rlhf_preference": {
        "title": "Rank responses (RLHF)",
        "description": "Clinicians pick the better of two model responses. You get preference "
                       "pairs for RLHF/DPO plus an agreement summary.",
        "needs": "Each item carries a `prompt` and two responses, `response_a` and `response_b`.",
        "eval_config": {
            "title": "Preference ranking",
            "purpose": "create",
            "schema": {
                "input": "text",
                "context": [
                    {"key": "prompt", "label": "Prompt"},
                    {"key": "response_a", "label": "Response A"},
                    {"key": "response_b", "label": "Response B"},
                ],
                "case_id_field": "case_id",
                "fields": {
                    "preference": {"type": "single", "options": ["Response A", "Response B"], "required": True},
                    "reason": {"type": "text"},
                },
            },
        },
    },
    "gold_answers": {
        "title": "Create gold answers",
        "description": "Clinicians write the ideal answer to each prompt. You get a gold "
                       "dataset for supervised fine-tuning plus a coverage summary.",
        "needs": "Each item carries a `prompt`. A single expert authors each answer.",
        "eval_config": {
            "title": "Gold answer creation",
            "purpose": "create",
            "schema": {
                "input": "text",
                "context": [{"key": "prompt", "label": "Prompt"}],
                "case_id_field": "case_id",
                "fields": {
                    "answer": {"type": "text", "required": True},
                },
            },
        },
    },
}


def list_templates() -> list[dict]:
    """The catalog a client picks from — name, what it's for, and its purpose."""
    return [
        {"name": name, "title": t["title"], "description": t["description"],
         "needs": t["needs"], "purpose": t["eval_config"]["purpose"]}
        for name, t in TEMPLATES.items()
    ]


def config_from_template(name: str, classes: list[str] | None = None) -> dict | None:
    """Expand a template into a full eval_config, applying the client's `classes` override.
    Returns None if the template name is unknown."""
    t = TEMPLATES.get(name)
    if not t:
        return None
    ec = copy.deepcopy(t["eval_config"])
    if classes:
        ec["schema"]["classes"] = list(classes)
    return ec
