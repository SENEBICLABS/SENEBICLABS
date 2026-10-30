"""
A client is told the result and who stands behind it, never how the work was staffed.

The portal is the one client-facing surface that renders a report in our own markup, so a
staffing count has two ways back in: the `qa` payload the route returns, and the page that
reads it. Both are pinned here.

They drifted apart once already. The reviewer count was removed from `qa` deliberately,
and this page kept asking for it, so a delivered client was shown "Each case reviewed by
clinicians" with nothing where the number had been: the disclosure the rule forbids,
rendered as a blank.
"""
import json
import re
from pathlib import Path
from unittest.mock import patch

from _fakedb import FakeDB
from app.api.v1 import project as proj
from app.services import report as report_svc

PORTAL = Path(__file__).resolve().parents[1] / "ui" / "app" / "portal" / "page.tsx"
PID = "proj-portal"

# An interpolated value, then a word for the people who did the work. This is what a
# rendered staffing count looks like in JSX, whichever field it is read from.
RENDERED_COUNT = re.compile(
    r"\{[^{}]*\}\s*(?:</strong>\s*)?(clinicians|reviewers|specialists|doctors|annotators)\b")

# A count of people, under any name the payload might give it.
COUNT_KEYS = ("reviewers", "reviewer_count", "clinicians", "clinician_count",
              "n_reviewers", "annotators", "seniors")


def _delivered_db():
    """A delivered, multi-reviewed project: the only state in which the portal renders QA."""
    return FakeDB({
        "project_submissions": [{"id": PID, "email": "c@x.com", "company": "Acme",
                                 "stage": "delivered", "eval_config": {}}],
        "project_items": [
            {"id": "i0", "project_id": PID, "idx": 0, "status": "done", "version": 1,
             "content": {"prediction": "Normal"},
             "label": {"verdict": "Correct", "_reviewers": 3, "_agreement": 1.0},
             "labeled_at": "t"},
            {"id": "i1", "project_id": PID, "idx": 1, "status": "done", "version": 1,
             "content": {"prediction": "Normal"},
             "label": {"verdict": "Incorrect", "correct_label": "Abnormal",
                       "_reviewers": 3, "_agreement": 0.667, "_disagreed": True},
             "labeled_at": "t"},
        ],
    })


def _portal_payload():
    db = _delivered_db()
    with patch.object(proj, "get_client", return_value=db), \
            patch.object(proj, "verify_token", return_value="c@x.com"):
        return proj.portal_results(token="t", project_id=PID)


# ── the payload ─────────────────────────────────────────────────────────────────

def test_the_qa_a_client_receives_carries_no_count_of_people():
    qa = _portal_payload()["report"]["qa"]
    assert qa is not None, "this fixture must reach the branch that reports agreement"
    assert qa["mean_agreement"] is not None
    for k in COUNT_KEYS:
        assert k not in qa, f"qa.{k} tells a client how the work was staffed"


def test_no_part_of_the_delivered_payload_counts_the_people_who_reviewed():
    blob = json.dumps(_portal_payload())
    for k in COUNT_KEYS:
        assert f'"{k}"' not in blob, f"the client payload carries a {k} key"
    assert "_reviewers" not in blob, "per-reviewer internals reached the client payload"


def test_agreement_is_still_delivered_so_the_fix_removed_only_the_count():
    qa = _portal_payload()["report"]["qa"]
    assert qa["reviewed_items"] == 2 and qa["disagreements"] == 1


# ── the page ────────────────────────────────────────────────────────────────────

def test_the_portal_page_renders_no_staffing_count():
    src = PORTAL.read_text(encoding="utf-8")
    hit = RENDERED_COUNT.search(src)
    assert hit is None, f"the portal renders a staffing count: {hit.group(0)!r}"


def test_the_portal_page_does_not_read_a_reviewer_count_from_the_report():
    src = PORTAL.read_text(encoding="utf-8")
    assert ".reviewers" not in src, "the page reads a reviewer count off the report"
    for k in COUNT_KEYS:
        assert f"{k}:" not in src, f"the page's Report type still declares {k}"


def test_the_page_still_says_the_work_was_independently_reviewed():
    # Removing the count must not remove the assurance. A client is owed the second half
    # of the rule, not just the silence of the first.
    src = PORTAL.read_text(encoding="utf-8")
    assert "independently reviewed" in src
    assert "inter-reviewer agreement" in src


# ── the two must agree ──────────────────────────────────────────────────────────

def test_the_page_asks_for_nothing_the_report_does_not_send():
    """The original defect: the page read a `qa` field the report had stopped sending."""
    src = PORTAL.read_text(encoding="utf-8")
    declared = re.search(r"qa\?: \{([^}]*)\}", src)
    assert declared, "the portal's Report type no longer declares qa in a readable shape"
    names = {m.strip().split(":")[0].strip() for m in declared.group(1).split(";") if m.strip()}
    sent = set(report_svc._agreement_qa(_delivered_db().rows["project_items"], None) or {})
    assert names <= sent, f"the page declares qa fields the report never sends: {names - sent}"
