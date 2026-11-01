"""
The scheduled operations check: availability, scoring, delivery retries, stuck work.

Run every few minutes by .github/workflows/ops.yml (or cron). It exits non-zero when
something needs a person, which is what turns it into an alert: a failed GitHub Actions
run emails the repository's owner. It prints names, counts and ids, never secrets or content.

  1. the API answers /api/v1/health          (allowing for a free-tier cold start)
  2. the clinician app answers               (an unauthenticated request must get 401)
  3. the scoring drain runs                  (POST /project/internal/process-answers: scores
                                              waiting answers and re-sends failed client
                                              deliveries whose backoff has passed)
  4. nothing is stuck                        (GET /project/admin/ops-health: 503 = act)

Environment: OPS_API_BASE (e.g. https://api.senebiclabs.com), OPS_ADMIN_KEY (an operator
key; issue a dedicated one with POST /project/admin/operators so it can be revoked alone),
OPS_APP_URL (e.g. https://app.senebiclabs.com). Standard library only.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request


def _call(method, url, key=None, timeout=60):
    req = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    if key:
        req.add_header("X-Admin-Key", key)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body, code = r.read(), r.status
    except urllib.error.HTTPError as e:
        body, code = e.read(), e.code
    except Exception as e:                      # refused, timed out, DNS
        return None, None, int((time.monotonic() - started) * 1000), type(e).__name__
    try:
        data = json.loads(body or b"null")
    except ValueError:
        data = None
    return code, data, int((time.monotonic() - started) * 1000), None


# Exit codes, because "it failed" is not enough to act on. The workflow turns each of these
# into a different message, and they answer different questions:
#
#   0  the platform is healthy, and this run established that
#   1  the platform is unhealthy: a check is in alert and needs a person
#   2  this check is not configured, so nothing was established either way
#   3  the platform could not be reached or would not answer, so nothing was established
#
# 2 and 3 are not "the platform is fine" and they are not "the platform is broken". Reporting
# either as success is how a monitor comes to look healthy while watching nothing, and
# reporting them as an alert sends somebody to look at a platform that may be perfectly well.
EXIT_OK, EXIT_ALERT, EXIT_UNCONFIGURED, EXIT_UNREACHABLE = 0, 1, 2, 3

REQUIRED = ("OPS_API_BASE", "OPS_ADMIN_KEY")
OPTIONAL = ("OPS_APP_URL",)


def _annotate(level: str, message: str) -> None:
    """A GitHub Actions annotation, so the run page and the e-mail carry the reason.

    Harmless anywhere else: outside Actions it is just a line of output.
    """
    print(f"::{level}::{message}")


def _summary(lines: list[str]) -> None:
    """Write the findings to the run summary, which is what the failure e-mail shows.

    Without this the e-mail says only that a job failed and the reason is a log dive away,
    which is the wrong thing to hand someone at three in the morning.
    """
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass                                   # a summary that cannot be written is not a fault


def main() -> int:
    api = os.environ.get("OPS_API_BASE", "").rstrip("/")
    key = os.environ.get("OPS_ADMIN_KEY", "")
    app = os.environ.get("OPS_APP_URL", "").rstrip("/")

    missing = [n for n in REQUIRED if not os.environ.get(n)]
    if missing:
        # Deliberately a failure and not a skip. This used to `exit 0`, which made every
        # scheduled run green, so the Actions tab said the platform was being watched while
        # nothing had been read since the workflow was written. A monitor that is not
        # configured is not monitoring, and the only honest colour for that is red.
        _annotate("error", "ops check is not configured: " + ", ".join(missing)
                  + " not set. Nothing was checked, so nothing is known about the platform.")
        _summary(["## Operations check: not configured", "",
                  "Missing repository secret(s): " + ", ".join(f"`{m}`" for m in missing),
                  "", "**Nothing was checked.** This run establishes nothing about the "
                  "platform's health, in either direction.", "",
                  "Set them under Settings -> Secrets and variables -> Actions. "
                  "docs/operations.md, \"Switching the scheduled check on\", has the "
                  "procedure and what each one is for."])
        print("ops check unconfigured: " + ", ".join(missing))
        return EXIT_UNCONFIGURED
    problems, warnings, unreachable = [], [], []

    code, _, ms, err = _call("GET", f"{api}/api/v1/health", timeout=90)
    print(f"api health: {code or err} in {ms} ms")
    if code != 200:
        unreachable.append(f"the API did not answer /health ({code or err})")
    elif ms > 5000:
        warnings.append(f"API slow to answer ({ms} ms; a cold start on a free plan looks like this)")

    if app:
        code, _, ms, err = _call("GET", f"{app}/api/pools")
        print(f"clinician app: {code or err} in {ms} ms")
        if code != 401:
            problems.append(f"Clinician app not answering as expected ({code or err}, expected 401)")

    code, data, ms, err = _call("POST", f"{api}/api/v1/project/internal/process-answers", key, timeout=120)
    if code == 404:
        print("scoring drain: not deployed yet (no native task store)")
    elif code != 200:
        problems.append(f"Scoring drain failed ({code or err})")
    else:
        summary = {k: v for k, v in (data or {}).items() if k != "ok"}
        print(f"scoring drain: {summary} in {ms} ms")
        if (data or {}).get("failed"):
            warnings.append(f"{data['failed']} scoring event(s) failed this run and will be retried")

    code, data, ms, err = _call("GET", f"{api}/api/v1/project/admin/ops-health", key)
    if code == 404:
        print("ops-health: not deployed yet")
    elif code not in (200, 503) or not isinstance(data, dict):
        unreachable.append(f"ops-health would not answer ({code or err}); a 403 means OPS_ADMIN_KEY has been revoked or replaced")
    else:
        for c in data.get("checks", []):
            if c["status"] != "ok":
                line = f"{c['name']}: {c['status']} count={c['count']} ids={c['ids']} - {c['note']}"
                (problems if c["status"] == "alert" else warnings).append(line)
        print(f"ops-health: {data.get('status')}")

    for w in warnings:
        print(f"WARN  {w}")
        _annotate("warning", w)
    for p in problems:
        print(f"ALERT {p}")
        _annotate("error", p)

    if unreachable:
        _summary(["## Operations check: the platform could not be read", "",
                  *[f"- {u}" for u in unreachable], "",
                  "**Nothing was established about the platform's health.** This is a "
                  "monitoring failure, not a platform alert: the platform may be perfectly "
                  "well. Check the API host and the database dashboard, and whether OPS_ADMIN_KEY has been "
                  "revoked."])
        return EXIT_UNREACHABLE
    if problems:
        _summary(["## Operations check: the platform needs a person", "",
                  *[f"- {p}" for p in problems],
                  *(["", "Warnings:", *[f"- {w}" for w in warnings]] if warnings else []),
                  "", "docs/operations.md has what each check means and what to do."])
        return EXIT_ALERT
    _summary(["## Operations check: healthy",
              f"- {len(warnings)} warning(s)" if warnings else "- no warnings"])
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
