"""
Serving a case holds it, proved against real Postgres (see conftest.py).

The defect these tests exist for: `/next` picked the lowest open case and returned it
without recording anything, so clinicians asking at the same moment were handed the same
case. All but one then had their submission refused, correctly, after they had already
read the case and formed a judgement. The refusal was never the bug; the wasted reading
was. Migration 009 records a reservation at the moment a case is served.

The property under test is not exclusivity. A project commissioning three reviews wants
three clinicians on a case and no more, so these tests assert the configured number of
distinct holders rather than one.
"""
import json
import pathlib
import threading
import uuid

import psycopg
import pytest

RESULT = [{"from_name": "verdict", "to_name": "image", "type": "choices",
           "value": {"choices": ["Correct"]}}]
ANSWERS = {"verdict": "Correct"}


def _project(c, reviewers=3):
    pid = str(uuid.uuid4())
    c.execute(
        "insert into project_submissions (id, name, email, company, description, eval_config, task_store)"
        " values (%s, 'P', 'c@x.com', 'Co', 'd', %s, 'native')",
        (pid, json.dumps({"reviewers_per_item": reviewers, "schema": {"fields": {}}})))
    return pid


def _items(c, project_id, n, status="queued", first_idx=0):
    out = []
    for i in range(first_idx, first_idx + n):
        row = c.execute(
            "insert into project_items (project_id, idx, content, status) values (%s, %s, %s, %s)"
            " returning task_no", (project_id, i, json.dumps({"prompt": f"q{i}"}), status)).fetchone()
        out.append(row[0])
    return out


def _clinician(c):
    cid = str(uuid.uuid4())
    c.execute("insert into clinicians (id, name, email, access_code) values (%s, 'Dr', %s, %s)",
              (cid, f"{cid[:8]}@x.com", cid))
    return cid


def _pool(c, project_id, *clinicians, closed=False):
    pool = str(uuid.uuid4())
    c.execute(
        "insert into pools (id, name, ls_project_id, calibration_items, project_id, closed_at)"
        " values (%s, 'Pool', 0, '[]', %s, %s)", (pool, project_id, "now()" if closed else None))
    for cl in clinicians:
        c.execute("insert into pool_eligibility (clinician_id, pool_id, eligible) values (%s, %s, true)",
                  (cl, pool))
    return pool


def _reserve(c, clinician, pool, minutes=60):
    row = c.execute("select task_no from reserve_next_item(%s, %s, %s)",
                    (clinician, pool, minutes)).fetchone()
    return row[0] if row else None


def _submit(c, clinician, pool, task_no):
    return c.execute("select native_submit_judgement(%s, %s, %s, %s, %s)",
                     (clinician, pool, task_no, json.dumps(RESULT), json.dumps(ANSWERS))).fetchone()[0]


def _taken(c, task_no):
    return c.execute("select item_slots_taken(%s)", (task_no,)).fetchone()[0]


def _count(c, sql, *args):
    return c.execute(sql, args).fetchone()[0]


# ── the defect ──────────────────────────────────────────────────────────────────

def test_five_clinicians_asking_at_once_are_not_handed_the_same_case(db, dsn):
    """The measured failure: five clinicians, five identical task ids.

    Three is what the project commissioned, so three hold the case. The fourth and fifth
    get other work, which is the whole point: nobody reads a case they cannot submit.
    """
    pid = _project(db, reviewers=3)
    _items(db, pid, 5)
    doctors = [_clinician(db) for _ in range(5)]
    pool = _pool(db, pid, *doctors)
    served, barrier = [], threading.Barrier(len(doctors))

    def go(doc):
        with psycopg.connect(dsn, autocommit=True) as c:
            barrier.wait()
            served.append(_reserve(c, doc, pool))

    threads = [threading.Thread(target=go, args=(d,)) for d in doctors]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert None not in served, "every clinician had work available"
    assert len(served) == 5
    # No case is held past the number of reviews the client commissioned.
    counts = sorted(served.count(t) for t in set(served))
    assert max(counts) <= 3
    assert sum(counts) == 5
    # And each clinician holds exactly one.
    assert _count(db, "select count(*) from item_reservations where status = 'active'") == 5
    assert _count(db, "select count(distinct clinician_id) from item_reservations") == 5


def test_every_clinician_who_is_served_can_submit(db, dsn):
    """What the clinician actually feels: a case offered is a case that can be answered.

    Before migration 009 this run produced 343 refusals against 120 answers. The refusals
    are what this asserts on, not the total: how many of the 24 slots the concurrent phase
    happens to fill depends on thread timing, because a clinician who finds every case
    held at that instant stops asking. The sequential drain afterwards is what proves the
    commissioned readings are all still reachable.
    """
    pid = _project(db, reviewers=3)
    _items(db, pid, 8)
    doctors = [_clinician(db) for _ in range(12)]
    pool = _pool(db, pid, *doctors)
    outcomes, barrier = [], threading.Barrier(len(doctors))

    def go(doc):
        with psycopg.connect(dsn, autocommit=True) as c:
            barrier.wait()
            for _ in range(4):
                task = _reserve(c, doc, pool)
                if task is None:
                    outcomes.append("no_work")
                    break
                outcomes.append(_submit(c, doc, pool, task))

    threads = [threading.Thread(target=go, args=(d,)) for d in doctors]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    # Nobody was asked to read a case they could not then answer. This is the property.
    assert "full" not in outcomes, "a clinician read a case that was already at its ceiling"
    assert "already" not in outcomes, "a clinician was served a case they had answered"
    assert outcomes.count("claimed") >= 12, outcomes
    assert _count(db, "select count(*) from task_answers") == outcomes.count("claimed")

    # Drain what the race left, one clinician at a time: the ceiling must still be
    # reachable, and must still be a ceiling.
    for _ in range(40):
        if _count(db, "select count(*) from task_answers") == 24:
            break
        for doc in doctors:
            task = _reserve(db, doc, pool)
            if task is not None:
                assert _submit(db, doc, pool, task) == "claimed"
    assert _count(db, "select count(*) from task_answers") == 24   # 8 cases x 3 readings
    rows = db.execute("select count(*) from task_answers group by task_no").fetchall()
    assert [r[0] for r in rows] == [3] * 8
    # And with every reading in, nothing is being served from this project at all.
    assert all(_reserve(db, d, pool) is None for d in doctors)


def test_a_case_is_offered_to_three_different_clinicians_not_one(db):
    pid = _project(db, reviewers=3)
    (task,) = _items(db, pid, 1)
    doctors = [_clinician(db) for _ in range(5)]
    pool = _pool(db, pid, *doctors)

    served = [_reserve(db, d, pool) for d in doctors]
    assert served[:3] == [task] * 3
    assert served[3:] == [None, None], "no work left once the case is at its ceiling"
    assert _taken(db, task) == 3


# ── holding a case ──────────────────────────────────────────────────────────────

def test_asking_again_returns_the_same_case_rather_than_drawing_another(db):
    """A reload, a reconnect or a clinician coming back after lunch resumes."""
    pid = _project(db, reviewers=3)
    tasks = _items(db, pid, 4)
    doc = _clinician(db)
    pool = _pool(db, pid, doc)

    first = _reserve(db, doc, pool)
    assert first == tasks[0]
    assert [_reserve(db, doc, pool) for _ in range(4)] == [first] * 4
    assert _count(db, "select count(*) from item_reservations where status = 'active'") == 1
    # Once answered, they move on.
    assert _submit(db, doc, pool, first) == "claimed"
    assert _reserve(db, doc, pool) == tasks[1]


def test_a_clinician_never_holds_two_slots_on_one_case(db):
    pid = _project(db, reviewers=3)
    (task,) = _items(db, pid, 1)
    doc = _clinician(db)
    pool = _pool(db, pid, doc)
    _reserve(db, doc, pool)
    _reserve(db, doc, pool)
    assert _count(db, "select count(*) from item_reservations where task_no = %s", task) == 1
    assert _taken(db, task) == 1

    # The unique index is what guarantees it, not the query that avoids it.
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute("insert into item_reservations"
                   " (task_no, project_id, pool_id, clinician_id, expires_at)"
                   " values (%s, %s, %s, %s, now() + interval '1 hour')",
                   (task, pid, pool, doc))


def test_an_answer_and_its_lease_are_one_slot_not_two(db):
    """The lease must stop counting the moment its answer lands, settled or not."""
    pid = _project(db, reviewers=3)
    (task,) = _items(db, pid, 1)
    d1, d2, d3 = _clinician(db), _clinician(db), _clinician(db)
    pool = _pool(db, pid, d1, d2, d3)

    _reserve(db, d1, pool)
    assert _submit(db, d1, pool, task) == "claimed"
    # One answer, one still-active lease row, but one slot.
    assert _count(db, "select count(*) from item_reservations where status = 'active'") == 1
    assert _taken(db, task) == 1
    # So the other two still fit, and the case reaches its three readings.
    assert _reserve(db, d2, pool) == task
    assert _reserve(db, d3, pool) == task
    assert _submit(db, d2, pool, task) == "claimed"
    assert _submit(db, d3, pool, task) == "claimed"
    assert _count(db, "select count(*) from task_answers where task_no = %s", task) == 3


def test_settling_a_lease_tidies_the_row_without_changing_the_arithmetic(db):
    pid = _project(db, reviewers=3)
    (task,) = _items(db, pid, 1)
    doc = _clinician(db)
    pool = _pool(db, pid, doc)
    _reserve(db, doc, pool)
    _submit(db, doc, pool, task)
    before = _taken(db, task)
    assert db.execute("select settle_reservation(%s, %s)", (doc, task)).fetchone()[0] == "submitted"
    assert _taken(db, task) == before == 1
    assert _count(db, "select count(*) from item_reservations where status = 'submitted'") == 1


# ── giving a case back ──────────────────────────────────────────────────────────

def test_releasing_a_case_returns_its_slot_at_once(db):
    pid = _project(db, reviewers=1)
    (task,) = _items(db, pid, 1)
    d1, d2 = _clinician(db), _clinician(db)
    pool = _pool(db, pid, d1, d2)

    assert _reserve(db, d1, pool) == task
    assert _reserve(db, d2, pool) is None, "the case was at its ceiling of one"
    assert db.execute("select release_reservation(%s, %s, 'skipped')", (d1, task)).fetchone()[0] == "released"
    assert _reserve(db, d2, pool) == task
    assert _count(db, "select count(*) from item_reservations where release_reason = 'skipped'") == 1


def test_an_expired_lease_stops_counting_even_if_nothing_sweeps(db):
    """Recovery must not depend on a cron job: a dropped laptop frees the case by itself."""
    pid = _project(db, reviewers=1)
    (task,) = _items(db, pid, 1)
    d1, d2 = _clinician(db), _clinician(db)
    pool = _pool(db, pid, d1, d2)

    _reserve(db, d1, pool, minutes=5)
    assert _reserve(db, d2, pool) is None
    db.execute("update item_reservations set expires_at = now() - interval '1 minute'")
    assert _taken(db, task) == 0
    assert _reserve(db, d2, pool) == task
    # The sweep is housekeeping on top of that, not the thing that made it correct.
    assert db.execute("select expire_reservations()").fetchone()[0] == 1
    assert _count(db, "select count(*) from item_reservations where status = 'expired'") == 1


def test_a_lease_renews_a_bounded_number_of_times(db):
    pid = _project(db, reviewers=1)
    (task,) = _items(db, pid, 1)
    doc = _clinician(db)
    pool = _pool(db, pid, doc)
    _reserve(db, doc, pool)

    outcomes = [db.execute("select renew_reservation(%s, %s, 60, 4)", (doc, task)).fetchone()[0]
                for _ in range(5)]
    assert outcomes == ["renewed"] * 4 + ["limit_reached"]


def test_an_expired_lease_is_marked_expired_rather_than_renewed(db):
    pid = _project(db, reviewers=1)
    (task,) = _items(db, pid, 1)
    doc = _clinician(db)
    pool = _pool(db, pid, doc)
    _reserve(db, doc, pool)
    db.execute("update item_reservations set expires_at = now() - interval '1 second'")

    assert db.execute("select renew_reservation(%s, %s)", (doc, task)).fetchone()[0] == "expired"
    assert _count(db, "select count(*) from item_reservations where status = 'expired'") == 1
    assert db.execute("select renew_reservation(%s, %s)", (doc, task)).fetchone()[0] == "not_found"


def test_a_lease_outside_the_permitted_range_is_refused_not_clamped(db):
    pid = _project(db, reviewers=1)
    _items(db, pid, 1)
    doc = _clinician(db)
    pool = _pool(db, pid, doc)
    for bad in (0, 4, 481, 9999):
        try:
            _reserve(db, doc, pool, minutes=bad)
        except psycopg.errors.RaiseException:
            continue
        raise AssertionError(f"a {bad} minute lease was accepted")


# ── what is never served ────────────────────────────────────────────────────────

def test_nothing_is_served_from_a_closed_pool(db):
    pid = _project(db)
    _items(db, pid, 3)
    doc = _clinician(db)
    pool = _pool(db, pid, doc, closed=True)
    assert _reserve(db, doc, pool) is None
    assert _count(db, "select count(*) from item_reservations") == 0


def test_nothing_is_served_to_a_clinician_who_is_not_eligible(db):
    pid = _project(db)
    _items(db, pid, 3)
    doc, outsider = _clinician(db), _clinician(db)
    pool = _pool(db, pid, doc)
    assert _reserve(db, outsider, pool) is None
    assert _reserve(db, doc, pool) is not None


def test_an_unreleased_case_is_never_served(db):
    pid = _project(db)
    _items(db, pid, 2, status="pending")
    doc = _clinician(db)
    pool = _pool(db, pid, doc)
    assert _reserve(db, doc, pool) is None
    db.execute("update project_items set status = 'queued'")
    assert _reserve(db, doc, pool) is not None


def test_a_case_a_senior_has_settled_is_not_served_again(db):
    pid = _project(db, reviewers=3)
    first, second = _items(db, pid, 2)
    doc = _clinician(db)
    pool = _pool(db, pid, doc)
    db.execute("insert into task_completions (clinician_id, pool_id, ls_task_id, annotation_data, kind)"
               " values (%s, %s, %s, '[]', 'adjudication')", (doc, pool, first))
    assert _reserve(db, doc, pool) == second


def test_superseding_a_case_gives_its_lease_back(db):
    """Re-uploading replaces the content, so the lease on it is a lease on nothing."""
    pid = _project(db, reviewers=3)
    (task,) = _items(db, pid, 1)
    doc = _clinician(db)
    pool = _pool(db, pid, doc)
    assert _reserve(db, doc, pool) == task

    db.execute("select ingest_item_version(%s, 0, %s, 'hash-v2')",
               (pid, json.dumps({"prompt": "revised"})))
    row = db.execute("select status, release_reason from item_reservations").fetchone()
    assert row == ("released", "superseded")
    # The replacement arrives unreleased, so nothing is served from it until an operator
    # or /sync-pending releases it.
    assert _reserve(db, doc, pool) is None
    db.execute("update project_items set status = 'queued' where superseded_at is null")
    assert _reserve(db, doc, pool) not in (None, task)


def test_a_case_this_clinician_answered_is_never_offered_back(db):
    pid = _project(db, reviewers=3)
    first, second = _items(db, pid, 2)
    doc = _clinician(db)
    pool = _pool(db, pid, doc)
    _reserve(db, doc, pool)
    _submit(db, doc, pool, first)
    db.execute("select settle_reservation(%s, %s)", (doc, first))
    assert _reserve(db, doc, pool) == second


# ── counting a pool honestly ────────────────────────────────────────────────────

def _lease_states(c, project_id, clinician):
    return c.execute("select task_no, slots_taken, mine from item_lease_states(%s, %s)",
                     (project_id, clinician)).fetchall()


def test_lease_states_count_what_serving_would_count(db):
    """The pool card and the queue must agree, or a card offers an empty queue."""
    pid = _project(db, reviewers=2)
    first, second, third = _items(db, pid, 3)
    mine, other = _clinician(db), _clinician(db)
    pool = _pool(db, pid, mine, other)

    _reserve(db, other, pool)                    # other holds `first`
    assert _lease_states(db, pid, mine) == [(first, 1, False), (second, 0, False), (third, 0, False)]

    assert _reserve(db, mine, pool) == first     # one slot left on it, and it is theirs
    assert _lease_states(db, pid, mine) == [(first, 2, True), (second, 0, False), (third, 0, False)]


def test_a_case_this_clinician_answered_is_no_longer_theirs_to_finish(db):
    pid = _project(db, reviewers=2)
    (task,) = _items(db, pid, 1)
    doc = _clinician(db)
    pool = _pool(db, pid, doc)
    _reserve(db, doc, pool)
    assert _lease_states(db, pid, doc) == [(task, 1, True)]
    _submit(db, doc, pool, task)
    # One slot, not two, and no longer work in their hands.
    assert _lease_states(db, pid, doc) == [(task, 1, False)]


def test_unreleased_and_superseded_cases_are_not_counted(db):
    pid = _project(db, reviewers=2)
    (released,) = _items(db, pid, 1)
    _items(db, pid, 1, status="pending", first_idx=1)
    doc = _clinician(db)
    _pool(db, pid, doc)
    assert [r[0] for r in _lease_states(db, pid, doc)] == [released]
    db.execute("select ingest_item_version(%s, 0, %s, 'hash-v2')",
               (pid, json.dumps({"prompt": "revised"})))
    assert _lease_states(db, pid, doc) == []


def test_a_case_this_clinician_flagged_is_never_offered_back(db):
    """A flag means "not for me". Re-serving it would ask the same refusal twice."""
    pid = _project(db, reviewers=3)
    first, second = _items(db, pid, 2)
    doc = _clinician(db)
    pool = _pool(db, pid, doc)
    db.execute("insert into task_flags (clinician_id, pool_id, ls_task_id, reason)"
               " values (%s, %s, %s, 'unclear')", (doc, pool, first))
    assert _reserve(db, doc, pool) == second


def test_a_pass_of_any_kind_puts_a_case_out_of_reach(db):
    pid = _project(db, reviewers=3)
    first, second = _items(db, pid, 2)
    doc = _clinician(db)
    pool = _pool(db, pid, doc)
    db.execute("insert into task_completions (clinician_id, pool_id, ls_task_id, annotation_data, kind)"
               " values (%s, %s, %s, '[]', 'judgement')", (doc, pool, first))
    assert _reserve(db, doc, pool) == second


# ── renewal bounds ──────────────────────────────────────────────────────────────

def _renew(c, clinician, task_no, minutes=60, max_renewals=4, max_total=480):
    return c.execute("select renew_reservation(%s, %s, %s, %s, %s)",
                     (clinician, task_no, minutes, max_renewals, max_total)).fetchone()[0]


def test_a_lease_cannot_outlive_the_absolute_bound_however_often_it_renews(db):
    """The renewal count alone is not enough: a client that renews more often than we
    expect would keep a case out of circulation all day."""
    pid = _project(db, reviewers=1)
    (task,) = _items(db, pid, 1)
    doc = _clinician(db)
    pool = _pool(db, pid, doc)
    _reserve(db, doc, pool)

    # A lease older than the absolute bound is not renewed, it is retired.
    db.execute("update item_reservations set created_at = now() - interval '9 hours',"
               " expires_at = now() + interval '30 minutes'")
    assert _renew(db, doc, task, max_total=480) == "too_old"
    assert db.execute("select status, release_reason from item_reservations").fetchone() \
        == ("expired", "max_age")


def test_a_renewal_never_extends_past_the_absolute_bound(db):
    pid = _project(db, reviewers=1)
    (task,) = _items(db, pid, 1)
    doc = _clinician(db)
    pool = _pool(db, pid, doc)
    _reserve(db, doc, pool)
    # 30 minutes of life left on an 8 hour bound; a 60 minute renewal is clipped to it.
    db.execute("update item_reservations set created_at = now() - interval '7 hours 30 minutes'")
    assert _renew(db, doc, task, minutes=60, max_total=480) == "renewed"
    left = db.execute("select expires_at - now() from item_reservations").fetchone()[0]
    assert left.total_seconds() <= 30 * 60 + 5, left


def test_a_renewal_on_a_case_that_has_been_re_uploaded_is_refused(db):
    pid = _project(db, reviewers=1)
    (task,) = _items(db, pid, 1)
    doc = _clinician(db)
    pool = _pool(db, pid, doc)
    _reserve(db, doc, pool)
    # Bump the version without the supersede trigger, which is the case this guards:
    # a path that changes a version without releasing the lease.
    db.execute("update project_items set version = version + 1 where task_no = %s", (task,))
    assert _renew(db, doc, task) == "stale"
    assert db.execute("select status, release_reason from item_reservations").fetchone() \
        == ("released", "stale_version")


def test_a_clinician_cannot_renew_a_lease_that_is_not_theirs(db):
    pid = _project(db, reviewers=2)
    (task,) = _items(db, pid, 1)
    holder, other = _clinician(db), _clinician(db)
    pool = _pool(db, pid, holder, other)
    _reserve(db, holder, pool)
    # Ownership is (task, clinician). Another clinician's renewal finds nothing to renew,
    # and must not touch the holder's lease.
    assert _renew(db, other, task) == "not_found"
    assert db.execute("select clinician_id, status from item_reservations").fetchone() \
        == (uuid.UUID(holder), "active")


def test_an_out_of_range_absolute_bound_is_refused(db):
    pid = _project(db, reviewers=1)
    (task,) = _items(db, pid, 1)
    doc = _clinician(db)
    pool = _pool(db, pid, doc)
    _reserve(db, doc, pool)
    assert _renew(db, doc, task, minutes=60, max_total=30) == "invalid"      # below the lease
    assert _renew(db, doc, task, minutes=60, max_total=5000) == "invalid"    # beyond a day
    assert db.execute("select status from item_reservations").fetchone()[0] == "active"


# ── what a monitor sees ─────────────────────────────────────────────────────────

def test_reservation_health_tells_a_busy_project_from_a_starved_one(db):
    """The one failure no other check can see: work present, and nobody able to take it."""
    pid = _project(db, reviewers=1)
    first, second = _items(db, pid, 2)
    d1, d2 = _clinician(db), _clinician(db)
    pool = _pool(db, pid, d1, d2)

    def health():
        return db.execute("select open_items, servable_items, lease_blocked_items,"
                          " oldest_blocking_lease is not null from reservation_health()"
                          " where project_id = %s", (pid,)).fetchone()

    assert health() == (2, 2, 0, False)             # nothing held, everything servable

    _reserve(db, d1, pool)                          # one case held, one free
    assert health() == (2, 1, 1, True)

    _reserve(db, d2, pool)                          # both held: no work can be served
    assert health() == (2, 0, 2, True)

    # An answer, not a lease, is why a case is full: that is finished work, not starvation.
    db.execute("delete from item_reservations")
    _reserve(db, d1, pool)
    _submit(db, d1, pool, first)
    db.execute("select settle_reservation(%s, %s)", (d1, first))
    assert health() == (2, 1, 0, False), "a case filled by an answer is not lease-blocked"


def test_reservation_health_ignores_lapsed_leases_and_unreleased_items(db):
    pid = _project(db, reviewers=1)
    (task,) = _items(db, pid, 1)
    _items(db, pid, 2, status="pending", first_idx=1)
    doc = _clinician(db)
    pool = _pool(db, pid, doc)
    _reserve(db, doc, pool)

    row = db.execute("select open_items, servable_items, lease_blocked_items"
                     " from reservation_health() where project_id = %s", (pid,)).fetchone()
    assert row == (1, 0, 1), "unreleased items are not open work"

    db.execute("update item_reservations set expires_at = now() - interval '1 minute'")
    row = db.execute("select open_items, servable_items, lease_blocked_items"
                     " from reservation_health() where project_id = %s", (pid,)).fetchone()
    assert row == (1, 1, 0), "a lapsed lease blocks nothing"


# ── settling a disagreement ─────────────────────────────────────────────────────

def _hold(c, task_no):
    """Put a case in the state a split leaves it in."""
    c.execute("update project_items set status = 'needs_adjudication' where task_no = %s", (task_no,))


def _senior(c, pool, clinician):
    c.execute("update pool_eligibility set senior = true where pool_id = %s and clinician_id = %s",
              (pool, clinician))


def _adjudicate(c, clinician, pool, minutes=60):
    row = c.execute("select task_no from reserve_adjudication(%s, %s, %s)",
                    (clinician, pool, minutes)).fetchone()
    return row[0] if row else None


def test_a_held_case_goes_to_one_senior_and_the_others_get_nothing(db):
    """The policy: one senior settles it, and the decision is final."""
    pid = _project(db, reviewers=3)
    (task,) = _items(db, pid, 1)
    s1, s2, s3 = _clinician(db), _clinician(db), _clinician(db)
    pool = _pool(db, pid, s1, s2, s3)
    for s in (s1, s2, s3):
        _senior(db, pool, s)
    _hold(db, task)

    assert _adjudicate(db, s1, pool) == task
    assert _adjudicate(db, s2, pool) is None, "two seniors were given the same held case"
    assert _adjudicate(db, s3, pool) is None
    # Asking again resumes, so a reload does not lose the decision in progress.
    assert _adjudicate(db, s1, pool) == task
    assert _count(db, "select count(*) from item_reservations where task_no = %s"
                      " and kind = 'adjudication' and status = 'active'", task) == 1


def test_two_seniors_asking_at_the_same_instant_do_not_both_get_the_case(db, dsn):
    """The concurrency case, against real Postgres rather than reasoned about."""
    pid = _project(db, reviewers=3)
    _items(db, pid, 1)
    seniors = [_clinician(db) for _ in range(6)]
    pool = _pool(db, pid, *seniors)
    for s in seniors:
        _senior(db, pool, s)
    db.execute("update project_items set status = 'needs_adjudication'")

    got, barrier = [], threading.Barrier(len(seniors))

    def go(s):
        with psycopg.connect(dsn, autocommit=True) as c:
            barrier.wait()
            got.append(_adjudicate(c, s, pool))

    threads = [threading.Thread(target=go, args=(s,)) for s in seniors]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    served = [t for t in got if t is not None]
    assert len(served) == 1, f"the held case was assigned {len(served)} times: {got}"
    assert _count(db, "select count(*) from item_reservations where kind = 'adjudication'"
                      " and status = 'active'") == 1


def test_a_senior_who_read_the_case_is_never_asked_to_settle_it(db):
    """They are not an independent third reader of a split they are part of."""
    pid = _project(db, reviewers=1)
    (task,) = _items(db, pid, 1)
    reader, senior = _clinician(db), _clinician(db)
    pool = _pool(db, pid, reader, senior)
    _senior(db, pool, reader)            # the reader is also a senior
    _senior(db, pool, senior)
    _reserve(db, reader, pool)
    _submit(db, reader, pool, task)
    _hold(db, task)

    assert _adjudicate(db, reader, pool) is None, "a senior was offered their own reading"
    assert _adjudicate(db, senior, pool) == task


def test_a_case_already_settled_is_never_assigned_again(db):
    pid = _project(db, reviewers=3)
    (task,) = _items(db, pid, 1)
    s1, s2 = _clinician(db), _clinician(db)
    pool = _pool(db, pid, s1, s2)
    _senior(db, pool, s1)
    _senior(db, pool, s2)
    _hold(db, task)
    db.execute("insert into task_completions (clinician_id, pool_id, ls_task_id, annotation_data, kind)"
               " values (%s, %s, %s, '[]', 'adjudication')", (s1, pool, task))
    assert _adjudicate(db, s2, pool) is None


def test_a_senior_who_walks_away_releases_the_case_when_the_lease_lapses(db):
    """Abandonment is handled by the lease and nothing else: bounded, not permanent."""
    pid = _project(db, reviewers=3)
    (task,) = _items(db, pid, 1)
    s1, s2 = _clinician(db), _clinician(db)
    pool = _pool(db, pid, s1, s2)
    _senior(db, pool, s1)
    _senior(db, pool, s2)
    _hold(db, task)

    assert _adjudicate(db, s1, pool) == task
    assert _adjudicate(db, s2, pool) is None
    db.execute("update item_reservations set expires_at = now() - interval '1 minute'")
    assert _adjudicate(db, s2, pool) == task, "a lapsed hold did not free the case"
    # And the one who walked away does not get it back while the other holds it.
    assert _adjudicate(db, s1, pool) is None


def test_only_a_senior_is_ever_assigned_a_held_case(db):
    pid = _project(db, reviewers=3)
    (task,) = _items(db, pid, 1)
    ordinary, senior = _clinician(db), _clinician(db)
    pool = _pool(db, pid, ordinary, senior)
    _senior(db, pool, senior)
    _hold(db, task)
    assert _adjudicate(db, ordinary, pool) is None, "an ordinary clinician was asked to settle"
    assert _adjudicate(db, senior, pool) == task


def test_an_adjudication_hold_is_not_one_of_the_commissioned_readings(db):
    """A held case is already at its ceiling. Counting the senior's hold as a reading
    would make the arithmetic say the client commissioned one more than they did."""
    pid = _project(db, reviewers=2)
    (task,) = _items(db, pid, 1)
    r1, r2, senior = _clinician(db), _clinician(db), _clinician(db)
    pool = _pool(db, pid, r1, r2, senior)
    _senior(db, pool, senior)
    for r in (r1, r2):
        _reserve(db, r, pool)
        _submit(db, r, pool, task)
    _hold(db, task)
    before = _taken(db, task)
    assert _adjudicate(db, senior, pool) == task
    assert _taken(db, task) == before == 2, "the senior's hold was counted as a reading"


def test_a_held_case_is_never_served_as_ordinary_work(db):
    """`reserve_next_item` must not hand a held case to a reader as fresh work."""
    pid = _project(db, reviewers=3)
    first, second = _items(db, pid, 2)
    doc = _clinician(db)
    pool = _pool(db, pid, doc)
    _hold(db, first)
    assert _reserve(db, doc, pool) == second
    # Once the only ordinary case is answered, the held one is still not offered: it is a
    # decision for a senior, not fresh work for a reader.
    _submit(db, doc, pool, second)
    db.execute("select settle_reservation(%s, %s)", (doc, second))
    assert _reserve(db, doc, pool) is None


# ── the migration itself ────────────────────────────────────────────────────────

RESERVATION_FUNCTIONS = [
    "item_slots_taken(integer)",
    "item_lease_states(uuid,uuid)",
    "reservation_health()",
    "reserve_next_item(uuid,uuid,integer)",
    "reserve_adjudication(uuid,uuid,integer)",
    "renew_reservation(uuid,integer,integer,integer,integer)",
    "release_reservation(uuid,integer,text)",
    "settle_reservation(uuid,integer)",
    "expire_reservations()",
]


def test_the_service_role_can_call_every_reservation_function(db):
    """Revoking from `public` removes the default EXECUTE, and nothing restores it.

    Found the hard way: the functions existed, PostgREST listed them, and every call came
    back 42501, so serving answered 502 on every held case. The grant has to be in the
    migration rather than in whatever happens to run after it.
    """
    for sig in RESERVATION_FUNCTIONS:
        ok = db.execute("select has_function_privilege('service_role', %s, 'execute')",
                        (sig,)).fetchone()[0]
        assert ok, f"service_role cannot execute {sig}"


def test_the_anon_and_authenticated_roles_cannot_call_any_of_them(db):
    """The other half of the same line: a lease is not something a browser may take."""
    for sig in RESERVATION_FUNCTIONS:
        for role in ("anon", "authenticated"):
            ok = db.execute("select has_function_privilege(%s, %s, 'execute')",
                            (role, sig)).fetchone()[0]
            assert not ok, f"{role} can execute {sig}"


def test_only_one_renew_reservation_signature_exists(db):
    """`create or replace` cannot change arity, so an earlier four-argument version would
    survive as an overload and keep taking four-argument calls, silently skipping the
    bounds the five-argument one adds."""
    rows = db.execute("select pronargs from pg_proc where proname = 'renew_reservation'").fetchall()
    assert [r[0] for r in rows] == [5], rows


def test_the_migration_is_safe_to_run_twice(db):
    """Re-running must change nothing, including the grants and the lease kinds."""
    migration = (pathlib.Path(__file__).resolve().parents[2]
                 / "migrations" / "009_item_reservations.sql").read_text()
    pid = _project(db, reviewers=2)
    (task,) = _items(db, pid, 1)
    doc = _clinician(db)
    pool = _pool(db, pid, doc)
    assert _reserve(db, doc, pool) == task

    db.execute(migration)
    db.execute(migration)

    # The lease survived, and everything still works.
    assert db.execute("select count(*) from item_reservations where status = 'active'").fetchone()[0] == 1
    assert _taken(db, task) == 1
    assert db.execute("select has_function_privilege('service_role',"
                      " 'reserve_adjudication(uuid,uuid,integer)', 'execute')").fetchone()[0]
    assert [r[0] for r in db.execute("select pronargs from pg_proc"
                                     " where proname = 'renew_reservation'").fetchall()] == [5]
