"""
A rehearsal on the task store: the real HEALTH API, the real workforce app, real Postgres
behind PostgREST (as Supabase serves it), driven over HTTP the way a client, clinicians, a
senior and an operator would.

It needs a throwaway stack (docs/native-task-store.md, "Rehearsing end to end") and skips
without one:

    NATIVE_E2E_API=http://127.0.0.1:8765  NATIVE_E2E_APP=http://localhost:3765 \\
    NATIVE_E2E_DSN=postgresql://postgres:test@127.0.0.1:55432/senebi_e2e \\
    NATIVE_E2E_ADMIN_KEY=...  NATIVE_E2E_JWT_SECRET=...  PYTHONPATH=. pytest tests/e2e/test_native_cutover.py

Never point it at production: it creates clinicians, projects and sessions.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

httpx = pytest.importorskip("httpx")
psycopg = pytest.importorskip("psycopg")

API = os.environ.get("NATIVE_E2E_API")
APP = os.environ.get("NATIVE_E2E_APP")
DSN = os.environ.get("NATIVE_E2E_DSN")
ADMIN = os.environ.get("NATIVE_E2E_ADMIN_KEY")
JWT_SECRET = os.environ.get("NATIVE_E2E_JWT_SECRET")

if not all([API, APP, DSN, ADMIN, JWT_SECRET]):
    pytest.skip("native cutover rehearsal needs a throwaway stack (see the docstring)", allow_module_level=True)

P = f"{API}/api/v1/project"


# ── people and sessions ─────────────────────────────────────────────────────────

def _b64(d: bytes) -> str:
    return base64.urlsafe_b64encode(d).rstrip(b"=").decode()


def _session(db, clinician_id: str, email: str) -> str:
    """A session the way the platform issues one: a signed token and its live row."""
    now = int(time.time())
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64(json.dumps({"clinicianId": clinician_id, "email": email, "jti": secrets.token_urlsafe(16),
                               "iat": now, "exp": now + 3600}).encode())
    sig = _b64(hmac.new(JWT_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    token = f"{header}.{payload}.{sig}"
    db.execute("insert into sessions (clinician_id, token, expires_at) values (%s, %s, %s)",
               (clinician_id, hashlib.sha256(token.encode()).hexdigest(),
                datetime.now(timezone.utc) + timedelta(hours=1)))
    return token


class Clinician:
    def __init__(self, db, name):
        self.id = str(uuid.uuid4())
        self.email = f"{name}-{self.id[:6]}@e2e.test"
        db.execute("insert into clinicians (id, name, email, access_code) values (%s, %s, %s, %s)",
                   (self.id, name, self.email, self.id))
        self.http = httpx.Client(base_url=APP, timeout=30,
                                 cookies={"sessionToken": _session(db, self.id, self.email)})

    def next(self, pool):
        return self.http.get(f"/api/pools/{pool}/next")

    def submit(self, task_id, answers):
        return self.http.post(f"/api/tasks/{task_id}/submit", json={"answers": answers})


def _admin(method, path, **kw):
    r = httpx.request(method, f"{P}{path}", headers={"X-Admin-Key": ADMIN}, timeout=60, **kw)
    assert r.status_code < 300, (path, r.status_code, r.text)
    return r.json()


def _client(key):
    return httpx.Client(base_url=P, headers={"Authorization": f"Bearer {key}"}, timeout=60)


def _project(client, template, classes=None, **ec_overrides):
    pid = client.post("/projects", json={"name": f"E2E {template}", "template": template,
                                         "classes": classes}).json()["project_id"]
    if ec_overrides:
        ec = _admin("GET", "/admin/submissions")    # just to prove the route works with the store
        assert ec["ok"]
    return pid


def _pool(db, project_id, members, seniors=()):
    pool = str(uuid.uuid4())
    db.execute("insert into pools (id, name, ls_project_id, calibration_items, project_id, open_access)"
               " values (%s, 'E2E pool', 0, '[]', %s, false)", (pool, project_id))
    for c in members:
        db.execute("insert into pool_eligibility (clinician_id, pool_id, eligible, senior) values (%s, %s, true, %s)",
                   (c.id, pool, c in seniors))
    return pool


def _wait(predicate, what, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        got = predicate()
        if got:
            return got
        time.sleep(0.5)
    raise AssertionError(f"timed out waiting for {what}")


@pytest.fixture(scope="module")
def db():
    with psycopg.connect(DSN, autocommit=True) as c:
        yield c


@pytest.fixture(scope="module")
def client():
    email = f"client-{uuid.uuid4().hex[:6]}@e2e.test"
    return _client(_admin("POST", "/admin/api-key", json={"email": email})["api_key"])


# ── the rehearsal ───────────────────────────────────────────────────────────────

def test_evaluation_from_ingest_to_delivery_with_a_senior_settling_a_split(db, client):
    d1, d2, d3, senior, outsider = (Clinician(db, n) for n in ("d1", "d2", "d3", "senior", "outsider"))
    pid = _project(client, "model_evaluation", classes=["Normal", "Abnormal"])
    pool = _pool(db, pid, [d1, d2, d3, senior], seniors=[senior])
    items = [{"case_id": f"CASE-{n}", "scenario": f"Patient message {n}", "prediction": "Normal"} for n in range(3)]
    assert client.post("/ingest", json={"project_id": pid, "items": items}).status_code == 200
    # Released automatically by the ingest's own background release.
    _wait(lambda: db.execute("select count(*) from project_items where project_id = %s and status = 'queued'",
                             (pid,)).fetchone()[0] == 3, "release")

    # The pool lists for the clinicians in it, and not for anyone else.
    pools = d1.http.get("/api/pools").json()["pools"]
    assert [p["available_to_you"] for p in pools if p["id"] == pool] == [3]
    assert outsider.next(pool).status_code == 403

    verdicts = {"CASE-0": ["Correct"] * 3,
                "CASE-1": ["Correct", "Incorrect", "Partial"],                  # a three-way split
                "CASE-2": ["Incorrect"] * 3}
    for n, doc in enumerate((d1, d2, d3)):
        task = doc.next(pool).json()
        assert task["task_id"] >= 1_000_000_000
        assert "_gold_expected" not in json.dumps(task) and "_webhook" not in json.dumps(task)
        while task:
            case = task["case_id"]
            v = verdicts[case][n]
            answers = {"verdict": v, "notes": f"reader {n} on {case}"}
            if v != "Correct":
                answers["correct_label"] = "Abnormal"
            if case == "CASE-2":
                answers.update({"critical_miss": "Yes", "critical_miss_finding": "Abnormal"})
            r = doc.submit(task["task_id"], answers)
            assert r.status_code == 200, r.text
            if n == 0 and case == "CASE-0":
                again = doc.submit(task["task_id"], answers)       # a double click
                assert again.status_code == 409
            task = r.json()["next"]

    # HEALTH scores them from the native store; the split is held for the senior.
    def scored():
        rows = db.execute("select label->>'verdict', status from project_items where project_id = %s order by idx",
                          (pid,)).fetchall()
        return rows if all(s in ("done", "needs_adjudication") for _, s in rows) else None
    rows = _wait(scored, "scoring")
    assert [s for _, s in rows] == ["done", "needs_adjudication", "done"]

    # A fourth reader cannot add a review the client did not commission.
    late = Clinician(db, "late")
    db.execute("insert into pool_eligibility (clinician_id, pool_id, eligible) values (%s, %s, true)", (late.id, pool))
    assert late.next(pool).status_code == 204

    # The senior is served the held case as an ordinary one, and settles it. The platform
    # caches HEALTH's held list for up to 20 seconds (lib/health.ts HELD_TTL_MS), so the
    # case can take that long to be offered; that is by design.
    held = _wait(lambda: (lambda r: r.json() if r.status_code == 200 else None)(senior.next(pool)),
                 "the held case to be offered", timeout=40)
    assert held["case_id"] == "CASE-1" and "earlier" not in json.dumps(held)
    r = senior.submit(held["task_id"], {"verdict": "Incorrect", "correct_label": "Abnormal", "notes": "settled"})
    assert r.status_code == 200, r.text
    assert db.execute("select status, label->>'verdict', (label->>'_adjudicated')::boolean from project_items "
                      "where project_id = %s and idx = 1", (pid,)).fetchone() == ("done", "Incorrect", True)

    # Nothing ships until an operator signs off, then the client gets results with no identities.
    assert client.get("/results", params={"project_id": pid}).json()["status"] == "in_review"
    _admin("POST", "/admin/advance", json={"submission_id": pid, "stage": "delivered"})
    res = client.get("/results", params={"project_id": pid}).json()
    assert res["status"] == "delivered" and res["done"] == 3
    body = json.dumps(res)
    for doc in (d1, d2, d3, senior):
        assert doc.email not in body and doc.id not in body
    assert [c["case_id"] for c in res["report"]["critical_misses"]] == ["CASE-2"]

    # Attribution is real internally: every reading names its clinician.
    rq = _admin("GET", f"/admin/reviewers/{pid}")
    assert {d1.email, d2.email, d3.email} <= set(rq["reviewers"])


def test_a_number_and_highlights_survive_to_the_delivered_result(db, client):
    doc = Clinician(db, "tracer")
    pid = _project(client, "agent_trace_eval")
    _admin("POST", "/admin/reviewers", json={"project_id": pid, "reviewers_per_item": 1})
    pool = _pool(db, pid, [doc])
    trace = "1. search(chest pain) 2. calc(dose) 3. answer: discharge home"
    client.post("/ingest", json={"project_id": pid, "items": [{"case_id": "T-1", "scenario": "q", "trace": trace,
                                                               "prediction": "discharge"}]})
    _wait(lambda: doc.next(pool).status_code == 200, "release")
    task = doc.next(pool).json()
    text = task["context"][-1]["value"]
    start = text.index("calc(dose)")
    answers = {"verdict": "Incorrect", "first_failed_step": 2, "rationale": "dosing step was wrong",
               "error_category": "Bad tool arguments", "severity": "High",
               "step_errors": [{"start": start, "end": start + 10, "text": "calc(dose)", "label": "Bad tool arguments"}]}
    r = doc.submit(task["task_id"], answers)
    assert r.status_code == 200, r.text
    label = _wait(lambda: (db.execute("select label from project_items where project_id = %s and status = 'done'",
                                      (pid,)).fetchone() or [None])[0], "scoring")
    assert label["first_failed_step"] == 2
    assert label["step_errors"] == {"labels": ["Bad tool arguments"], "start": start, "end": start + 10,
                                    "text": "calc(dose)"}
    assert text[label["step_errors"]["start"]:label["step_errors"]["end"]] == "calc(dose)"
    bad = doc.submit(task["task_id"], {**answers, "first_failed_step": 2.5})
    assert bad.status_code in (409, 422)


def test_second_reading_on_the_native_store(db, client):
    author, reader = Clinician(db, "author"), Clinician(db, "reader")
    pid = client.post("/projects", json={"name": "E2E gold", "template": "gold_answers"}).json()["project_id"]
    pool = _pool(db, pid, [author, reader])
    client.post("/ingest", json={"project_id": pid, "items": [{"case_id": "G-1", "prompt": "What is first-line for X?"}]})
    _wait(lambda: author.next(pool).status_code == 200, "release")
    draft = author.next(pool).json()
    assert draft["phase"] == "author"
    assert author.submit(draft["task_id"], {"answer": "First draft"}).status_code == 200
    # Nothing is published, and nothing scored, before a second clinician reads it.
    assert db.execute("select count(*) from task_answers where project_id = %s", (pid,)).fetchone()[0] == 0
    # The author is never offered their own draft to approve.
    assert author.next(pool).status_code == 204
    review = reader.next(pool).json()
    assert review["phase"] == "review" and review["authored"] == {"answer": "First draft"}
    own = author.http.post(f"/api/tasks/{review['task_id']}/review", json={"action": "approve"})
    assert own.status_code == 403
    r = reader.http.post(f"/api/tasks/{review['task_id']}/review",
                         json={"action": "edit", "answers": {"answer": "Edited answer"}})
    assert r.status_code == 200, r.text
    label = _wait(lambda: (db.execute("select label from project_items where project_id = %s and status = 'done'",
                                      (pid,)).fetchone() or [None])[0], "scoring")
    assert label["answer"] == "Edited answer"
    assert label["_second_reading"]["approved"] is True and label["_second_reading"]["edited"] is True


def test_images_are_shown_through_the_gate_and_never_as_a_url(db, client):
    doc, outsider = Clinician(db, "rad"), Clinician(db, "rad-outsider")
    pid = client.post("/projects", json={"name": "E2E x-ray", "eval_config": {
        "schema": {"input": "image", "classes": ["Normal", "Abnormal"],
                   "fields": {"verdict": {"type": "single", "options": ["Correct", "Incorrect"], "required": True}}}}}
    ).json()["project_id"]
    _admin("POST", "/admin/reviewers", json={"project_id": pid, "reviewers_per_item": 1})
    pool = _pool(db, pid, [doc])
    url = "https://images.example.org/scan-1.png?X-Amz-Signature=abc"
    client.post("/ingest", json={"project_id": pid, "items": [{"image": url, "prediction": "Normal"}]})
    _wait(lambda: doc.next(pool).status_code == 200, "release")
    task = doc.next(pool).json()
    assert url not in json.dumps(task)
    assert task["context"][0] == {"label": "Image", "value": "", "kind": "image",
                                  "src": f"/api/tasks/{task['task_id']}/media"}
    media = doc.http.get(task["context"][0]["src"], follow_redirects=False)
    assert media.status_code == 302 and media.headers["location"] == url
    assert media.headers["cache-control"] == "private, no-store"
    assert outsider.http.get(task["context"][0]["src"], follow_redirects=False).status_code == 403
    # Audio and video are refused at the door until clinicians can play them.
    refused = client.post("/projects", json={"name": "E2E audio", "eval_config": {
        "schema": {"input": "audio", "fields": {"t": {"type": "text"}}}}})
    assert refused.status_code == 422 and "not supported yet" in refused.text


def test_scoring_waits_safely_when_health_is_unreachable_and_catches_up(db, client):
    """The nudge is lost (we simulate it by leaving events pending), and the client's next
    results poll drains them. Nothing is lost while HEALTH is not looking."""
    doc = Clinician(db, "late-scorer")
    pid = _project(client, "data_labeling", classes=["A", "B"])
    _admin("POST", "/admin/reviewers", json={"project_id": pid, "reviewers_per_item": 1})
    pool = _pool(db, pid, [doc])
    client.post("/ingest", json={"project_id": pid, "items": [{"text": "Discharge summary"}]})
    _wait(lambda: doc.next(pool).status_code == 200, "release")
    task = doc.next(pool).json()
    r = doc.submit(task["task_id"], {"label": "A"})
    assert r.status_code == 200
    _wait(lambda: db.execute("select count(*) from answer_events where project_id = %s and status = 'done'",
                             (pid,)).fetchone()[0] == 1, "drain")
    assert db.execute("select status from project_items where project_id = %s", (pid,)).fetchone()[0] == "done"


# ── recovery, retries and the transition ────────────────────────────────────────

import subprocess  # noqa: E402
import threading  # noqa: E402
from http.server import BaseHTTPRequestHandler, HTTPServer  # noqa: E402

STACK = os.environ.get("NATIVE_E2E_STACK")


def _single_reader_project(db, client, template="data_labeling", classes=("A", "B")):
    doc = Clinician(db, f"reader-{template}")
    pid = _project(client, template, classes=list(classes))
    _admin("POST", "/admin/reviewers", json={"project_id": pid, "reviewers_per_item": 1})
    pool = _pool(db, pid, [doc])
    return doc, pid, pool


@pytest.mark.skipif(not STACK, reason="needs NATIVE_E2E_STACK to stop and start the API")
def test_an_answer_given_while_the_api_is_down_is_scored_when_it_returns(db, client):
    doc, pid, pool = _single_reader_project(db, client)
    client.post("/ingest", json={"project_id": pid, "items": [{"text": "Discharge summary"}]})
    _wait(lambda: doc.next(pool).status_code == 200, "release")
    task = doc.next(pool).json()
    subprocess.run([STACK, "stop-health"], check=True, capture_output=True)
    try:
        # The clinician's work does not depend on HEALTH being up: it is stored with its event.
        r = doc.submit(task["task_id"], {"label": "A"})
        assert r.status_code == 200, r.text
        assert db.execute("select count(*) from task_answers where project_id = %s", (pid,)).fetchone()[0] == 1
        assert db.execute("select status from answer_events where project_id = %s", (pid,)).fetchone()[0] == "pending"
    finally:
        subprocess.run([STACK, "start-health"], check=True, capture_output=True)
    # The client's next poll drains what waited.
    _wait(lambda: (client.get("/results", params={"project_id": pid}).json(),
                   db.execute("select status from project_items where project_id = %s", (pid,)).fetchone()[0] == "done")[1],
          "scoring after the restart")
    assert db.execute("select status from answer_events where project_id = %s", (pid,)).fetchone()[0] == "done"


class _Receiver(BaseHTTPRequestHandler):
    codes: list = []
    seen: list = []

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).seen.append((dict(self.headers), body))
        code = type(self).codes.pop(0) if type(self).codes else 200
        self.send_response(code)
        self.end_headers()

    def log_message(self, *_a):
        pass


def test_a_failed_client_delivery_is_retried_once_due_with_the_same_event_id(db, client):
    server = HTTPServer(("127.0.0.1", 0), _Receiver)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Receiver.codes, _Receiver.seen = [503, 503, 503], []
    try:
        doc = Clinician(db, "deliverer")
        made = client.post("/projects", json={"name": "E2E delivery", "template": "data_labeling",
                                              "classes": ["A", "B"],
                                              "webhook_url": f"http://127.0.0.1:{server.server_port}/hook"}).json()
        pid, secret = made["project_id"], made["webhook_secret"]
        _admin("POST", "/admin/reviewers", json={"project_id": pid, "reviewers_per_item": 1})
        pool = _pool(db, pid, [doc])
        client.post("/ingest", json={"project_id": pid, "items": [{"text": "x"}]})
        _wait(lambda: doc.next(pool).status_code == 200, "release")
        assert doc.submit(doc.next(pool).json()["task_id"], {"label": "B"}).status_code == 200
        _wait(lambda: db.execute("select status from project_items where project_id = %s",
                                 (pid,)).fetchone()[0] == "done", "scoring")
        _admin("POST", "/admin/advance", json={"submission_id": pid, "stage": "delivered"})
        last = client.get("/results", params={"project_id": pid}).json()["webhook"]
        assert last["delivered"] is False and last["attempts"] == 3
        # Not due yet: the scheduled run leaves it alone.
        _admin("POST", "/internal/process-answers")
        assert len(_Receiver.seen) == 3
        # Make the backoff pass, as time would; the scheduled run re-sends it.
        db.execute("update project_submissions set eval_config = jsonb_set(eval_config, '{_webhook_last,at}', "
                   "to_jsonb((now() - interval '10 minutes')::text)) where id = %s", (pid,))
        out = _admin("POST", "/internal/process-answers")
        assert out["deliveries_retried"] == 1
        assert client.get("/results", params={"project_id": pid}).json()["webhook"]["delivered"] is True
        ids = {h["X-Senebiclabs-Event-Id"] for h, _ in _Receiver.seen}
        assert len(_Receiver.seen) == 4 and len(ids) == 1
        # Every attempt is signed for the receiver to verify.
        for headers, body in _Receiver.seen:
            signed = headers["X-Senebiclabs-Timestamp"].encode() + b"." + body
            assert headers["X-Senebiclabs-Signature"] == "sha256=" + hmac.new(secret.encode(), signed,
                                                                               hashlib.sha256).hexdigest()
        # Delivered once it landed: nothing more is sent.
        _admin("POST", "/internal/process-answers")
        assert len(_Receiver.seen) == 4
    finally:
        server.shutdown()


def test_a_change_after_review_creates_a_new_version_and_keeps_the_answer(db, client):
    from app.services.items import content_hash
    doc, pid, pool = _single_reader_project(db, client)
    client.post("/ingest", json={"project_id": pid, "items": [{"text": "First version"}]})
    _wait(lambda: doc.next(pool).status_code == 200, "release")
    first = doc.next(pool).json()
    assert doc.submit(first["task_id"], {"label": "A"}).status_code == 200
    # The client uploads changed content for the same row. (The portal route needs Storage,
    # which this stack does not run; it calls exactly this function: tests/test_item_versions.)
    changed = {"text": "Second version"}
    out = db.execute("select outcome from ingest_item_version(%s, 0, %s, %s)",
                     (pid, json.dumps(changed), content_hash(changed))).fetchone()[0]
    assert out == "superseded"
    _admin("POST", "/admin/release", json={"project_id": pid})
    second = _wait(lambda: (lambda r: r.json() if r.status_code == 200 else None)(doc.next(pool)), "new version")
    assert second["task_id"] != first["task_id"] and "Second version" in json.dumps(second["context"])
    # The superseded version takes no more answers, even from someone who has not yet
    # answered it; the new one does.
    other = Clinician(db, "late-reader")
    db.execute("insert into pool_eligibility (clinician_id, pool_id, eligible) values (%s, %s, true)", (other.id, pool))
    assert other.submit(first["task_id"], {"label": "B"}).status_code == 403
    assert db.execute("select count(*) from task_answers where task_no = %s", (first["task_id"],)).fetchone()[0] == 1
    assert doc.submit(second["task_id"], {"label": "B"}).status_code == 200
    _wait(lambda: db.execute("select count(*) from project_items where project_id = %s and status = 'done'",
                             (pid,)).fetchone()[0] == 1, "scoring")
    _admin("POST", "/admin/advance", json={"submission_id": pid, "stage": "delivered"})
    res = client.get("/results", params={"project_id": pid}).json()
    assert [i["content"]["text"] for i in res["items"]] == ["Second version"] and res["total"] == 1
    # The first answer is still there, on the version it judged.
    export = _admin("GET", "/admin/export", params={"project_id": pid})["items"]
    old = [i for i in export if i["status"] == "superseded"]
    assert len(old) == 1 and old[0]["label"]["label"] == "A" and old[0]["content"]["text"] == "First version"


def test_invalid_answers_are_refused_and_nothing_is_stored(db, client):
    doc, pid, pool = _single_reader_project(db, client, "case_review", classes=("A",))
    client.post("/ingest", json={"project_id": pid, "items": [{"case": "A case", "ai_involvement": "Triage suggestion"}]})
    _wait(lambda: doc.next(pool).status_code == 200, "release")
    task = doc.next(pool).json()
    for bad in ({"ai_effect": "Made up"}, {"ai_effect": "Improved", "quality": 9},
                {"ai_effect": "Improved", "quality": 2.5}, {"quality": 3}):
        r = doc.submit(task["task_id"], bad)
        assert r.status_code == 422, (bad, r.text)
    assert doc.submit(999_999_999, {"ai_effect": "Improved"}).status_code in (403, 502)
    assert db.execute("select count(*) from task_answers where project_id = %s", (pid,)).fetchone()[0] == 0
