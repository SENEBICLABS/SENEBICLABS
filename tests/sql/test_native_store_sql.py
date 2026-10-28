"""
The native task store's guarantees, proved against real Postgres (see conftest.py).
"""
import json
import threading
import uuid

import psycopg
import pytest

from conftest import apply_native

RESULT = [{"from_name": "verdict", "to_name": "image", "type": "choices",
           "value": {"choices": ["Correct"]}}]
ANSWERS = {"verdict": "Correct"}


# ── seeding ─────────────────────────────────────────────────────────────────────

def _project(c, store="native", reviewers=3, **ec):
    pid = str(uuid.uuid4())
    config = {"reviewers_per_item": reviewers, "schema": {"fields": {}}, **ec}
    c.execute(
        "insert into project_submissions (id, name, email, company, description, eval_config, task_store)"
        " values (%s, 'P', 'c@x.com', 'Co', 'd', %s, %s)",
        (pid, json.dumps(config), store))
    return pid


def _item(c, project_id, status="queued", idx=0):
    row = c.execute(
        "insert into project_items (project_id, idx, content, status) values (%s, %s, %s, %s)"
        " returning id, task_no", (project_id, idx, json.dumps({"prompt": "q"}), status)).fetchone()
    return {"id": str(row[0]), "task_no": row[1]}


def _clinician(c):
    cid = str(uuid.uuid4())
    c.execute("insert into clinicians (id, name, email, access_code) values (%s, 'Dr', %s, %s)",
              (cid, f"{cid[:8]}@x.com", cid))
    return cid


def _pool(c, project_id, *clinicians, closed=False):
    pool = str(uuid.uuid4())
    c.execute(
        "insert into pools (id, name, ls_project_id, calibration_items, project_id, closed_at)"
        " values (%s, 'Pool', 0, '[]', %s, %s)",
        (pool, project_id, "now()" if closed else None))
    for cl in clinicians:
        c.execute("insert into pool_eligibility (clinician_id, pool_id, eligible) values (%s, %s, true)",
                  (cl, pool))
    return pool


def _submit(c, clinician, pool, task_no, result=RESULT, answers=ANSWERS):
    return c.execute("select native_submit_judgement(%s, %s, %s, %s, %s)",
                     (clinician, pool, task_no, json.dumps(result), json.dumps(answers))).fetchone()[0]


def _count(c, sql, *args):
    return c.execute(sql, args).fetchone()[0]


# ── task identity ───────────────────────────────────────────────────────────────

def test_existing_items_are_numbered_once_each_when_the_store_is_added(dsn_before_native):
    with psycopg.connect(dsn_before_native, autocommit=True) as c:
        pid = str(uuid.uuid4())
        c.execute("insert into project_submissions (id, name, email, company, description)"
                  " values (%s, 'P', 'c@x.com', 'Co', 'd')", (pid,))
        for i in range(25):
            c.execute("insert into project_items (project_id, idx) values (%s, %s)", (pid, i))
        apply_native(c)
        rows = c.execute("select task_no from project_items").fetchall()
        numbers = [r[0] for r in rows]
        assert len(numbers) == 25 and len(set(numbers)) == 25
        assert all(n >= 1_000_000_000 for n in numbers)
        # Re-running the migration renumbers nothing.
        apply_native(c)
        assert sorted(r[0] for r in c.execute("select task_no from project_items").fetchall()) == sorted(numbers)
        # Migration 005 leaves projects that existed before it marked label_studio.
        assert _count(c, "select count(*) from project_submissions where task_store = 'label_studio'") == 1


def test_new_items_get_a_number_and_a_label_studio_sized_one_is_refused(db):
    pid = _project(db)
    a, b = _item(db, pid), _item(db, pid, idx=1)
    assert a["task_no"] >= 1_000_000_000 and b["task_no"] > a["task_no"]
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("insert into project_items (project_id, task_no) values (%s, 18193)", (pid,))
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute("insert into project_items (project_id, task_no) values (%s, %s)", (pid, a["task_no"]))


# ── submitting a judgement ──────────────────────────────────────────────────────

def test_a_judgement_writes_answer_completion_and_event_together(db):
    pid = _project(db)
    t = _item(db, pid)
    doc = _clinician(db)
    pool = _pool(db, pid, doc)
    assert _submit(db, doc, pool, t["task_no"]) == "claimed"
    ans = db.execute("select clinician_id, kind, result, answers from task_answers").fetchall()
    assert len(ans) == 1 and str(ans[0][0]) == doc and ans[0][1] == "judgement"
    assert ans[0][2] == RESULT and ans[0][3] == ANSWERS
    assert _count(db, "select count(*) from task_completions where ls_task_id = %s and kind = 'judgement'",
                  t["task_no"]) == 1
    ev = db.execute("select kind, status, answer_id from answer_events").fetchall()
    assert ev == [("answer_recorded", "pending", ev[0][2])]


@pytest.mark.parametrize("case,expected", [
    ("unreleased", "unavailable"), ("ineligible", "not_found"), ("other_project", "not_found"),
    ("closed", "closed"), ("settled", "settled"),
    ("review_required", "wrong_phase"), ("empty_result", "invalid"), ("no_such_task", "not_found"),
])
def test_refusals_write_nothing(db, case, expected):
    extra = {"review_required": True} if case == "review_required" else {}
    pid = _project(db, **extra)
    t = _item(db, pid, status="pending" if case == "unreleased" else "queued")
    doc = _clinician(db)
    if case == "ineligible":
        pool = _pool(db, pid)
    elif case == "other_project":
        pool = _pool(db, _project(db), doc)
    else:
        pool = _pool(db, pid, doc, closed=(case == "closed"))
    if case == "settled":
        db.execute("insert into task_completions (clinician_id, pool_id, ls_task_id, kind)"
                   " values (%s, %s, %s, 'adjudication')", (_clinician(db), pool, t["task_no"]))
    task_no = 1_999_999_999 if case == "no_such_task" else t["task_no"]
    result = [] if case == "empty_result" else RESULT
    assert _submit(db, doc, pool, task_no, result=result) == expected
    assert _count(db, "select count(*) from task_answers") == 0
    assert _count(db, "select count(*) from answer_events") == 0
    assert _count(db, "select count(*) from task_completions where kind = 'judgement'") == 0


def test_the_same_clinician_cannot_answer_twice(db):
    pid = _project(db)
    t = _item(db, pid)
    doc = _clinician(db)
    pool = _pool(db, pid, doc)
    assert _submit(db, doc, pool, t["task_no"]) == "claimed"
    assert _submit(db, doc, pool, t["task_no"]) == "already"
    assert _count(db, "select count(*) from task_answers") == 1
    assert _count(db, "select count(*) from answer_events") == 1


def test_a_failure_part_way_through_leaves_no_answer_and_no_event(db):
    """The completion insert is the second write. Make it fail (an older completion for the
    same clinician and task already exists) and the answer written just before it must be
    gone too, with no event behind it."""
    pid = _project(db)
    t = _item(db, pid)
    doc = _clinician(db)
    pool = _pool(db, pid, doc)
    db.execute("insert into task_completions (clinician_id, pool_id, ls_task_id, kind)"
               " values (%s, %s, %s, 'judgement')", (doc, pool, t["task_no"]))
    assert _submit(db, doc, pool, t["task_no"]) == "already"
    assert _count(db, "select count(*) from task_answers") == 0
    assert _count(db, "select count(*) from answer_events") == 0


def test_the_ceiling_holds_when_clinicians_submit_at_the_same_moment(db, dsn):
    pid = _project(db, reviewers=3)
    t = _item(db, pid)
    doctors = [_clinician(db) for _ in range(8)]
    pool = _pool(db, pid, *doctors)
    outcomes, barrier = [], threading.Barrier(len(doctors))

    def go(doc):
        with psycopg.connect(dsn, autocommit=True) as c:
            barrier.wait()
            outcomes.append(_submit(c, doc, pool, t["task_no"]))

    threads = [threading.Thread(target=go, args=(d,)) for d in doctors]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sorted(outcomes) == ["claimed"] * 3 + ["full"] * 5
    assert _count(db, "select count(*) from task_answers") == 3
    assert _count(db, "select count(*) from answer_events") == 3
    assert _count(db, "select count(*) from task_completions where kind = 'judgement'") == 3


def test_task_states_count_answers_against_the_projects_own_ceiling(db):
    pid = _project(db, reviewers=2)
    a, b, unreleased = _item(db, pid), _item(db, pid, idx=1), _item(db, pid, idx=2, status="pending")
    d1, d2 = _clinician(db), _clinician(db)
    pool = _pool(db, pid, d1, d2)
    _submit(db, d1, pool, a["task_no"])
    _submit(db, d2, pool, a["task_no"])
    _submit(db, d1, pool, b["task_no"])
    states = db.execute("select task_no, total_annotations, is_labeled from native_task_states(%s)",
                        (pid,)).fetchall()
    assert states == [(a["task_no"], 2, True), (b["task_no"], 1, False)]
    assert unreleased["task_no"] not in [s[0] for s in states]


# ── approving authored work ─────────────────────────────────────────────────────

def _review_item(c, pool, task_no, author, state="needs_review"):
    rid = str(uuid.uuid4())
    c.execute("insert into review_items (id, pool_id, ls_task_id, state, author_id, authored_data)"
              " values (%s, %s, %s, %s, %s, %s)",
              (rid, pool, task_no, state, author, json.dumps({"answer": "draft"})))
    return rid


def _approve(c, rid, reviewer, action="approved"):
    return c.execute("select native_approve_review(%s, %s, %s, %s, %s)",
                     (rid, reviewer, action, json.dumps({"answer": "final"}),
                      json.dumps([{"from_name": "answer", "to_name": "image", "type": "textarea",
                                   "value": {"text": ["final"]}}]))).fetchone()[0]


def test_approval_publishes_the_authors_answer_in_one_transaction(db):
    pid = _project(db, reviewers=1, review_required=True)
    t = _item(db, pid)
    author, reader = _clinician(db), _clinician(db)
    pool = _pool(db, pid, author, reader)
    rid = _review_item(db, pool, t["task_no"], author)
    assert _approve(db, rid, reader, "edited") == "approved"
    ans = db.execute("select id, clinician_id, kind, review_item_id from task_answers").fetchone()
    assert str(ans[1]) == author and ans[2] == "approved_authoring" and str(ans[3]) == rid
    ri = db.execute("select state, reviewer_id, review_action, ls_annotation_id from review_items").fetchone()
    assert ri[0] == "approved" and str(ri[1]) == reader and ri[2] == "edited" and ri[3] == ans[0]
    assert _count(db, "select count(*) from task_completions where kind = 'review' and clinician_id = %s",
                  reader) == 1
    assert _count(db, "select count(*) from answer_events where kind = 'approval_recorded'") == 1


def test_an_author_cannot_approve_their_own_work(db):
    pid = _project(db, reviewers=1, review_required=True)
    t = _item(db, pid)
    author = _clinician(db)
    pool = _pool(db, pid, author)
    rid = _review_item(db, pool, t["task_no"], author)
    assert _approve(db, rid, author) == "own_work"
    assert _count(db, "select count(*) from task_answers") == 0
    assert db.execute("select state from review_items").fetchone()[0] == "needs_review"


def test_two_readers_approving_at_once_publish_one_answer(db, dsn):
    pid = _project(db, reviewers=1, review_required=True)
    t = _item(db, pid)
    author, r1, r2 = _clinician(db), _clinician(db), _clinician(db)
    pool = _pool(db, pid, author, r1, r2)
    rid = _review_item(db, pool, t["task_no"], author)
    outcomes, barrier = [], threading.Barrier(2)

    def go(reader):
        with psycopg.connect(dsn, autocommit=True) as c:
            barrier.wait()
            outcomes.append(_approve(c, rid, reader))

    threads = [threading.Thread(target=go, args=(r,)) for r in (r1, r2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sorted(outcomes) == ["approved", "conflict"]
    assert _count(db, "select count(*) from task_answers") == 1
    assert _count(db, "select count(*) from answer_events") == 1


def test_approval_refuses_a_draft_not_waiting_for_review(db):
    pid = _project(db, reviewers=1, review_required=True)
    t = _item(db, pid)
    author, reader = _clinician(db), _clinician(db)
    pool = _pool(db, pid, author, reader)
    rid = _review_item(db, pool, t["task_no"], author, state="needs_author")
    assert _approve(db, rid, reader) == "conflict"


# ── the outbox ──────────────────────────────────────────────────────────────────

def _claim(c, limit=20, stale=300):
    return c.execute("select event_id, task_no, project_id, upto_event_id, attempts"
                     " from claim_answer_events(%s, %s)", (limit, stale)).fetchall()


def _finish(c, event_id, ok, upto=None, error=None):
    c.execute("select finish_answer_event(%s, %s, %s, %s)", (event_id, ok, upto, error))


def _two_answers_on_one_task(db):
    pid = _project(db, reviewers=3)
    t = _item(db, pid)
    d1, d2, d3 = _clinician(db), _clinician(db), _clinician(db)
    pool = _pool(db, pid, d1, d2, d3)
    _submit(db, d1, pool, t["task_no"])
    _submit(db, d2, pool, t["task_no"])
    return pid, t, pool, d3


def test_one_claim_per_task_and_finishing_covers_what_was_read(db):
    pid, t, pool, d3 = _two_answers_on_one_task(db)
    claimed = _claim(db)
    assert len(claimed) == 1                       # two events, one task, one claim
    ev, task_no, _, upto, attempts = claimed[0]
    assert task_no == t["task_no"] and attempts == 1
    # Another worker gets nothing for this task while it is processing.
    assert _claim(db) == []
    # An answer arriving mid-processing is NOT covered: its event is newer than upto.
    _submit(db, d3, pool, t["task_no"])
    _finish(db, ev, True, upto)
    rows = db.execute("select status from answer_events order by id").fetchall()
    assert [r[0] for r in rows] == ["done", "done", "pending"]
    assert len(_claim(db)) == 1


def _one_answer(db):
    pid = _project(db, reviewers=3)
    t = _item(db, pid)
    d1 = _clinician(db)
    _submit(db, d1, _pool(db, pid, d1), t["task_no"])
    return pid


def test_a_failed_event_is_retried_then_parked_as_failed(db):
    pid = _one_answer(db)
    for attempt in range(1, 6):
        (ev, _, _, upto, attempts), = _claim(db)
        assert attempts == attempt
        _finish(db, ev, False, upto, "boom")
    row = db.execute("select status, attempts, last_error from answer_events where id = %s", (ev,)).fetchone()
    assert row == ("failed", 5, "boom")
    # Failed is visible and stays put until an operator asks for a retry.
    assert _claim(db) == []
    assert _count(db, "select retry_failed_events(%s)", pid) == 1
    assert [e[0] for e in _claim(db)] == [ev]


def test_a_later_success_on_the_task_resolves_an_earlier_failure(db):
    _two_answers_on_one_task(db)
    for _ in range(5):
        (first, _, _, upto, _), = _claim(db)
        _finish(db, first, False, first, "boom")     # covers only itself
    assert db.execute("select status from answer_events where id = %s", (first,)).fetchone()[0] == "failed"
    (second, _, _, upto, _), = _claim(db)
    assert second != first
    _finish(db, second, True, upto)
    assert [r[0] for r in db.execute("select status from answer_events order by id")] == ["done", "done"]


def test_an_abandoned_event_is_picked_up_again_after_a_restart(db):
    _two_answers_on_one_task(db)
    (ev, *_), = _claim(db)
    db.execute("update answer_events set locked_at = now() - interval '10 minutes' where id = %s", (ev,))
    again = _claim(db, stale=300)
    assert [e[0] for e in again] == [ev] and again[0][4] == 2


def test_only_one_of_many_concurrent_workers_gets_a_task(db, dsn):
    _two_answers_on_one_task(db)
    got, barrier = [], threading.Barrier(6)

    def go():
        with psycopg.connect(dsn, autocommit=True) as c:
            barrier.wait()
            got.extend(_claim(c))

    threads = [threading.Thread(target=go) for _ in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(got) == 1


def test_rebuild_queues_every_released_task(db):
    pid = _project(db)
    _item(db, pid)
    _item(db, pid, idx=1)
    _item(db, pid, idx=2, status="pending")
    assert _count(db, "select request_rebuild(%s)", pid) == 2
    assert _count(db, "select count(*) from answer_events where kind = 'rebuild'") == 2


# ── deletion and privileges ─────────────────────────────────────────────────────

def test_deleting_a_project_removes_its_tasks_answers_and_events(db):
    pid, t, pool, _ = _two_answers_on_one_task(db)
    db.execute("delete from pools where id = %s", (pool,))
    db.execute("delete from project_submissions where id = %s", (pid,))
    for table in ("project_items", "task_answers", "answer_events"):
        assert _count(db, f"select count(*) from {table}") == 0


def test_a_project_with_a_pool_cannot_be_deleted_out_from_under_it(db):
    pid, *_ = _two_answers_on_one_task(db)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute("delete from project_submissions where id = %s", (pid,))


def test_only_one_pool_per_native_project(db):
    pid = _project(db)
    _pool(db, pid)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _pool(db, pid)


@pytest.mark.parametrize("fn", [
    "native_submit_judgement(uuid, uuid, integer, jsonb, jsonb)",
    "native_approve_review(uuid, uuid, text, jsonb, jsonb)",
    "native_task_states(uuid)", "claim_answer_events(integer, integer)",
    "finish_answer_event(bigint, boolean, bigint, text, integer)",
    "request_rebuild(uuid)", "retry_failed_events(uuid)",
])
def test_the_anon_key_cannot_call_the_store(db, fn):
    for role in ("anon", "authenticated"):
        assert db.execute("select has_function_privilege(%s, %s, 'execute')", (role, fn)).fetchone()[0] is False
