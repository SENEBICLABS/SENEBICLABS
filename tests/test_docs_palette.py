"""
The reference's colour palette is fixed.

The /docs redesign was explicitly a composition change: size, weight, spacing
and shape. The palette is Senebiclabs' identity and was not part of it. This
pins every colour token so a later edit that drifts one shows up here rather
than on the live site.

Hermetic: reads the stylesheet off disk, no browser, no network. It runs in the
ordinary `pytest -q tests` sweep, which is the point: the browser suite is not
in CI, this is.
"""
import re
from pathlib import Path

CSS = Path(__file__).resolve().parents[1] / "ui" / "app" / "docs" / "docs.css"

# The palette as shipped. Changing a value here is a deliberate brand decision
# and should be made by editing this list and the stylesheet in one commit.
PALETTE = {
    "--d-bg": "#000000",
    "--d-raised": "#0B0B0B",
    "--d-sunken": "#070707",
    "--d-line": "rgba(255, 255, 255, 0.07)",
    "--d-line-firm": "rgba(255, 255, 255, 0.13)",
    "--d-ink": "#FFFFFF",
    "--d-ink-2": "rgba(255, 255, 255, 0.74)",
    "--d-ink-3": "rgba(255, 255, 255, 0.50)",
    "--d-ink-4": "rgba(255, 255, 255, 0.34)",
}


def _declared(name: str):
    """Every value the stylesheet gives this token, in source order."""
    body = CSS.read_text(encoding="utf-8")
    return [m.group(1).strip()
            for m in re.finditer(rf"{re.escape(name)}\s*:\s*([^;]+);", body)]


def test_the_stylesheet_is_present():
    assert CSS.is_file(), f"{CSS} is missing"


def test_every_palette_token_holds_its_value():
    for token, expected in PALETTE.items():
        values = _declared(token)
        assert values, f"{token} is no longer declared in docs.css"
        assert values[0] == expected, (
            f"{token} is {values[0]!r}, expected {expected!r}. "
            "The /docs palette is fixed; change this test and the stylesheet together."
        )


def test_no_palette_token_is_redefined_with_a_different_value():
    """A second declaration elsewhere in the file would silently win."""
    for token, expected in PALETTE.items():
        for value in _declared(token):
            assert value == expected, (
                f"{token} is redeclared as {value!r} somewhere in docs.css, "
                f"which overrides the palette value {expected!r}"
            )


# Three near-blacks in the stylesheet carry a faint blue cast rather than being
# pure grey. They predate the redesign and are part of the palette as shipped:
# #0b0e13 is live, it is the search panel's ground. The other two are superseded
# by --d-sunken and --d-ink-2 but remain declared. They are listed so the check
# below can forbid a *new* hue without pretending these are not there.
EXISTING_TINTS = {"#0b0e13", "#0d1117", "#e2e8f0"}


def test_no_new_hue_is_introduced():
    """The reference is monochrome apart from the three tints above.

    Greys, black and white are always allowed. Anything else is a colour the
    brand has not chosen, and a redesign is not the place to choose one.
    """
    body = CSS.read_text(encoding="utf-8")
    offenders = []

    for m in re.finditer(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b", body):
        literal = m.group(0).lower()
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        if r == g == b or literal in EXISTING_TINTS:
            continue
        offenders.append(literal)

    for m in re.finditer(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", body):
        r, g, b = (int(x) for x in m.groups())
        if r != g != b or r != b:
            offenders.append(m.group(0) + ")")

    assert re.search(r"\bhsla?\(", body) is None, "hsl() introduces a hue"
    assert not offenders, (
        f"colours in docs.css that are neither grey nor an existing tint: "
        f"{sorted(set(offenders))}"
    )


def test_the_existing_tints_are_still_exactly_three():
    """If one is removed or another added, this says so rather than drifting."""
    body = CSS.read_text(encoding="utf-8")
    found = {m.group(0).lower() for m in re.finditer(r"#[0-9a-fA-F]{6}\b", body)
             if len({m.group(0)[1:3], m.group(0)[3:5], m.group(0)[5:7]}) > 1}
    assert found == EXISTING_TINTS, f"tinted colours changed: {sorted(found)}"
