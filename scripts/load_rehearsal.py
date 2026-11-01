#!/usr/bin/env python3
"""
Load rehearsal: push a client-sized batch through the whole pipeline and time each leg.

Not a test with a pass mark. It reports where the platform bends, so a delivery promise
can be made from measurements instead of hope. Point it at the throwaway stack only.

    NATIVE_E2E_API=http://127.0.0.1:8765 NATIVE_E2E_APP=http://localhost:3765 \
    NATIVE_E2E_DSN=postgresql://postgres:test@127.0.0.1:55432/senebi_e2e \
    NATIVE_E2E_ADMIN_KEY=e2e-admin-key \
    NATIVE_E2E_JWT_SECRET=... \
    python3 load_rehearsal.py --items 2000 --readers 8

What it measures, in the order a client's batch meets it:

  ingest      how long POST /ingest takes, and whether a large body is accepted at all
  release     how long until every item is queued and servable
  serve       reserve + submit, by several clinicians at once, which is the only leg
              that has a lease and therefore the only one that can collide
  score       how long after the last answer until nothing is left unscored
  report      GET /admin/report at this size, where paging past 1000 rows decides
              whether the numbers are right rather than merely fast
  results     what the client's own poll costs, and how big the payload is
"""
import argparse
import hashlib
import hmac
import json
import os
import statistics
import sys
import time
import uuid
from base64 import urlsafe_b64encode
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
import psycopg

API = os.environ.get("NATIVE_E2E_API", "")
APP = os.environ.get("NATIVE_E2E_APP", "")
DSN = os.environ.get("NATIVE_E2E_DSN", "")
ADMIN = os.environ.get("NATIVE_E2E_ADMIN_KEY", "")
SECRET = os.environ.get("NATIVE_E2E_JWT_SECRET", "")
P = f"{API}/api/v1/project"

if not all([API, APP, DSN, ADMIN, SECRET]):
    sys.exit("needs the throwaway stack's NATIVE_E2E_* variables (see the docstring)")

CLASSES = ["Self-care at home", "See a clinician within 24 hours", "Emergency care now"]


class Timer:
    """Each leg's wall time, printed as a table at the end."""

    def __init__(self):
        self.legs: list[tuple[str, float, str]] = []

    def __call__(self, name, note=""):
        return _Leg(self, name, note)

    def record(self, name, seconds, note):
        self.legs.append((name, seconds, note))
        print(f"  {name:<10} {seconds:8.2f}s   {note}", flush=True)

    def table(self, items):
        print("\n  leg          seconds      per item     note")
        print("  " + "-" * 72)
        for name, s, note in self.legs:
            print(f"  {name:<12} {s:8.2f}    {s / items * 1000:8.1f}ms   {note}")
        total = sum(s for _, s, _ in self.legs)
        print("  " + "-" * 72)
        print(f"  {'total':<12} {total:8.2f}    {total / items * 1000:8.1f}ms"
              f"   {items} items end to end")


class _Leg:
    def __init__(self, t, name, note):
        self.t, self.name, self.note = t, name, note

    def __enter__(self):
        self.started = time.monotonic()
        return self

    def __exit__(self, *exc):
        self.t.record(self.name, time.monotonic() - self.started, self.note)


def admin(method, path, **kw):
    r = httpx.request(method, f"{P}{path}", headers={"X-Admin-Key": ADMIN}, timeout=600, **kw)
    r.raise_for_status()
    return r.json()


def session(db, clinician_id, email):
    b = lambda d: urlsafe_b64encode(json.dumps(d, separators=(",", ":")).encode()).rstrip(b"=")
    now = int(time.time())
    h, p = b({"alg": "HS256", "typ": "JWT"}), b({
        "clinicianId": clinician_id, "email": email, "jti": uuid.uuid4().hex,
        "iat": now, "exp": now + 6 * 3600})
    sig = urlsafe_b64encode(hmac.new(SECRET.encode(), h + b"." + p, hashlib.sha256).digest()).rstrip(b"=")
    tok = (h + b"." + p + b"." + sig).decode()
    db.execute("insert into sessions (clinician_id, token, expires_at) values (%s,%s,%s)",
               (clinician_id, hashlib.sha256(tok.encode()).hexdigest(),
                datetime.now(timezone.utc) + timedelta(hours=6)))
    return tok


def reader(db, n):
    cid = str(uuid.uuid4())
    email = f"load-{n}-{cid[:6]}@e2e.test"
    db.execute("insert into clinicians (id,name,email,access_code) values (%s,%s,%s,%s)",
               (cid, f"load {n}", email, cid))
    return cid, email, session(db, cid, email)


def wait(predicate, what, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        got = predicate()
        if got:
            return got
        time.sleep(1)
    raise SystemExit(f"  TIMED OUT after {timeout}s waiting for {what}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", type=int, default=2000)
    ap.add_argument("--readers", type=int, default=8)
    ap.add_argument("--batch", type=int, default=500, help="items per /ingest call")
    ap.add_argument("--skip-serve", action="store_true",
                    help="measure only the legs whose cost grows with the batch: ingest, "
                         "release, report and the client's poll. The serving leg is paced "
                         "by people, so at large sizes it only measures the clock.")
    a = ap.parse_args()
    N, R = a.items, a.readers
    t = Timer()
    print(f"\n  {N} items, {R} concurrent clinicians, {a.batch} per ingest call\n")

    db = psycopg.connect(DSN, autocommit=True)
    key = admin("POST", "/admin/api-key", json={"email": f"load-{uuid.uuid4().hex[:6]}@e2e.test"})["api_key"]
    client = httpx.Client(base_url=P, headers={"Authorization": f"Bearer {key}"}, timeout=600)

    pid = client.post("/projects", json={"name": f"LOAD {N}", "template": "model_evaluation",
                                         "classes": CLASSES}).json()["project_id"]
    admin("POST", "/admin/reviewers", json={"project_id": pid, "reviewers_per_item": 1})

    # ---- ingest ----------------------------------------------------------------
    items = [{"case_id": f"LOAD-{i:06d}",
              "scenario": f"Patient message number {i}. " + "Symptom detail. " * 12,
              "prediction": "This is likely self-limiting; rest and review if it worsens."}
             for i in range(N)]
    batches = [items[i:i + a.batch] for i in range(0, N, a.batch)]
    with t("ingest", f"{len(batches)} call(s) of {a.batch}"):
        for b in batches:
            r = client.post("/ingest", json={"project_id": pid, "items": b})
            if r.status_code != 200:
                raise SystemExit(f"  INGEST FAILED at batch size {len(b)}: "
                                 f"{r.status_code} {r.text[:300]}")

    # ---- release ---------------------------------------------------------------
    def queued():
        n = db.execute("select count(*) from project_items where project_id=%s and status='queued'",
                       (pid,)).fetchone()[0]
        return n if n >= N else None

    with t("release", "every item queued and servable"):
        wait(queued, "release", timeout=max(180, N // 5))

    # ---- serve: reserve + submit, several at once -------------------------------
    readers = [reader(db, i) for i in range(R)]
    counts, collisions, errors, latencies = [0] * R, [], [], []

    def work(idx):
        _, _, tok = readers[idx]
        http = httpx.Client(base_url=APP, timeout=120, cookies={"sessionToken": tok})
        seen = set()
        while True:
            s = time.monotonic()
            r = http.get(f"/api/pools/{pool}/next")
            if r.status_code == 204:
                break
            if r.status_code != 200:
                errors.append(f"next -> {r.status_code}")
                break
            task = r.json()
            if task["task_id"] in seen:
                collisions.append(task["task_id"])
            seen.add(task["task_id"])
            sub = http.post(f"/api/tasks/{task['task_id']}/submit",
                            json={"answers": {"verdict": "Correct",
                                              "notes": f"load reader {idx}"}})
            if sub.status_code != 200:
                errors.append(f"submit -> {sub.status_code} {sub.text[:120]}")
                break
            latencies.append(time.monotonic() - s)
            counts[idx] += 1
        http.close()

    pool = str(uuid.uuid4())
    db.execute("insert into pools (id,name,ls_project_id,calibration_items,project_id,open_access)"
               " values (%s,'LOAD pool',0,'[]',%s,false)", (pool, pid))
    for cid, _, _ in readers:
        db.execute("insert into pool_eligibility (clinician_id,pool_id,eligible,senior)"
                   " values (%s,%s,true,false)", (cid, pool))

    if not a.skip_serve:
        with t("serve", f"{R} clinicians reserving and answering"):
            with ThreadPoolExecutor(max_workers=R) as ex:
                list(ex.map(work, range(R)))

    answered = sum(counts)

    # ---- score -----------------------------------------------------------------
    def settled():
        left = db.execute("select count(*) from answer_events where project_id=%s"
                          " and status not in ('done','scored','delivered')", (pid,)).fetchone()[0]
        done = db.execute("select count(*) from project_items where project_id=%s"
                          " and status in ('done','needs_adjudication')", (pid,)).fetchone()[0]
        return (left, done) if left == 0 and done >= answered else None

    if not a.skip_serve:
        with t("score", "nothing left unscored"):
            left, done = wait(settled, "scoring to settle", timeout=max(300, N // 2))

    # ---- report and the client's own poll --------------------------------------
    with t("report", "GET /admin/report"):
        rep = admin("GET", f"/admin/report/{pid}")
    with t("results", "GET /results, the client's poll"):
        res = client.get("/results", params={"project_id": pid})
        payload = len(res.content)

    totals = rep.get("report", {}).get("totals", {})
    counted = totals.get("items")
    print(f"\n  answered           {answered} by {R} clinicians "
          f"(min {min(counts)}, max {max(counts)} per clinician)")
    print(f"  served twice       {len(collisions)}  <- must be 0")
    print(f"  errors             {len(errors)}  {errors[:3]}")
    if latencies:
        latencies.sort()
        print(f"  reserve+submit     median {statistics.median(latencies)*1000:.0f}ms  "
              f"p95 {latencies[int(len(latencies)*0.95)]*1000:.0f}ms  "
              f"max {latencies[-1]*1000:.0f}ms")
    print(f"  report counted     {counted} of {N}  <- must equal, or paging truncated")
    print(f"  client payload     {payload/1024:.0f} KiB for {N} items")
    t.table(N)

    verdict = []
    if collisions:
        verdict.append(f"{len(collisions)} cases served twice")
    if errors:
        verdict.append(f"{len(errors)} request errors")
    if counted != N:
        verdict.append(f"report counted {counted} not {N}")
    print("\n  " + ("BENDS: " + "; ".join(verdict) if verdict
                    else "clean: no collisions, no errors, every item counted") + "\n")
    print(f"  project {pid}\n")


if __name__ == "__main__":
    main()
