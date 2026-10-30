"""
The scheduled drain also retires leases that have lapsed.

Nothing swept `item_reservations`, so `expire_reservations()` had no caller and the
`reservations_lapsed_unswept` check would have sat at warn forever once holds started
lapsing, which teaches an operator to ignore it. The sweep rides on the drain the
scheduled run already calls every few minutes rather than on a new route or credential.

It is housekeeping, not correctness: serving already ignores a lease whose expiry has
passed (`item_slots_taken` tests `expires_at > now()`), so a failed sweep costs a stale
row and must never cost a scored answer.
"""
from unittest.mock import patch

import pytest

from _fakedb import FakeDB
from app.api.v1 import project as proj


def _db(expire=None, **rows):
    """A drain with an empty outbox, so only the sweep is in play."""
    base = {"project_submissions": [], "project_items": [], "answer_events": [],
            "item_reservations": []}
    base.update(rows)
    calls: list = []

    def claim(_params):
        return []                      # nothing to score

    def sweep(params):
        calls.append(params)
        if isinstance(expire, Exception):
            raise expire
        return 0 if expire is None else expire

    db = FakeDB(base, rpcs={"claim_answer_events": claim, "expire_reservations": sweep})
    db.sweep_calls = calls             # type: ignore[attr-defined]
    return db


def _drain(db):
    with patch.object(proj, "get_client", return_value=db), \
         patch.object(proj, "_require_admin", return_value="ops@example.test"), \
         patch.object(proj, "retry_failed_deliveries", return_value={}):
        return proj.internal_process_answers(x_admin_key="k")


# ── it is actually called ───────────────────────────────────────────────────────

def test_the_drain_retires_lapsed_leases_and_says_how_many():
    db = _db(expire=3)
    out = _drain(db)
    assert out["ok"] is True
    assert out["reservations_expired"] == 3
    assert len(db.sweep_calls) == 1, "the sweep ran exactly once per drain"


def test_a_drain_with_nothing_to_retire_reports_zero_rather_than_nothing():
    # An operator reading the reply should be able to tell "swept, found none" from
    # "never ran": the first is a number, the second is the key being absent.
    out = _drain(_db(expire=0))
    assert out["reservations_expired"] == 0
    assert "reservation_sweep_error" not in out


def test_a_null_return_is_read_as_zero():
    # PostgREST gives null for a function that returned nothing; `or 0` keeps the reply
    # a number rather than None, which an operator would read as a failure.
    out = _drain(_db(expire=None))
    assert out["reservations_expired"] == 0


# ── running it again changes nothing ───────────────────────────────────────────

def test_running_the_drain_twice_is_safe():
    """The sweep only touches rows whose expiry has already passed, so the second run has
    nothing left to do. The scheduled job runs every few minutes, so this is the normal
    case, not an edge one."""
    db = _db(expire=2)
    first = _drain(db)
    assert first["reservations_expired"] == 2

    db2 = _db(expire=0)                # the same rows, already retired
    second = _drain(db2)
    assert second["reservations_expired"] == 0
    assert "reservation_sweep_error" not in second


def test_the_sweep_is_asked_for_nothing_in_particular():
    """It takes no arguments, so it cannot be pointed at one project or one clinician by
    accident: it retires what has expired, everywhere, or nothing."""
    db = _db(expire=1)
    _drain(db)
    assert db.sweep_calls == [{}]


# ── a failed sweep must not cost a scored answer ───────────────────────────────

def test_a_failed_sweep_does_not_fail_the_drain():
    db = _db(expire=RuntimeError("connection reset"))
    out = _drain(db)
    assert out["ok"] is True, "the drain still succeeded"
    assert out["reservation_sweep_error"] == "RuntimeError"
    assert "reservations_expired" not in out


def test_a_missing_function_does_not_fail_the_drain():
    """Before migration 009 is applied the function is not there. The drain has to keep
    scoring answers regardless, because that is the part clinicians are waiting on."""
    db = FakeDB({"project_submissions": [], "project_items": [], "answer_events": []},
                rpcs={"claim_answer_events": lambda _p: []})   # no expire_reservations
    with patch.object(proj, "get_client", return_value=db), \
         patch.object(proj, "_require_admin", return_value="ops@example.test"), \
         patch.object(proj, "retry_failed_deliveries", return_value={}):
        out = proj.internal_process_answers(x_admin_key="k")
    assert out["ok"] is True
    assert out["reservation_sweep_error"] == "RuntimeError"


def test_the_scoring_result_survives_a_failed_sweep():
    """The order matters: scoring and delivery retries are already in the reply before the
    sweep is attempted, so a sweep that throws cannot take their numbers with it."""
    db = _db(expire=RuntimeError("boom"))
    with patch.object(proj, "get_client", return_value=db), \
         patch.object(proj, "_require_admin", return_value="ops@example.test"), \
         patch.object(proj, "retry_failed_deliveries", return_value={"deliveries_retried": 2}):
        out = proj.internal_process_answers(x_admin_key="k")
    assert out["ok"] is True
    assert out["deliveries_retried"] == 2
    assert out["reservation_sweep_error"] == "RuntimeError"


def test_a_failed_delivery_retry_still_leaves_the_sweep_to_run():
    """The two best-effort passes are independent; neither may swallow the other."""
    db = _db(expire=5)
    with patch.object(proj, "get_client", return_value=db), \
         patch.object(proj, "_require_admin", return_value="ops@example.test"), \
         patch.object(proj, "retry_failed_deliveries", side_effect=RuntimeError("nope")):
        out = proj.internal_process_answers(x_admin_key="k")
    assert out["delivery_retry_error"] == "RuntimeError"
    assert out["reservations_expired"] == 5


# ── the guard that keeps it on this route ──────────────────────────────────────

def test_the_sweep_needs_no_new_route_or_credential():
    """It rides on the drain the scheduled run already calls, so ops_check.py is unchanged:
    that script is HTTP-only and standard-library, with no database connection of its own."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    ops_check = (root / "scripts" / "ops_check.py").read_text()
    assert "expire_reservations" not in ops_check
    assert "psycopg" not in ops_check and "supabase" not in ops_check.lower()

    routes = (root / "app" / "api" / "v1" / "project.py").read_text()
    assert routes.count("expire_reservations") == 1, "one caller, on the existing drain"
