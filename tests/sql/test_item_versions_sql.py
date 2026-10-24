"""
Re-uploading an item never destroys work (migrations/006_item_versions.sql), proved against
real Postgres: every outcome of ingest_item_version, concurrency on one row, and that a
clinician's answers survive any later upload.
"""
import json
import threading
import uuid

import psycopg
import pytest

from app.services.items import content_hash

RESULT = [{"from_name": "verdict", "to_name": "image", "type": "choices", "value": {"choices": ["Correct"]}}]


def _project(c, reviewers=1):
    pid = str(uuid.uuid4())
    c.execute("insert into project_submissions (id, name, email, company, description, eval_config, task_store)"
              " values (%s, 'P', 'c@x.com', 'Co', 'd', %s, 'native')",
              (pid, json.dumps({"reviewers_per_item": reviewers, "schema": {"fields": {}}})))
    return pid


def _ingest(c, pid, idx, content):
    return c.execute("select outcome, item_id, task_no, previous_item_id from ingest_item_version(%s, %s, %s, %s)",
                     (pid, idx, json.dumps(content), content_hash(content))).fetchone()


def _answer(c, pid, task_no):
    doc = str(uuid.uuid4())
    c.execute("insert into clinicians (id, name, email, access_code) values (%s, 'Dr', %s, %s)", (doc, f"{doc[:6]}@x", doc))
    pool = c.execute("select id from pools where project_id = %s", (pid,)).fetchone()
    if not pool:
        pool_id = str(uuid.uuid4())
        c.execute("insert into pools (id, name, ls_project_id, calibration_items, project_id) values (%s, 'P', 0, '[]', %s)",
                  (pool_id, pid))
    else:
        pool_id = str(pool[0])
    c.execute("insert into pool_eligibility (clinician_id, pool_id, eligible) values (%s, %s, true)", (doc, pool_id))
    return c.execute("select native_submit_judgement(%s, %s, %s, %s, %s)",
                     (doc, pool_id, task_no, json.dumps(RESULT), json.dumps({"verdict": "Correct"}))).fetchone()[0]


V1 = {"image": "https://x/sign?token=a", "_image_key": "k1", "_image_sha256": "aaa", "prediction": "Normal"}
V1_RETRY = {**V1, "image": "https://x/sign?token=b"}          # same image, a fresh signed URL
V2 = {"image": "https://x/sign?token=c", "_image_key": "k2", "_image_sha256": "bbb", "prediction": "Normal"}


def test_a_new_row_is_created_and_a_retry_writes_nothing(db):
    pid = _project(db)
    out, item, task_no, prev = _ingest(db, pid, 0, V1)
    assert out == "created" and prev is None
    assert _ingest(db, pid, 0, V1_RETRY)[0] == "unchanged"       # signed URL differs; image does not
    assert db.execute("select count(*) from project_items where project_id = %s", (pid,)).fetchone()[0] == 1


def test_a_change_before_any_work_is_made_in_place(db):
    pid = _project(db)
    _, item, task_no, _ = _ingest(db, pid, 0, V1)
    out, item2, task_no2, prev = _ingest(db, pid, 0, V2)
    assert (out, item2, task_no2, prev) == ("updated", item, task_no, None)
    assert db.execute("select content->>'_image_key', version from project_items where id = %s",
                      (item,)).fetchone() == ("k2", 1)


def test_a_change_after_an_answer_keeps_the_answered_version(db):
    pid = _project(db)
    _, item, task_no, _ = _ingest(db, pid, 0, V1)
    db.execute("update project_items set status = 'queued' where id = %s", (item,))
    assert _answer(db, pid, task_no) == "claimed"

    out, new_item, new_task, prev = _ingest(db, pid, 0, V2)
    assert out == "superseded" and str(prev) == str(item) and new_task != task_no
    old = db.execute("select status, superseded_at is not null, content->>'_image_key' from project_items where id = %s",
                     (item,)).fetchone()
    assert old == ("superseded", True, "k1")                       # the old content, untouched
    assert db.execute("select version, supersedes, status from project_items where id = %s",
                      (new_item,)).fetchone() == (2, item, "pending")
    # The clinician's answer is still there, still on the version they judged.
    assert db.execute("select count(*) from task_answers where task_no = %s", (task_no,)).fetchone()[0] == 1
    # And the superseded version takes no further answers.
    assert _answer(db, pid, task_no) == "not_found"
    # Its task leaves the serving list; the new version joins it once released.
    db.execute("update project_items set status = 'queued' where id = %s", (new_item,))
    states = [r[0] for r in db.execute("select task_no from native_task_states(%s)", (pid,)).fetchall()]
    assert states == [new_task]


@pytest.mark.parametrize("work", ["completion", "flag", "review_item", "label"])
def test_any_kind_of_work_counts_as_started(db, work):
    pid = _project(db)
    _, item, task_no, _ = _ingest(db, pid, 0, V1)
    doc = str(uuid.uuid4())
    db.execute("insert into clinicians (id, name, email, access_code) values (%s, 'Dr', %s, %s)", (doc, f"{doc[:6]}@x", doc))
    pool = str(uuid.uuid4())
    db.execute("insert into pools (id, name, ls_project_id, calibration_items, project_id) values (%s, 'P', 0, '[]', %s)",
               (pool, pid))
    if work == "completion":
        db.execute("insert into task_completions (clinician_id, pool_id, ls_task_id, kind) values (%s, %s, %s, 'judgement')",
                   (doc, pool, task_no))
    elif work == "flag":
        db.execute("insert into task_flags (clinician_id, pool_id, ls_task_id) values (%s, %s, %s)", (doc, pool, task_no))
    elif work == "review_item":
        db.execute("insert into review_items (pool_id, ls_task_id, state, author_id) values (%s, %s, 'needs_review', %s)",
                   (pool, task_no, doc))
    else:
        db.execute("update project_items set label = '{\"verdict\": \"Correct\"}' where id = %s", (item,))
    assert _ingest(db, pid, 0, V2)[0] == "superseded"


def test_concurrent_uploads_of_one_row_leave_exactly_one_current_item(db, dsn):
    pid = _project(db)
    _, item, task_no, _ = _ingest(db, pid, 0, V1)
    db.execute("update project_items set status = 'queued' where id = %s", (item,))
    _answer(db, pid, task_no)
    versions = [{**V2, "_image_sha256": f"v{n}", "_image_key": f"k{n}"} for n in range(6)]
    outcomes, barrier = [], threading.Barrier(len(versions))

    def go(content):
        with psycopg.connect(dsn, autocommit=True) as c:
            barrier.wait()
            outcomes.append(_ingest(c, pid, 0, content)[0])

    threads = [threading.Thread(target=go, args=(v,)) for v in versions]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    current = db.execute("select count(*) from project_items where project_id = %s and idx = 0 and status <> 'superseded'",
                         (pid,)).fetchone()[0]
    assert current == 1
    # The first change supersedes the answered item; later ones edit the untouched new version.
    assert outcomes.count("superseded") == 1 and outcomes.count("updated") == 5
    assert db.execute("select count(*) from task_answers").fetchone()[0] == 1


def test_identical_concurrent_retries_create_one_item(db, dsn):
    pid = _project(db)
    outcomes, barrier = [], threading.Barrier(5)

    def go():
        with psycopg.connect(dsn, autocommit=True) as c:
            barrier.wait()
            outcomes.append(_ingest(c, pid, 3, V1)[0])

    threads = [threading.Thread(target=go) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["created"] + ["unchanged"] * 4
    assert db.execute("select count(*) from project_items where project_id = %s", (pid,)).fetchone()[0] == 1


def test_an_answered_item_cannot_be_deleted_but_its_project_can(db):
    pid = _project(db)
    _, item, task_no, _ = _ingest(db, pid, 0, V1)
    db.execute("update project_items set status = 'queued' where id = %s", (item,))
    _answer(db, pid, task_no)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute("delete from project_items where id = %s", (item,))
    assert db.execute("select count(*) from task_answers").fetchone()[0] == 1
    # Deleting the whole project is deliberate and takes everything of it together.
    db.execute("delete from pool_eligibility")
    db.execute("delete from pools")
    db.execute("delete from project_submissions where id = %s", (pid,))
    for t in ("project_items", "task_answers", "answer_events"):
        assert db.execute(f"select count(*) from {t}").fetchone()[0] == 0


def test_two_current_items_at_one_index_are_impossible(db):
    pid = _project(db)
    _ingest(db, pid, 0, V1)
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute("insert into project_items (project_id, idx) values (%s, 0)", (pid,))


def test_the_function_refuses_bad_input_and_the_anon_key(db):
    pid = _project(db)
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("select * from ingest_item_version(%s, 0, '[]'::jsonb, 'h')", (pid,))
    assert db.execute("select has_function_privilege('anon', 'ingest_item_version(uuid, integer, jsonb, text)', 'execute')"
                      ).fetchone()[0] is False
