"""
Reservations and the heartbeat in a real browser, against the throwaway stack.

The rest of the reservation suite drives HTTP. This drives Chromium, because the heartbeat
is the one part that only exists in a browser: a loop of timers inside a mounted component,
whose job is to keep a case while somebody reads it and to tell them plainly when it could
not. None of that is observable from a request.

Needs the throwaway stack with a short heartbeat so a renewal happens while a test watches:

    WORKFORCE_REPO=<the workforce checkout> HEARTBEAT_INTERVAL_MS=3000 \\
      scripts/native_e2e_stack.sh up

then the same NATIVE_E2E_* variables as test_native_cutover, plus --browser chromium.
Never point it at production: it creates clinicians, projects and sessions.
"""
import json
import os
import time
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("playwright")
from playwright.sync_api import Page, expect  # noqa: E402

APP = os.environ.get("NATIVE_E2E_APP")
API = os.environ.get("NATIVE_E2E_API")
DSN = os.environ.get("NATIVE_E2E_DSN")
ADMIN = os.environ.get("NATIVE_E2E_ADMIN_KEY")
JWT_SECRET = os.environ.get("NATIVE_E2E_JWT_SECRET")

if not all([APP, API, DSN, ADMIN, JWT_SECRET]):
    pytest.skip("needs the throwaway stack (see the docstring)", allow_module_level=True)

import sys                                                      # noqa: E402
sys.path.insert(0, os.path.dirname(__file__))
from test_native_cutover import Clinician, _admin, _client, _pool, _session  # noqa: E402

httpx = pytest.importorskip("httpx")


@pytest.fixture(scope="module")
def db():
    with psycopg.connect(DSN, autocommit=True) as c:
        yield c


@pytest.fixture(scope="module")
def client():
    email = f"browser-{uuid.uuid4().hex[:6]}@e2e.test"
    return _client(_admin("POST", "/admin/api-key", json={"email": email})["api_key"])


def _project_with_cases(client, db, n, reviewers=1):
    pid = client.post("/projects", json={"name": "E2E browser", "template": "model_evaluation",
                                         "classes": ["Normal", "Abnormal"]}).json()["project_id"]
    db.execute("update project_submissions set eval_config = jsonb_set(eval_config,"
               " '{reviewers_per_item}', %s::jsonb) where id = %s", (str(reviewers), pid))
    items = [{"case_id": f"BROWSE-{i}", "scenario": f"Patient message {i}", "prediction": "Normal"}
             for i in range(n)]
    assert client.post("/ingest", json={"project_id": pid, "items": items}).status_code == 200
    end = time.monotonic() + 30
    while time.monotonic() < end:
        if db.execute("select count(*) from project_items where project_id = %s and status = 'queued'",
                      (pid,)).fetchone()[0] == n:
            return pid
        time.sleep(0.5)
    raise AssertionError("items were not released")


def _signed_in(page: Page, db, doc: Clinician):
    """The session the platform would have issued, set as its cookie."""
    token = _session(db, doc.id, doc.email)
    page.context.add_cookies([{
        "name": "sessionToken", "value": token,
        "domain": "127.0.0.1", "path": "/", "httpOnly": True, "sameSite": "Lax",
    }])


def _open_workspace(page: Page, pool: str):
    page.goto(f"{APP}/workspace?pool={pool}", wait_until="networkidle")


def _shown_case(page: Page) -> str:
    """The case id the workspace is showing, read from the page itself."""
    expect(page.get_by_text("BROWSE-", exact=False).first).to_be_visible(timeout=20_000)
    return page.get_by_text("BROWSE-", exact=False).first.inner_text()


def _lease(db, clinician_id):
    return db.execute(
        "select task_no, status from item_reservations where clinician_id = %s"
        " and status = 'active' and expires_at > now()", (clinician_id,)).fetchone()


# ── a case is held the moment the browser is shown it ──────────────────────────

def test_opening_the_workspace_holds_the_case_it_shows(page: Page, db, client):
    doc = Clinician(db, "br-hold")
    pid = _project_with_cases(client, db, 2)
    pool = _pool(db, pid, [doc])
    _signed_in(page, db, doc)
    _open_workspace(page, pool)

    case = _shown_case(page)
    assert "BROWSE-" in case, case
    held = _lease(db, doc.id)
    assert held is not None, "the browser was shown a case nobody holds"


def test_two_browsers_are_never_shown_the_same_case(page: Page, browser, db, client):
    """Duplicate assignment, as a clinician would meet it: two signed-in browsers."""
    one, two = Clinician(db, "br-a"), Clinician(db, "br-b")
    pid = _project_with_cases(client, db, 2, reviewers=1)
    pool = _pool(db, pid, [one, two])

    _signed_in(page, db, one)
    _open_workspace(page, pool)
    first = _shown_case(page)

    other = browser.new_context()
    second_page = other.new_page()
    _signed_in(second_page, db, two)
    _open_workspace(second_page, pool)
    try:
        second = _shown_case(second_page)
        assert second != first, f"both browsers were shown {first}"
        holders = db.execute(
            "select count(distinct task_no) from item_reservations where status = 'active'"
            " and project_id = %s", (pid,)).fetchone()[0]
        assert holders == 2
    finally:
        other.close()


def test_a_reload_returns_to_the_same_case(page: Page, db, client):
    doc = Clinician(db, "br-reload")
    pid = _project_with_cases(client, db, 4, reviewers=3)
    pool = _pool(db, pid, [doc])
    _signed_in(page, db, doc)
    _open_workspace(page, pool)
    first = _shown_case(page)

    for _ in range(3):
        page.reload(wait_until="networkidle")
        assert _shown_case(page) == first, "a reload drew a different case"

    assert db.execute("select count(*) from item_reservations where clinician_id = %s"
                      " and status = 'active'", (doc.id,)).fetchone()[0] == 1


# ── the heartbeat, watched ──────────────────────────────────────────────────────

def test_the_heartbeat_keeps_the_case_while_it_is_open(page: Page, db, client):
    """The lease's expiry moves on while the page sits there, without touching it."""
    doc = Clinician(db, "br-beat")
    pid = _project_with_cases(client, db, 1)
    pool = _pool(db, pid, [doc])
    _signed_in(page, db, doc)
    _open_workspace(page, pool)
    _shown_case(page)

    task, _ = _lease(db, doc.id)
    db.execute("update item_reservations set expires_at = now() + interval '90 seconds'"
               " where task_no = %s", (task,))
    before = db.execute("select expires_at, renewals from item_reservations where task_no = %s",
                        (task,)).fetchone()

    # The stack runs a 3 second interval, so a renewal is due almost at once.
    end = time.monotonic() + 30
    while time.monotonic() < end:
        after = db.execute("select expires_at, renewals from item_reservations where task_no = %s",
                           (task,)).fetchone()
        if after[1] > before[1]:
            assert after[0] > before[0], "a renewal did not move the expiry"
            return
        time.sleep(1)
    raise AssertionError("the open page never renewed its lease")


def test_losing_the_lease_is_said_plainly_and_the_answers_are_kept(page: Page, db, client):
    """The case that must never be silent.

    A clinician typing into a case whose hold has gone must be told before they finish, and
    must not lose what they typed: their answers are the work, and the lease is not.
    """
    doc = Clinician(db, "br-lost")
    pid = _project_with_cases(client, db, 1)
    pool = _pool(db, pid, [doc])
    _signed_in(page, db, doc)
    _open_workspace(page, pool)
    _shown_case(page)
    task, _ = _lease(db, doc.id)

    # They type something. Any visible answer control will do; the notes box is always there.
    typed = "a finding worth keeping"
    box = page.get_by_role("textbox").first
    expect(box).to_be_visible(timeout=20_000)
    box.fill(typed)

    # The hold ends under them: the lease lapses, and the next beat finds out.
    db.execute("update item_reservations set expires_at = now() - interval '1 minute'"
               " where task_no = %s", (task,))

    warning = page.locator("#lease-lost")
    expect(warning).to_be_visible(timeout=30_000)
    text = warning.inner_text()
    assert "pool" in text.lower() or "no longer" in text.lower(), text

    # Said once, and still said: not a toast that vanishes.
    page.wait_for_timeout(5_000)
    expect(warning).to_be_visible()

    # And what they typed is exactly where it was.
    assert box.input_value() == typed, "the answers were lost with the lease"


def test_the_answers_survive_a_reload_after_the_lease_is_lost(page: Page, db, client):
    doc = Clinician(db, "br-draft")
    pid = _project_with_cases(client, db, 1)
    pool = _pool(db, pid, [doc])
    _signed_in(page, db, doc)
    _open_workspace(page, pool)
    _shown_case(page)
    task, _ = _lease(db, doc.id)

    typed = "kept across a reload"
    box = page.get_by_role("textbox").first
    expect(box).to_be_visible(timeout=20_000)
    box.fill(typed)
    page.wait_for_timeout(1_000)          # the draft is written as they type

    db.execute("update item_reservations set status = 'expired' where task_no = %s", (task,))
    page.reload(wait_until="networkidle")

    # The same case comes back (its slot is free again) with the draft still in it.
    _shown_case(page)
    again = page.get_by_role("textbox").first
    expect(again).to_be_visible(timeout=20_000)
    assert again.input_value() == typed


def test_a_beat_that_cannot_reach_the_store_retries_and_recovers(page: Page, db, client):
    """Retry and backoff, with a real outage and a real browser.

    The unit tests in the workforce repo prove the loop retries; this proves it survives
    the store actually going away, that it never claims the case is lost while it simply
    does not know, and that the case is still submittable afterwards.
    """
    import subprocess

    doc = Clinician(db, "br-retry")
    pid = _project_with_cases(client, db, 1)
    pool = _pool(db, pid, [doc])
    _signed_in(page, db, doc)
    _open_workspace(page, pool)
    _shown_case(page)
    task, _ = _lease(db, doc.id)

    typed = "written while the store was away"
    box = page.get_by_role("textbox").first
    expect(box).to_be_visible(timeout=20_000)
    box.fill(typed)

    # Paused rather than stopped: the stack's containers are created with --rm, so
    # stopping one deletes it.
    subprocess.run(["docker", "pause", "senebi-e2e-postgrest"], check=True, capture_output=True)
    try:
        page.wait_for_timeout(12_000)
        # Beats are failing, and the clinician has NOT been told the case is lost: we do
        # not know that, and saying it would be a guess with their work attached.
        assert page.locator("#lease-lost").count() == 0, \
            "a passing outage was reported as a lost case"
    finally:
        subprocess.run(["docker", "unpause", "senebi-e2e-postgrest"], check=True, capture_output=True)

    # It recovers by itself: a later beat lands and the lease keeps moving.
    before = db.execute("select renewals from item_reservations where task_no = %s",
                        (task,)).fetchone()[0]
    end = time.monotonic() + 60
    while time.monotonic() < end:
        now = db.execute("select renewals from item_reservations where task_no = %s",
                         (task,)).fetchone()[0]
        if now > before:
            break
        time.sleep(1)
    else:
        raise AssertionError("the loop never recovered after the store came back")

    # The renewal moving is the evidence a beat landed: it is the database's own record,
    # not something read off the page.
    # Nothing was lost either: what they typed is still there, and the case is still theirs.
    assert box.input_value() == typed
    assert _lease(db, doc.id) is not None, "the case stopped being theirs"
    assert page.locator("#lease-lost").count() == 0
