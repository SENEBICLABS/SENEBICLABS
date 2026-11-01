"""
The scheduled operations check: what it establishes, and what colour it reports.

This is the only thing in the platform that can e-mail anybody, and for as long as it has
existed it has told the truth about nothing. `ops.yml` ended its missing-configuration branch
with `exit 0`, so every scheduled run since the workflow was written was green while
`OPS_API_BASE` and `OPS_ADMIN_KEY` had never been set and ops-health had never been read. The
Actions tab said the platform was watched. It was not.

The four exit codes answer different questions and must not be collapsed:

    0  healthy, and this run established that
    1  the platform is unhealthy and needs a person
    2  not configured, so nothing was established either way
    3  the platform could not be read, so nothing was established either way

2 and 3 are the ones that matter most here. Neither means "fine" and neither means "broken";
reporting either as success is how a monitor comes to look healthy while watching nothing.
No real HTTP is made: every call is mocked.
"""
import importlib.util
import json
import pathlib
import sys
from unittest.mock import patch

import pytest

_spec = importlib.util.spec_from_file_location(
    "ops_check", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "ops_check.py")
ops_check = importlib.util.module_from_spec(_spec)
sys.modules["ops_check"] = ops_check
_spec.loader.exec_module(ops_check)

CONFIGURED = {"OPS_API_BASE": "https://api.test", "OPS_ADMIN_KEY": "op_test_not_a_real_key",
              "OPS_APP_URL": "https://app.test"}


def _health(checks, status="ok"):
    return {"status": status, "checked_at": "2026-11-01T12:00:00+00:00", "checks": checks}


def _ok(name):
    return {"name": name, "status": "ok", "count": 0, "ids": [], "note": ""}


def _bad(name, status, note="look here"):
    return {"name": name, "status": status, "count": 1, "ids": ["x"], "note": note}


def _run(env, responses, summary_path=None):
    """Run main() with every HTTP call mocked. `responses` maps a URL fragment to
    (code, data). Returns (exit code, printed output, summary text)."""
    calls = []

    def fake_call(method, url, key=None, timeout=60):
        calls.append((method, url))
        for frag, (code, data) in responses.items():
            if frag in url:
                return code, data, 10, None
        return None, None, 10, "no stub"

    out = []
    with patch.dict("os.environ", env, clear=True), \
         patch.object(ops_check, "_call", fake_call), \
         patch("builtins.print", lambda *a, **k: out.append(" ".join(str(x) for x in a))):
        if summary_path:
            import os
            os.environ["GITHUB_STEP_SUMMARY"] = str(summary_path)
        rc = ops_check.main()
    text = summary_path.read_text() if summary_path and summary_path.exists() else ""
    return rc, "\n".join(out), text, calls


# ── not configured: the defect this workstream exists for ──────────────────────

@pytest.mark.parametrize("env, missing", [
    ({}, ["OPS_API_BASE", "OPS_ADMIN_KEY"]),
    ({"OPS_API_BASE": "https://api.test"}, ["OPS_ADMIN_KEY"]),
    ({"OPS_ADMIN_KEY": "op_x"}, ["OPS_API_BASE"]),
])
def test_missing_configuration_fails_rather_than_reporting_health(env, missing, tmp_path):
    rc, out, summary, calls = _run(env, {}, tmp_path / "s.md")
    assert rc == ops_check.EXIT_UNCONFIGURED == 2, rc
    assert rc != ops_check.EXIT_OK, "an unconfigured check must never report success"
    assert calls == [], "it should not pretend to check anything without configuration"
    for name in missing:
        assert name in out and name in summary, name
    assert "::error::" in out, "the run page needs an annotation, not just a log line"
    assert "Nothing was checked" in summary
    assert "nothing is known about the platform" in out


def test_the_optional_app_url_is_not_required():
    """OPS_APP_URL only adds the clinician-app check. Demanding it would have made the
    workflow red for a reason that is not about the platform's health."""
    env = {k: v for k, v in CONFIGURED.items() if k != "OPS_APP_URL"}
    rc, out, _, calls = _run(env, {"/health": (200, {}), "process-answers": (200, {}),
                                   "ops-health": (200, _health([_ok("database_latency_ms")]))})
    assert rc == ops_check.EXIT_OK, out
    assert not any("app.test" in u for _, u in calls)


# ── healthy, unhealthy, unreachable ───────────────────────────────────────────

def test_a_healthy_platform_is_reported_as_healthy(tmp_path):
    rc, out, summary, _ = _run(CONFIGURED, {
        "/health": (200, {}), "app.test": (401, None), "process-answers": (200, {}),
        "ops-health": (200, _health([_ok("database_latency_ms"), _ok("deliveries_failing")])),
    }, tmp_path / "s.md")
    assert rc == ops_check.EXIT_OK, out
    assert "healthy" in summary.lower()


def test_a_check_in_alert_needs_a_person_and_says_which(tmp_path):
    rc, out, summary, _ = _run(CONFIGURED, {
        "/health": (200, {}), "app.test": (401, None), "process-answers": (200, {}),
        "ops-health": (503, _health([_ok("database_latency_ms"),
                                     _bad("scoring_events_failed", "alert", "retry them")],
                                    status="alert")),
    }, tmp_path / "s.md")
    assert rc == ops_check.EXIT_ALERT == 1, out
    assert "scoring_events_failed" in summary and "retry them" in summary
    assert "::error::" in out
    assert "needs a person" in summary


def test_a_warning_alone_does_not_fail_the_run(tmp_path):
    rc, out, summary, _ = _run(CONFIGURED, {
        "/health": (200, {}), "app.test": (401, None), "process-answers": (200, {}),
        "ops-health": (200, _health([_bad("deliveries_failing", "warn")], status="warn")),
    }, tmp_path / "s.md")
    assert rc == ops_check.EXIT_OK, "a warning is not an alert and must not cry wolf"
    assert "::warning::" in out, "but it should still be visible on the run page"


@pytest.mark.parametrize("responses, why", [
    ({"/health": (None, None)}, "the API did not answer"),
    ({"/health": (503, None)}, "a 503 from the API itself"),
    ({"/health": (200, {}), "app.test": (401, None), "process-answers": (200, {}),
      "ops-health": (403, None)}, "ops-health refused the key"),
])
def test_a_platform_that_cannot_be_read_is_not_an_alert_and_not_a_pass(responses, why, tmp_path):
    """The distinction the workstream asks for: a monitoring failure is not a platform alert.

    Reporting it as an alert sends somebody to look at a platform that may be perfectly well;
    reporting it as success is the defect this whole change is about.
    """
    rc, out, summary, _ = _run(CONFIGURED, responses, tmp_path / "s.md")
    assert rc == ops_check.EXIT_UNREACHABLE == 3, (why, rc, out)
    assert rc not in (ops_check.EXIT_OK, ops_check.EXIT_ALERT)
    assert "could not be read" in summary
    assert "monitoring failure, not a platform alert" in summary


def test_a_revoked_key_says_so_rather_than_sending_someone_to_the_platform(tmp_path):
    rc, out, summary, _ = _run(CONFIGURED, {
        "/health": (200, {}), "app.test": (401, None), "process-answers": (200, {}),
        "ops-health": (403, None)}, tmp_path / "s.md")
    assert rc == ops_check.EXIT_UNREACHABLE
    assert "OPS_ADMIN_KEY has been revoked" in out or "revoked" in summary, out


# ── the key never reaches the output ──────────────────────────────────────────

def test_no_secret_value_is_ever_printed(tmp_path):
    secret = "op_SECRET_SENTINEL_abcdefghijklmnopqrstuvwxyz"
    env = dict(CONFIGURED, OPS_ADMIN_KEY=secret)
    rc, out, summary, _ = _run(env, {
        "/health": (200, {}), "app.test": (401, None), "process-answers": (200, {}),
        "ops-health": (503, _health([_bad("scoring_events_failed", "alert")], status="alert")),
    }, tmp_path / "s.md")
    assert secret not in out, "the key reached stdout"
    assert secret not in summary, "the key reached the run summary"


# ── the workflow wiring, which is where the original defect lived ─────────────

WORKFLOW = pathlib.Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ops.yml"


def test_the_workflow_no_longer_exits_zero_on_missing_configuration():
    """The exact line that made every run green for as long as the workflow existed."""
    text = WORKFLOW.read_text()
    assert "ops secrets not set; skipping" not in text

    # Executable lines only. The comments explain the old `exit 0` on purpose, so matching
    # the whole file flags the explanation as the defect -- which is how the first version
    # of this assertion failed.
    runnable = [l.strip() for l in text.splitlines()
                if l.strip() and not l.strip().startswith("#")]
    import re
    # `exit 0` anywhere in a runnable line, not only at its start or end: the first version
    # of this assertion missed a mutation that smuggled it into the middle of an `if ...;
    # then echo skip; exit 0; fi`.
    offending = [l for l in runnable if re.search(r"\bexit\s+0\b", l)]
    assert not offending, f"a skip on missing configuration is the defect: {offending}"
    assert any("python scripts/ops_check.py || rc=$?" in l for l in runnable), \
        "a bare call under bash -e aborts the step before the reason is printed"
    assert any(l == 'exit "$rc"' for l in runnable), "the job must carry the script's verdict"
    for code in ("0)", "1)", "2)", "3)"):
        assert any(l.startswith(code) for l in runnable), \
            f"the workflow should say what exit {code[0]} means"
