"""
Reservations, driven over HTTP through the real workforce app.

HEALTH's tests/sql proves the database functions in isolation. This proves the thing a
clinician actually touches: that every route which hands out a case holds it first, that
the pool card and the queue agree, and that nobody is asked to read a case whose answer
will be refused.

There are three paths that decide what a clinician sees, and all three are covered here:

  1. GET /api/pools/{id}/next          the queue asking for a case
  2. POST /api/tasks/{id}/submit       the next case returned in the submit response,
                                       which is how a working clinician draws nearly all
                                       of their cases
  3. GET /api/pools                    the pool card's count, which serves nothing but
                                       sends a clinician to the queue, so a wrong count
                                       is the same failure one step earlier

Needs the same throwaway stack as test_native_cutover (see its docstring), with the
workforce app wired to reservations. Never point it at production.
"""
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

httpx = pytest.importorskip("httpx")
psycopg = pytest.importorskip("psycopg")

from test_native_cutover import (          # noqa: E402  (the stack guard runs on import)
    APP,
    Clinician,
    _admin,
    _client,
    _pool,
    _project,
    _wait,
    db,                                    # noqa: F401  (fixtures, used by name)
    client,                                # noqa: F401
)

STACK = os.environ.get("NATIVE_E2E_STACK")


def _ingest(client, pid, n, prefix="CASE"):
    items = [{"case_id": f"{prefix}-{i}", "scenario": f"Patient message {i}", "prediction": "Normal"}
             for i in range(n)]
    assert client.post("/ingest", json={"project_id": pid, "items": items}).status_code == 200
    _wait(lambda: _count(db_conn(), "select count(*) from project_items where project_id = %s"
                                    " and status = 'queued'", pid) == n, "release")


def _count(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


_CONN = {}


def db_conn():
    """A connection for the helpers above, which run outside a fixture's scope."""
    if "c" not in _CONN:
        _CONN["c"] = psycopg.connect(os.environ["NATIVE_E2E_DSN"], autocommit=True)
    return _CONN["c"]


def _reviewers(conn, pid, n):
    conn.execute("update project_submissions set eval_config = jsonb_set(eval_config,"
                 " '{reviewers_per_item}', %s::jsonb) where id = %s", (str(n), pid))


def _leases(conn, pid):
    return conn.execute(
        "select task_no, clinician_id, status from item_reservations where project_id = %s"
        " order by task_no", (pid,)).fetchall()


# ── 1. the queue ────────────────────────────────────────────────────────────────

def test_clinicians_asking_at_the_same_moment_are_served_different_cases(db, client):
    """The measured defect, over HTTP: five asks at once used to return one task id."""
    docs = [Clinician(db, f"sim{i}") for i in range(5)]
    pid = _project(client, "model_evaluation", classes=["Normal", "Abnormal"])
    _reviewers(db, pid, 1)                       # one reading each, so no case may repeat
    pool = _pool(db, pid, docs)
    _ingest(client, pid, 5)

    with ThreadPoolExecutor(max_workers=5) as pool_exec:
        replies = list(pool_exec.map(lambda d: d.next(pool), docs))

    assert [r.status_code for r in replies] == [200] * 5, [r.status_code for r in replies]
    served = [r.json()["task_id"] for r in replies]
    assert len(set(served)) == 5, f"the same case went to more than one clinician: {served}"

    # And every one of them can submit, which is the point of holding it.
    for doc, task in zip(docs, served):
        r = doc.submit(task, {"verdict": "Correct", "notes": "ok"})
        assert r.status_code == 200, (task, r.status_code, r.text)


def test_three_readings_go_to_three_clinicians_and_the_fourth_gets_other_work(db, client):
    """Capacity, not exclusivity: the configured number of independent readings stands."""
    docs = [Clinician(db, f"cap{i}") for i in range(4)]
    pid = _project(client, "model_evaluation", classes=["Normal", "Abnormal"])
    _reviewers(db, pid, 3)
    pool = _pool(db, pid, docs)
    _ingest(client, pid, 1)                      # one case, three readings, four clinicians

    served = [d.next(pool) for d in docs]
    assert [r.status_code for r in served[:3]] == [200] * 3
    first_three = {r.json()["task_id"] for r in served[:3]}
    assert len(first_three) == 1, "three independent readings must be of the SAME case"
    # The fourth is told there is nothing, rather than being allowed to read and refused.
    assert served[3].status_code == 204, served[3].text


def test_asking_again_resumes_the_same_case(db, client):
    """A reload must not draw a second case and leave the first one held."""
    doc = Clinician(db, "reload")
    pid = _project(client, "model_evaluation", classes=["Normal", "Abnormal"])
    _reviewers(db, pid, 3)
    pool = _pool(db, pid, [doc])
    _ingest(client, pid, 4)

    first = doc.next(pool).json()["task_id"]
    for _ in range(4):
        assert doc.next(pool).json()["task_id"] == first
    assert _count(db, "select count(*) from item_reservations where project_id = %s"
                      " and status = 'active'", pid) == 1

    # Once answered, they move on, and the answered case is not offered back.
    assert doc.submit(first, {"verdict": "Correct", "notes": "ok"}).status_code == 200
    assert doc.next(pool).json()["task_id"] != first


# ── 2. the submit path, which is how a working clinician draws cases ───────────

def test_the_case_returned_by_submit_is_held_too(db, client):
    """The hot path. If only /next reserved, the collision would come straight back."""
    docs = [Clinician(db, f"hot{i}") for i in range(4)]
    pid = _project(client, "model_evaluation", classes=["Normal", "Abnormal"])
    _reviewers(db, pid, 1)
    pool = _pool(db, pid, docs)
    _ingest(client, pid, 8)

    # Each clinician takes one case from /next, then works purely off submit's `next`.
    def run(doc):
        seen, task = [], doc.next(pool).json()
        while task:
            seen.append(task["task_id"])
            r = doc.submit(task["task_id"], {"verdict": "Correct", "notes": "ok"})
            assert r.status_code == 200, (task["task_id"], r.text)
            task = r.json()["next"]
        return seen

    with ThreadPoolExecutor(max_workers=4) as ex:
        runs = list(ex.map(run, docs))

    worked = [t for run_ in runs for t in run_]
    assert len(worked) == 8, f"8 cases, one reading each, got {len(worked)}"
    assert len(set(worked)) == 8, "a case was worked twice at a ceiling of one"
    assert _count(db, "select count(*) from task_answers where project_id = %s", pid) == 8


def test_a_duplicate_submission_is_still_refused(db, client):
    """Reservations must not weaken the submit-time guard. A double click is a 409."""
    doc = Clinician(db, "dupe")
    pid = _project(client, "model_evaluation", classes=["Normal", "Abnormal"])
    _reviewers(db, pid, 3)
    pool = _pool(db, pid, [doc])
    _ingest(client, pid, 2)

    task = doc.next(pool).json()["task_id"]
    answers = {"verdict": "Correct", "notes": "ok"}
    assert doc.submit(task, answers).status_code == 200
    assert doc.submit(task, answers).status_code == 409
    assert _count(db, "select count(*) from task_answers where task_no = %s", task) == 1


# ── 3. the pool card ───────────────────────────────────────────────────────────

def test_the_card_does_not_offer_work_the_queue_cannot_serve(db, client):
    """A card saying work is waiting while every slot is being read is the same bug."""
    holder, looker = Clinician(db, "holder"), Clinician(db, "looker")
    pid = _project(client, "model_evaluation", classes=["Normal", "Abnormal"])
    _reviewers(db, pid, 1)
    pool = _pool(db, pid, [holder, looker])
    _ingest(client, pid, 1)

    before = [p["available_to_you"] for p in looker.http.get("/api/pools").json()["pools"]
              if p["id"] == pool]
    assert before == [1]

    holder.next(pool)                            # the only case is now held
    after = [p["available_to_you"] for p in looker.http.get("/api/pools").json()["pools"]
             if p["id"] == pool]
    assert after == [0], "the card still offered a case that was being read"
    assert looker.next(pool).status_code == 204


# ── leases over time ───────────────────────────────────────────────────────────

def test_a_lapsed_lease_returns_the_case_to_the_pool(db, client):
    first, second = Clinician(db, "lapse1"), Clinician(db, "lapse2")
    pid = _project(client, "model_evaluation", classes=["Normal", "Abnormal"])
    _reviewers(db, pid, 1)
    pool = _pool(db, pid, [first, second])
    _ingest(client, pid, 1)

    task = first.next(pool).json()["task_id"]
    assert second.next(pool).status_code == 204

    db.execute("update item_reservations set expires_at = now() - interval '1 minute'"
               " where task_no = %s", (task,))
    assert second.next(pool).json()["task_id"] == task, "a lapsed lease did not free the case"
    assert second.submit(task, {"verdict": "Correct", "notes": "ok"}).status_code == 200


def test_a_renewed_lease_keeps_the_case(db, client):
    first, second = Clinician(db, "renew1"), Clinician(db, "renew2")
    pid = _project(client, "model_evaluation", classes=["Normal", "Abnormal"])
    _reviewers(db, pid, 1)
    pool = _pool(db, pid, [first, second])
    _ingest(client, pid, 1)

    task = first.next(pool).json()["task_id"]
    db.execute("update item_reservations set expires_at = now() + interval '30 seconds'"
               " where task_no = %s", (task,))
    assert db.execute("select renew_reservation(%s, %s, 60, 4)", (first.id, task)).fetchone()[0] == "renewed"
    assert second.next(pool).status_code == 204, "a renewed lease did not keep the case"
    assert first.next(pool).json()["task_id"] == task


def test_flagging_a_case_gives_the_slot_back_at_once(db, client):
    first, second = Clinician(db, "flag1"), Clinician(db, "flag2")
    pid = _project(client, "model_evaluation", classes=["Normal", "Abnormal"])
    _reviewers(db, pid, 1)
    pool = _pool(db, pid, [first, second])
    _ingest(client, pid, 1)

    task = first.next(pool).json()["task_id"]
    r = first.http.post(f"/api/tasks/{task}/flag", json={"reason": "cannot judge this"})
    assert r.status_code == 200, r.text
    assert db.execute("select status, release_reason from item_reservations where task_no = %s",
                      (task,)).fetchone() == ("released", "flagged")
    # Free for somebody else immediately, and never offered back to the flagger.
    assert second.next(pool).json()["task_id"] == task
    assert first.next(pool).status_code == 204


def test_re_uploading_a_case_takes_the_lease_with_it(db, client):
    """Content that changes under a clinician must not keep their lease.

    The client's own upload route needs Storage, which this stack does not run, so the
    change goes through the function that route calls (as test_native_cutover does).
    """
    from app.services.items import content_hash

    doc = Clinician(db, "version")
    pid = _project(client, "model_evaluation", classes=["Normal", "Abnormal"])
    _reviewers(db, pid, 3)
    pool = _pool(db, pid, [doc])
    _ingest(client, pid, 1)

    task = doc.next(pool).json()["task_id"]
    changed = {"case_id": "CASE-0", "scenario": "Patient message 0 revised", "prediction": "Abnormal"}
    # A released case always supersedes rather than being edited in place, so a lease can
    # never survive onto content the clinician did not read. Holding it is enough.
    assert db.execute("select outcome from ingest_item_version(%s, 0, %s, %s)",
                      (pid, json.dumps(changed), content_hash(changed))).fetchone()[0] == "superseded"

    assert db.execute("select status, release_reason from item_reservations where task_no = %s",
                      (task,)).fetchone() == ("released", "superseded")
    # The old case is gone for good, and the replacement is a different case.
    assert doc.submit(task, {"verdict": "Correct", "notes": "late"}).status_code == 403
    _admin("POST", "/admin/release", json={"project_id": pid})
    new_case = _wait(lambda: (lambda r: r.json() if r.status_code == 200 else None)(doc.next(pool)),
                     "the new version to be served", timeout=40)
    assert new_case["task_id"] != task
    assert "revised" in json.dumps(new_case["context"])


# ── when something is unreachable ───────────────────────────────────────────────

@pytest.mark.skipif(not STACK, reason="needs NATIVE_E2E_STACK to stop and start HEALTH")
def test_cases_are_still_served_and_held_while_health_is_unreachable(db, client):
    """Serving reads Postgres, so a HEALTH outage must not stop clinicians working."""
    import subprocess

    docs = [Clinician(db, f"out{i}") for i in range(3)]
    pid = _project(client, "model_evaluation", classes=["Normal", "Abnormal"])
    _reviewers(db, pid, 1)
    pool = _pool(db, pid, docs)
    _ingest(client, pid, 3)

    subprocess.run([STACK, "stop-health"], check=True, capture_output=True)
    try:
        served = [d.next(pool) for d in docs]
        assert [r.status_code for r in served] == [200] * 3, [r.status_code for r in served]
        got = [r.json()["task_id"] for r in served]
        assert len(set(got)) == 3, f"an outage collapsed serving onto one case: {got}"
        for doc, task in zip(docs, got):
            assert doc.submit(task, {"verdict": "Correct", "notes": "ok"}).status_code == 200
    finally:
        subprocess.run([STACK, "start-health"], check=True, capture_output=True)

    # The answers were stored while HEALTH was down. Nothing nudged it while it was
    # gone, so the client's next poll is what drains them, exactly as in normal running.
    _wait(lambda: (client.get("/results", params={"project_id": pid}),
                   _count(db, "select count(*) from project_items where project_id = %s"
                              " and status = 'done'", pid) == 3)[1],
          "catching up after the outage", timeout=60)


# ── the whole way through ───────────────────────────────────────────────────────

def test_consensus_adjudication_and_delivery_with_every_case_held_when_served(db, client):
    """The full journey, with reservations in the path the whole way.

    Three readings per case, agreement on one and a split on the other, a senior settling
    it, and the delivered result carrying no clinician identity.
    """
    d1, d2, d3, senior = (Clinician(db, n) for n in ("c1", "c2", "c3", "csenior"))
    pid = _project(client, "model_evaluation", classes=["Normal", "Abnormal"])
    _reviewers(db, pid, 3)
    pool = _pool(db, pid, [d1, d2, d3, senior], seniors=[senior])
    _ingest(client, pid, 2)

    verdicts = {"CASE-0": ["Correct"] * 3, "CASE-1": ["Correct", "Incorrect", "Partial"]}
    for n, doc in enumerate((d1, d2, d3)):
        task = doc.next(pool).json()
        while task:
            # Every case a clinician is shown is one they hold.
            held = db.execute("select count(*) from item_reservations where task_no = %s"
                              " and clinician_id = %s and status = 'active'",
                              (task["task_id"], doc.id)).fetchone()[0]
            assert held == 1, f"{doc.email} was shown case {task['task_id']} without holding it"

            v = verdicts[task["case_id"]][n]
            answers = {"verdict": v, "notes": f"reader {n}"}
            if v != "Correct":
                answers["correct_label"] = "Abnormal"
            r = doc.submit(task["task_id"], answers)
            assert r.status_code == 200, r.text
            task = r.json()["next"]

    rows = _wait(lambda: (lambda rs: rs if all(s in ("done", "needs_adjudication") for _, s in rs) else None)(
        db.execute("select idx, status from project_items where project_id = %s order by idx",
                   (pid,)).fetchall()), "scoring")
    assert [s for _, s in rows] == ["done", "needs_adjudication"]

    # No case is left holding a lease once its readings are in.
    assert _count(db, "select count(*) from item_reservations where project_id = %s"
                      " and status = 'active' and expires_at > now()", pid) == 0

    held = _next_until_served(senior, pool, "the held case to reach the senior")
    assert held["case_id"] == "CASE-1" and "earlier" not in json.dumps(held)
    assert senior.submit(held["task_id"], {"verdict": "Incorrect", "correct_label": "Abnormal",
                                           "notes": "settled"}).status_code == 200

    _wait(lambda: _count(db, "select count(*) from project_items where project_id = %s"
                             " and status = 'done'", pid) == 2, "the settled case to be scored")

    # Delivery: the client gets results, and nothing about who produced them.
    results = client.get(f"/results/{pid}").json()
    blob = json.dumps(results)
    for doc in (d1, d2, d3, senior):
        assert doc.id not in blob and doc.email not in blob
    assert "clinician" not in blob.lower() and "reservation" not in blob.lower()


# ── keeping a case while it is being read ──────────────────────────────────────

def _beat(doc, task_id):
    return doc.http.post(f"/api/tasks/{task_id}/heartbeat")


def test_a_clinician_still_reading_keeps_their_case(db, client):
    """The lease is an hour; a hard case can run longer. The workspace beats, it holds."""
    holder, other = Clinician(db, "beat1"), Clinician(db, "beat2")
    pid = _project(client, "model_evaluation", classes=["Normal", "Abnormal"])
    _reviewers(db, pid, 1)
    pool = _pool(db, pid, [holder, other])
    _ingest(client, pid, 1)

    task = holder.next(pool).json()["task_id"]
    # Wind the lease down to its last minutes, as a long review would.
    db.execute("update item_reservations set expires_at = now() + interval '90 seconds'"
               " where task_no = %s", (task,))
    r = _beat(holder, task)
    assert r.status_code == 200 and r.json() == {"held": True}, r.text
    left = db.execute("select expires_at - now() from item_reservations where task_no = %s",
                      (task,)).fetchone()[0]
    assert left.total_seconds() > 30 * 60, "the beat did not extend the lease"
    assert other.next(pool).status_code == 204, "the case stopped being theirs"
    assert holder.submit(task, {"verdict": "Correct", "notes": "took a while"}).status_code == 200


def test_a_clinician_cannot_beat_a_case_they_do_not_hold(db, client):
    """Ownership is the database's to decide, and a lease is not a credential."""
    holder, other = Clinician(db, "own1"), Clinician(db, "own2")
    outsider = Clinician(db, "own3")
    pid = _project(client, "model_evaluation", classes=["Normal", "Abnormal"])
    _reviewers(db, pid, 1)
    pool = _pool(db, pid, [holder, other])          # outsider is NOT eligible
    _ingest(client, pid, 1)

    task = holder.next(pool).json()["task_id"]
    before = db.execute("select expires_at from item_reservations where task_no = %s",
                        (task,)).fetchone()[0]

    # Eligible for the pool, but not holding this case: told plainly, and nothing moves.
    r = _beat(other, task)
    assert r.status_code == 200 and r.json()["held"] is False, r.text

    # Not eligible for the pool at all: refused before any lease is considered.
    assert _beat(outsider, task).status_code == 403

    after = db.execute("select expires_at, clinician_id from item_reservations where task_no = %s",
                       (task,)).fetchone()
    assert after[0] == before, "someone else's beat moved the holder's lease"
    assert str(after[1]) == holder.id


def test_beating_a_lease_that_has_already_lapsed_does_not_revive_it(db, client):
    holder, other = Clinician(db, "lapse3"), Clinician(db, "lapse4")
    pid = _project(client, "model_evaluation", classes=["Normal", "Abnormal"])
    _reviewers(db, pid, 1)
    pool = _pool(db, pid, [holder, other])
    _ingest(client, pid, 1)

    task = holder.next(pool).json()["task_id"]
    db.execute("update item_reservations set expires_at = now() - interval '1 minute'"
               " where task_no = %s", (task,))
    r = _beat(holder, task)
    assert r.status_code == 200 and r.json()["held"] is False, r.text
    assert "gone back to the pool" in r.json()["reason"]
    assert db.execute("select status from item_reservations where task_no = %s",
                      (task,)).fetchone()[0] == "expired"
    # And the case really is available to somebody else.
    assert other.next(pool).json()["task_id"] == task


def test_a_beat_is_refused_for_a_task_that_cannot_be_held(db, client):
    doc = Clinician(db, "beat-bad")
    pid = _project(client, "model_evaluation", classes=["Normal", "Abnormal"])
    _pool(db, pid, [doc])
    # A Label Studio sized id has no lease, and neither has nonsense.
    assert _beat(doc, 5).status_code == 400
    assert doc.http.post("/api/tasks/0/heartbeat").status_code == 400


@pytest.mark.skipif(not STACK, reason="needs NATIVE_E2E_STACK to stop and start HEALTH")
def test_a_beat_never_reports_a_hold_it_could_not_confirm(db, client):
    """A heartbeat that cannot reach the database must not answer "still yours".

    The clinician would carry on reading a case about to be handed to someone else. The
    lease is read through PostgREST, so this is exercised by taking that away.
    """
    import subprocess

    doc = Clinician(db, "beat-out")
    pid = _project(client, "model_evaluation", classes=["Normal", "Abnormal"])
    _reviewers(db, pid, 1)
    pool = _pool(db, pid, [doc])
    _ingest(client, pid, 1)
    task = doc.next(pool).json()["task_id"]

    # Paused, not stopped: the stack's containers are created with --rm, so stopping one
    # deletes it and the stack cannot be restarted. Pausing is reversible.
    subprocess.run(["docker", "pause", "senebi-e2e-postgrest"], check=True, capture_output=True)
    try:
        # What must never happen is `held: true`. What actually happens is that the
        # request hangs until the client gives up, because the database client carries no
        # timeout of its own: the beat is left unanswered rather than answered wrongly.
        # Safe, and slow enough to be worth a timeout on that client one day.
        try:
            r = _beat(doc, task)
        except httpx.ReadTimeout:
            r = None
        if r is not None:
            assert r.status_code != 200, "a beat claimed a hold while the store was down"
            assert r.status_code in (403, 500, 502), (r.status_code, r.text)
    finally:
        subprocess.run(["docker", "unpause", "senebi-e2e-postgrest"], check=True, capture_output=True)
        _wait(lambda: _beat(doc, task).status_code == 200, "the store to come back", timeout=60)

    # The case was never lost: it is still theirs, and still submittable.
    assert doc.submit(task, {"verdict": "Correct", "notes": "after the outage"}).status_code == 200


# ── what reservations do not cover ─────────────────────────────────────────────

def _next_until_served(doc, pool, what, timeout=120):
    """A senior's `next` retried until it serves something.

    A single 204 is not a failure here. HEALTH's held-case list is cached by the platform
    for up to twenty seconds (lib/health.ts HELD_TTL_MS), so a case that has only just
    become held is legitimately invisible for that long, and under a loaded stack scoring
    takes a moment to get there too. Asserting on one call makes a timing window look like
    a defect; waiting is what the clinician's own page does.
    """
    return _wait(lambda: (lambda r: r.json() if r.status_code == 200 else None)(doc.next(pool)),
                 what, timeout=timeout)
    # The timeout is deliberately far longer than the cache. A shorter one flaked about
    # once in sixty test executions during a full run and never in isolation, which is the
    # signature of a harness racing a cache rather than of a defect: the assignment itself
    # is proved deterministically against Postgres in tests/sql.


def test_a_held_case_is_assigned_to_one_senior_and_settled_exactly_once(db, client):
    """The policy, over HTTP: one senior settles a case and that decision is final.

    A held case sits at its reviewer ceiling, so it cannot go through the ordinary
    reservation. It has its own assignment (`reserve_adjudication`), which hands it to one
    senior at a time. Before that it was shown to every free senior at once and all but
    the first were refused at submit, after doing the reading.
    """
    s1, s2, d1, d2, d3 = (Clinician(db, n) for n in ("sa", "sb", "sc", "sd", "se"))
    pid = _project(client, "model_evaluation", classes=["Normal", "Abnormal"])
    _reviewers(db, pid, 3)
    pool = _pool(db, pid, [s1, s2, d1, d2, d3], seniors=[s1, s2])
    _ingest(client, pid, 1)

    for doc, verdict in ((d1, "Correct"), (d2, "Incorrect"), (d3, "Partial")):
        task = doc.next(pool).json()["task_id"]
        answers = {"verdict": verdict, "notes": "reader"}
        if verdict != "Correct":
            answers["correct_label"] = "Abnormal"
        assert doc.submit(task, answers).status_code == 200

    _wait(lambda: _count(db, "select count(*) from project_items where project_id = %s"
                             " and status = 'needs_adjudication'", pid) == 1, "the split to be held")

    first = _next_until_served(s1, pool, "the held case to reach a senior")
    task = first["task_id"]

    # It is held for them, as an adjudication rather than as one of the readings.
    assert db.execute("select kind, status from item_reservations where task_no = %s"
                      " and clinician_id = %s", (task, s1.id)).fetchone() == ("adjudication", "active")
    # And holding it did not make the case look like it had a fourth reading.
    assert db.execute("select item_slots_taken(%s)", (task,)).fetchone()[0] == 3

    # The second senior is not shown it. There is no other work, so they are told so.
    second = s2.next(pool)
    assert second.status_code == 204, second.text

    # Asking again resumes, so a reload does not lose the decision in progress.
    assert _next_until_served(s1, pool, "the senior's own case to come back")["task_id"] == task

    settle = {"verdict": "Incorrect", "correct_label": "Abnormal", "notes": "settled"}
    assert s1.submit(task, settle).status_code == 200
    _wait(lambda: _count(db, "select count(*) from task_completions"
                             " where ls_task_id = %s and kind = 'adjudication'", task) == 1,
          "exactly one adjudication")
    assert db.execute("select status, (label->>'_adjudicated')::boolean from project_items"
                      " where task_no = %s", (task,)).fetchone() == ("done", True)


def test_a_senior_who_abandons_a_held_case_releases_it_to_another(db, client):
    """Abandonment is bounded by the lease and by nothing else."""
    s1, s2, d1, d2 = (Clinician(db, n) for n in ("ab1", "ab2", "ab3", "ab4"))
    pid = _project(client, "model_evaluation", classes=["Normal", "Abnormal"])
    _reviewers(db, pid, 2)
    pool = _pool(db, pid, [s1, s2, d1, d2], seniors=[s1, s2])
    _ingest(client, pid, 1)

    for doc, verdict in ((d1, "Correct"), (d2, "Incorrect")):
        task = doc.next(pool).json()["task_id"]
        answers = {"verdict": verdict, "notes": "reader"}
        if verdict != "Correct":
            answers["correct_label"] = "Abnormal"
        assert doc.submit(task, answers).status_code == 200

    _wait(lambda: _count(db, "select count(*) from project_items where project_id = %s"
                             " and status = 'needs_adjudication'", pid) == 1, "the split to be held")
    first = _next_until_served(s1, pool, "the held case to reach a senior")
    task = first["task_id"]
    assert s2.next(pool).status_code == 204

    # They close the laptop. The hold lapses and the case is another senior's to settle.
    db.execute("update item_reservations set expires_at = now() - interval '1 minute'"
               " where task_no = %s and clinician_id = %s", (task, s1.id))
    assert _next_until_served(s2, pool, "the lapsed case to reach the other senior")["task_id"] == task
    assert s2.submit(task, {"verdict": "Incorrect", "correct_label": "Abnormal",
                            "notes": "settled by the second"}).status_code == 200
    _wait(lambda: _count(db, "select count(*) from task_completions"
                             " where ls_task_id = %s and kind = 'adjudication'", task) == 1,
          "exactly one adjudication")


# ── the monitor, over HTTP ─────────────────────────────────────────────────────

def _ops_health():
    r = httpx.get(f"{os.environ['NATIVE_E2E_API']}/api/v1/project/admin/ops-health",
                  headers={"X-Admin-Key": os.environ["NATIVE_E2E_ADMIN_KEY"]}, timeout=60)
    return r.status_code, r.json()


def test_the_monitor_reports_the_lease_checks_against_a_real_database(db, client):
    """Exercised through the endpoint a monitor actually polls, not just the function."""
    doc = Clinician(db, "ops-ok")
    pid = _project(client, "model_evaluation", classes=["Normal", "Abnormal"])
    _reviewers(db, pid, 3)
    pool = _pool(db, pid, [doc])
    _ingest(client, pid, 2)
    task = doc.next(pool).json()["task_id"]

    code, out = _ops_health()
    names = {c["name"] for c in out["checks"]}
    assert {"reservations_lapsed_unswept", "reservations_overlong",
            "reservations_starving_work"} <= names, sorted(names)
    # Present and readable: it is never reported as a check that could not run.
    assert "reservations" not in names, "the lease table could not be read"

    # Scoped to this project. The stack is shared and every earlier test's work is still
    # in it, so a global assertion here would be about those rather than about this.
    by_name = {c["name"]: c for c in out["checks"]}
    assert str(task) not in by_name["reservations_lapsed_unswept"]["ids"]
    assert str(task) not in by_name["reservations_overlong"]["ids"]
    assert pid not in by_name["reservations_starving_work"]["ids"], \
        "a freshly served case was reported as starving the pool"


def test_the_monitor_sees_a_lease_that_outlived_its_bound_and_answers_503(db, client):
    doc = Clinician(db, "ops-stuck")
    pid = _project(client, "model_evaluation", classes=["Normal", "Abnormal"])
    _reviewers(db, pid, 1)
    pool = _pool(db, pid, [doc])
    _ingest(client, pid, 1)
    task = doc.next(pool).json()["task_id"]

    # A lease no renewal could have produced: alive for eleven hours, not yet expired.
    db.execute("update item_reservations set created_at = now() - interval '11 hours',"
               " expires_at = now() + interval '20 minutes' where task_no = %s", (task,))
    try:
        code, out = _ops_health()
        c = next(c for c in out["checks"] if c["name"] == "reservations_overlong")
        assert c["status"] == "alert", c
        assert str(task) in c["ids"], c
        assert code == 503, "a monitor pointed here must see a 503"
        assert out["status"] == "alert"
    finally:
        db.execute("delete from item_reservations where task_no = %s", (task,))

    # Cleared once the lease is gone, so the alarm is actionable rather than sticky.
    _, out = _ops_health()
    assert next(c for c in out["checks"] if c["name"] == "reservations_overlong")["status"] == "ok"


def test_the_monitor_sees_work_starvation_and_the_relief_clears_it(db, client):
    """A pool with work in it that no clinician can be served, and the way out."""
    holder = Clinician(db, "starve1")
    pid = _project(client, "model_evaluation", classes=["Normal", "Abnormal"])
    _reviewers(db, pid, 1)
    pool = _pool(db, pid, [holder])
    _ingest(client, pid, 2)

    # Hold every case, and make the holds old enough to be a fault rather than a session.
    first = holder.next(pool).json()["task_id"]
    db.execute("update item_reservations set created_at = now() - interval '3 hours'"
               " where project_id = %s", (pid,))
    second = [t for t in db.execute(
        "select task_no from project_items where project_id = %s", (pid,)).fetchall()
        if t[0] != first][0][0]
    db.execute("insert into item_reservations (task_no, project_id, pool_id, clinician_id,"
               " expires_at, created_at) values (%s, %s, %s, %s, now() + interval '1 hour',"
               " now() - interval '3 hours')", (second, pid, pool, Clinician(db, "starve2").id))

    try:
        code, out = _ops_health()
        c = next(c for c in out["checks"] if c["name"] == "reservations_starving_work")
        assert c["status"] == "alert", c
        assert pid in c["ids"], c
        assert code == 503
        # And a clinician really is being turned away while the work is there.
        newcomer = Clinician(db, "starve3")
        db.execute("insert into pool_eligibility (clinician_id, pool_id, eligible)"
                   " values (%s, %s, true)", (newcomer.id, pool))
        assert newcomer.next(pool).status_code == 204
    finally:
        pass

    # The relief the runbook names, applied here to prove it is the relief.
    db.execute("update item_reservations set expires_at = now() - interval '1 minute'"
               " where project_id = %s", (pid,))
    assert db.execute("select expire_reservations()").fetchone()[0] >= 2
    _, out = _ops_health()
    c = next(c for c in out["checks"] if c["name"] == "reservations_starving_work")
    assert c["status"] == "ok", c
