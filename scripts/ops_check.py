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


def main() -> int:
    api = os.environ.get("OPS_API_BASE", "").rstrip("/")
    key = os.environ.get("OPS_ADMIN_KEY", "")
    app = os.environ.get("OPS_APP_URL", "").rstrip("/")
    if not api or not key:
        print("OPS_API_BASE and OPS_ADMIN_KEY are required")
        return 2
    problems, warnings = [], []

    code, _, ms, err = _call("GET", f"{api}/api/v1/health", timeout=90)
    print(f"api health: {code or err} in {ms} ms")
    if code != 200:
        problems.append(f"API health check failed ({code or err})")
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
        problems.append(f"ops-health check could not run ({code or err})")
    else:
        for c in data.get("checks", []):
            if c["status"] != "ok":
                line = f"{c['name']}: {c['status']} count={c['count']} ids={c['ids']} - {c['note']}"
                (problems if c["status"] == "alert" else warnings).append(line)
        print(f"ops-health: {data.get('status')}")

    for w in warnings:
        print(f"WARN  {w}")
    for p in problems:
        print(f"ALERT {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
