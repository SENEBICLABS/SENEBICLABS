"""
A clinician's own counts, against timestamps as Postgres actually serialises them.

`work/home` drives what a clinician sees about their own work: how much they did this week,
their streak, and the activity bars. Every one of those is derived from `labeled_at`, which
arrives from `project_items` through PostgREST -- and Postgres trims trailing zeros from a
timestamptz in JSON, so .087200 comes back as ".0872". `fromisoformat` on 3.10 raises on that
width, and the `except Exception: pass` around it dropped the item from the week count, the
streak and the calendar while still counting it in the total.

Roughly one item in ten. A clinician who had worked every day could see a broken streak and a
week count short of what they had done, with nothing in any log to say why.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from _fakedb import FakeDB
from app.api.v1 import project as proj

NOW = datetime.now(timezone.utc)


def _pg(dt):
    """A datetime rendered the way Postgres renders a timestamptz in JSON: trailing zeros
    trimmed, and no fraction at all on an exact second."""
    frac = f"{dt.microsecond:06d}".rstrip("0")
    return f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}{'.' + frac if frac else ''}+00:00"


def _home(items):
    db = FakeDB({"project_items": items, "project_submissions": [], "project_clinicians": []})
    labeler = {"id": "admin", "name": "Dr Amina"}        # admin sees every project
    with patch.object(proj, "get_client", return_value=db), \
         patch.object(proj, "_resolve_labeler", return_value=labeler):
        return proj.work_home(x_work_code="code")


def _item(when, idx=0):
    return {"id": idx, "project_id": "p1", "task_no": idx, "status": "done",
            "labeled_by": "Dr Amina", "labeled_at": _pg(when)}


def test_the_fixture_really_produces_a_trimmed_fraction():
    """Guard: built on Python's isoformat these tests could not reproduce the defect, because
    Python emits six digits or none and both of those parse on every version."""
    s = _pg(NOW.replace(microsecond=87200))
    assert len(s.split(".")[1].split("+")[0]) == 4, s


@pytest.mark.parametrize("microsecond, width", [(87200, 4), (870000, 2), (100000, 1),
                                                (56226, 6), (0, 0)])
def test_work_counted_today_shows_up_whatever_the_fraction_width(microsecond, width):
    """The item is counted in the week, the streak and today's bar at every width Postgres
    can emit, not only the two the stdlib used to accept."""
    when = NOW.replace(microsecond=microsecond) - timedelta(hours=1)
    s = _pg(when)
    assert len(s.split(".")[1].split("+")[0]) == width if width else "." not in s

    out = _home([_item(when)])
    assert out["total_labeled"] == 1
    assert out["this_week"] == 1, f"width {width} dropped the item from this_week"
    assert out["streak"] == 1, f"width {width} broke the streak"
    assert sum(out["daily"]) == 1, f"width {width} dropped the item from the activity bars"


def test_a_week_of_daily_work_with_trimmed_fractions_keeps_the_streak_whole():
    """The symptom a clinician would actually notice. With the old parser each day had about
    a one-in-ten chance of vanishing, which breaks the streak at the first gap."""
    items = [_item(NOW.replace(microsecond=87200) - timedelta(days=d, hours=1), idx=d)
             for d in range(7)]
    out = _home(items)
    assert out["total_labeled"] == 7
    assert out["this_week"] == 7, out["this_week"]
    assert out["streak"] == 7, f"the streak broke at a trimmed timestamp: {out['streak']}"
    assert sum(out["daily"]) == 7


def test_a_genuinely_malformed_timestamp_is_skipped_without_breaking_the_page():
    """The `except` still has work to do: a corrupt value must not count and must not raise.
    It is counted in the total, because the item exists -- only its date is unusable."""
    out = _home([{"id": 1, "project_id": "p1", "task_no": 1, "status": "done",
                  "labeled_by": "Dr Amina", "labeled_at": "not a timestamp"}])
    assert out["total_labeled"] == 1
    assert out["this_week"] == 0
    assert out["streak"] == 0
    assert sum(out["daily"]) == 0


def test_work_older_than_the_week_is_still_excluded():
    """The fix must not turn everything into this week's work."""
    out = _home([_item(NOW.replace(microsecond=87200) - timedelta(days=30))])
    assert out["total_labeled"] == 1 and out["this_week"] == 0 and sum(out["daily"]) == 0
