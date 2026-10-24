"""
What a project's eval_config must look like, and what every item must carry for it.

These two answers used to be a side effect of generating Label Studio's labelling XML:
a config was "valid" if the XML could be built, and an item's required keys were read
back out of the XML with a regex. Nobody reads that XML (clinicians work in the workforce
app, which renders the form from eval_config itself), so the rules live here instead,
stated directly. tests/test_eval_schema.py holds them to exactly the old answers for every
template and every invalid case, so moving them changed nothing a client could see.

One rule is new: audio and video tasks are refused. HEALTH used to accept them, but the
clinician app has no player, so such a task would show a clinician a URL where the
recording should be. They come back when a player exists.
"""
import re

ALLOWED_FIELD_TYPES = {"single", "from_classes", "structured", "scale", "number", "flag", "text", "spans"}

# Input types a clinician can actually be shown today.
SUPPORTED_INPUTS = {"image", "text"}
UNSUPPORTED_INPUTS = {"audio", "video"}

_VISIBLE_WHEN = re.compile(r"\s*(\w+)\s*(==|!=)\s*(.+?)\s*$")
_DATA_KEY = re.compile(r"^\w+$")

DEFAULT_TEXT_CONTEXT = [{"key": "prompt", "label": "Prompt"}, {"key": "output", "label": "Model output"}]


def input_type(eval_config: dict) -> str:
    schema = (eval_config or {}).get("schema") or {}
    return str(schema.get("input") or (eval_config or {}).get("input") or "image").lower()


def _int_option(fdef: dict, key: str, name: str) -> None:
    if fdef.get(key) is None:
        return
    try:
        int(fdef[key])
    except (TypeError, ValueError):
        raise ValueError(f"Field {name!r}: {key!r} must be a whole number")


def _check_field(name: str, fdef, classes: list) -> None:
    if not isinstance(fdef, dict):
        raise ValueError(f"Field {name!r} must be an object")
    ftype = fdef.get("type")
    if ftype not in ALLOWED_FIELD_TYPES:
        raise ValueError(f"Unknown field type {ftype!r} for field {name!r}")
    if ftype == "single" and not fdef.get("options"):
        raise ValueError(f"Field {name!r} of type 'single' needs 'options'")
    if ftype == "from_classes" and not classes:
        raise ValueError(f"Field {name!r} of type 'from_classes' needs schema.classes")
    if ftype == "spans" and not (fdef.get("options") or classes):
        raise ValueError(f"Field {name!r} of type 'spans' needs 'options' (or schema.classes)")
    if ftype == "scale":
        _int_option(fdef, "max", name)
    if ftype == "number":
        _int_option(fdef, "min", name)
        _int_option(fdef, "max", name)
    if ftype == "text":
        _int_option(fdef, "rows", name)
    expr = fdef.get("visible_when")
    if expr and not _VISIBLE_WHEN.match(expr):
        raise ValueError(f"Unparseable visible_when: {expr!r}")


def validate(eval_config: dict) -> None:
    """Raise ValueError, with a sentence an operator or client can act on, if this config
    cannot be served to clinicians. Returns None for a valid config."""
    if not eval_config or "schema" not in eval_config:
        raise ValueError("eval_config missing 'schema'")
    schema = eval_config["schema"] or {}
    classes = schema.get("classes") or []
    fields = schema.get("fields") or {}
    if not fields:
        raise ValueError("eval_config.schema.fields is empty")
    if not isinstance(fields, dict):
        raise ValueError("eval_config.schema.fields must be an object of field name to field")

    kind = input_type(eval_config)
    if kind in UNSUPPORTED_INPUTS:
        raise ValueError(
            f"input '{kind}' is not supported yet: clinicians have no {kind} player. "
            "Use 'text' or 'image'.")
    if kind != "text" and any((f or {}).get("type") == "spans" for f in fields.values()):
        raise ValueError("'spans' fields require input: 'text'")
    if kind == "text":
        for c in (schema.get("context") or DEFAULT_TEXT_CONTEXT):
            if not (c or {}).get("key"):
                raise ValueError("each schema.context entry needs a 'key'")

    for name, fdef in fields.items():
        _check_field(name, fdef, classes)


def required_data_keys(eval_config: dict | None) -> list[str]:
    """The keys every item must carry for this config: the content a clinician is shown.

    image: the image and the model's prediction. text: each context key (prompt and output
    when none are declared). A key that is not a plain word is not required, which is what
    the old regex over the generated XML produced; tests pin that. An invalid config
    imposes nothing (it is refused where it is saved, not at ingest)."""
    if not eval_config:
        return []
    try:
        validate(eval_config)
    except ValueError:
        return []
    schema = eval_config.get("schema") or {}
    if input_type(eval_config) == "text":
        keys = [c.get("key") for c in (schema.get("context") or DEFAULT_TEXT_CONTEXT)]
    else:
        keys = ["image", "prediction"]
    return sorted({str(k) for k in keys if k and _DATA_KEY.match(str(k))})


def field_order(eval_config: dict) -> list[str]:
    """Field names in the order to ask them: schema.field_order first, then the rest."""
    schema = (eval_config or {}).get("schema") or {}
    fields = schema.get("fields") or {}
    declared = [n for n in (schema.get("field_order") or []) if n in fields]
    return declared + [n for n in fields if n not in declared]
