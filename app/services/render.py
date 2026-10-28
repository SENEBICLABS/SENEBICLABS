"""
How an item's content is shown to a clinician.

The stored content keeps the client's original structure. What a clinician reads is text,
so a structured value (an agent trace sent as a list of steps, a tool call as an object)
is rendered as readable numbered text. Scalars pass through untouched.
"""
import json


def render_value(v) -> str | int | float | bool | None:
    """A content value as a clinician is shown it."""
    if isinstance(v, list):
        out = []
        for i, step in enumerate(v, 1):
            if isinstance(step, dict):
                body = "\n".join(f"   {k}: {_one_line(step[k])}" for k in _step_key_order(step))
                out.append(f"Step {i}\n{body}")
            else:
                out.append(f"Step {i}: {_one_line(step)}")
        return "\n\n".join(out)
    if isinstance(v, dict):
        return "\n".join(f"{k}: {_one_line(x)}" for k, x in v.items())
    return v


def render_content(content: dict | None) -> dict:
    """Every visible content value, rendered. Keys starting with '_' are internal."""
    return {k: render_value(v) for k, v in (content or {}).items() if not str(k).startswith("_")}


# What a step IS comes before what it contains. The database's JSON storage does not keep
# the client's key order (it reorders keys), so a step would otherwise show its text above
# the fact that it was a tool call.
_STEP_LEAD_KEYS = ("step", "type", "kind", "role", "action", "tool", "name")


def _step_key_order(step: dict) -> list:
    lead = [k for k in _STEP_LEAD_KEYS if k in step]
    return lead + [k for k in step if k not in lead]


def _one_line(x) -> str:
    if isinstance(x, (dict, list)):
        return json.dumps(x, ensure_ascii=False)
    return str(x)
