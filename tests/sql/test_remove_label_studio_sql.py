"""
Removing Label Studio from the database, against real Postgres (see conftest.py):

  scripts/sql/delete_label_studio_test_data.sql   deletes exactly the Label Studio work it
      was reviewed against, with everything that hangs off it, records what it deleted,
      and leaves every other row alone. On anything unexpected it changes nothing.
  scripts/label_studio_precheck.py                 the read-only check run before it: READY
      exactly when the deletion's guards pass, and it writes nothing.
  migrations/008_remove_label_studio.sql           refuses while Label Studio work remains;
      afterwards makes the task store the only possible store and drops its schema.
"""
import json
import uuid

import psycopg
import pytest

from conftest import HEALTH
from scripts import label_studio_precheck as P
from scripts import schema_check as S

DELETE = (HEALTH / "scripts" / "sql" / "delete_label_studio_test_data.sql").read_text()
M008 = (HEALTH / "migrations" / "008_remove_label_studio.sql").read_text()
ACTOR = "Label Studio removal (reviewed SQL)"
KEPT_FN = DELETE[DELETE.index("create function pg_temp.ls_removal_kept"):DELETE.index("end $f$;") + len("end $f$;")]

UNRELATED = ("clinicians", "operators", "api_keys", "pool_eligibility", "task_completions",
             "task_answers", "answer_events", "review_items", "task_flags", "calibration_attempts",
             "clinical_failures", "project_clinicians", "ls_task_map")


def _project(c, store, stage="submitted", ls_project_id=None, items=0, status="new", images=(), staff=None):
    pid = str(uuid.uuid4())
    c.execute("insert into project_submissions (id, name, email, company, description, eval_config,"
              " task_store, stage, status, ls_project_id) values (%s, 'P', 'c@x.com', 'Co', 'd', '{}', %s, %s, %s, %s)",
              (pid, store, stage, status, ls_project_id))
    nos = []
    for i in range(items):
        content = {"_image_key": images[i]} if i < len(images) else {}
        nos.append(c.execute("insert into project_items (project_id, idx, content, status, label)"
                             " values (%s, %s, %s, 'done', '{\"verdict\": \"Correct\"}') returning id, task_no",
                             (pid, i, json.dumps(content))).fetchone())
    for item_id, _ in nos:
        c.execute("insert into audit_events (item_id, project_id, action, source) values (%s, %s, 'label', 'label_studio')",
                  (item_id, pid))
    c.execute("insert into clinical_failures (client_email, case_key, project_id) values ('c@x.com', %s, %s)",
              (f"K-{pid[:6]}", pid))
    if staff:
        c.execute("insert into project_clinicians (project_id, clinician_id) values (%s, %s)", (pid, staff))
    return pid, nos


def _clinician(c):
    cid = str(uuid.uuid4())
    c.execute("insert into clinicians (id, name, email, access_code) values (%s, 'Dr', %s, %s)",
              (cid, f"{cid[:8]}@x.com", cid))
    return cid


def _pool(c, project_id=None, ls_project_id=191, closed=False, worked_by=None, task=18193):
    pool = str(uuid.uuid4())
    c.execute("insert into pools (id, name, ls_project_id, calibration_items, project_id, closed_at)"
              " values (%s, 'Pool', %s, '[]', %s, case when %s then now() end)",
              (pool, ls_project_id, project_id, closed))
    if worked_by:
        c.execute("insert into pool_eligibility (clinician_id, pool_id, eligible) values (%s, %s, true)",
                  (worked_by, pool))
        c.execute("insert into calibration_attempts (clinician_id, pool_id, score, passed) values (%s, %s, 1, true)",
                  (worked_by, pool))
        c.execute("insert into task_completions (clinician_id, pool_id, ls_task_id, kind) values (%s, %s, %s, 'judgement')",
                  (worked_by, pool, task))
        c.execute("insert into task_flags (clinician_id, pool_id, ls_task_id) values (%s, %s, %s)",
                  (worked_by, pool, task))
        c.execute("insert into review_items (pool_id, ls_task_id) values (%s, %s)", (pool, task))
    return pool


def _production_like(c):
    """Production on 28 Oct 2026: four Label Studio projects, all internal test work whatever
    their stage (two of them 'delivered'), and five Label Studio pools including the demo
    pool 194; plus native work, people and keys the deletion must not touch."""
    doc = _clinician(c)
    c.execute("insert into operators (name, email, key_hash) values ('Op', 'op@x.com', 'oph')")
    c.execute("insert into api_keys (email, key_hash, last4) values ('c@x.com', 'h', '1234')")
    held, held_items = _project(c, "label_studio", "submitted", ls_project_id=191, items=2)
    c.execute("insert into ls_task_map (item_id, ls_instance, ls_project_id, ls_task_id, evidence)"
              " values (%s, 'gcp-2026', 191, 18193, 'f')", (held_items[0][0],))
    prod, _ = _project(c, "label_studio", "production", ls_project_id=192)
    done1, _ = _project(c, "label_studio", "delivered", ls_project_id=150, items=2, status="delivered",
                        images=("cid/only-this.png", "cid/shared.png"), staff=doc)
    done2, _ = _project(c, "label_studio", "delivered", ls_project_id=151, items=1, status="delivered", staff=doc)
    pools = [_pool(c, ls_project_id=190 + n, worked_by=doc if n == 0 else None) for n in range(5)]

    native, native_items = _project(c, "native", items=1, images=("cid/shared.png",), staff=doc)
    native_pool = _pool(c, project_id=native, ls_project_id=0, worked_by=doc, task=native_items[0][1])
    c.execute("insert into task_answers (task_no, project_id, pool_id, clinician_id, kind, result, answers)"
              " values (%s, %s, %s, %s, 'judgement', '[]', '{}')", (native_items[0][1], native, native_pool, doc))
    c.execute("insert into answer_events (task_no, project_id, kind) values (%s, %s, 'rebuild')",
              (native_items[0][1], native))
    return {"ls_projects": [held, prod, done1, done2], "delivered": [done1, done2], "ls_pools": pools,
            "native": native, "native_items": native_items, "native_pool": native_pool, "doc": doc}


def _rows(c, table, where="", *args):
    return sorted(json.dumps(r, default=str)
                  for (r,) in c.execute(f"select to_jsonb(t) from {table} t {where}", args).fetchall())


def _run(dsn, sql):
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(sql)


def _snapshot(c):
    return {t: _rows(c, t) for t in ("project_submissions", "project_items", "pools", "audit_events") + UNRELATED}


def _precheck(dsn):
    lines = []
    with psycopg.connect(dsn) as c:
        stops = P.precheck(c, out=lines.append)
    return stops, "\n".join(lines)


def test_the_precheck_is_ready_on_production_and_writes_nothing(dsn_before_008):
    with psycopg.connect(dsn_before_008, autocommit=True) as c:
        w = _production_like(c)
        before = _snapshot(c)
    stops, out = _precheck(dsn_before_008)
    assert stops == [], out
    assert "exactly 4 Label Studio projects (found 4)" in out
    assert "exactly 5 Label Studio pools (found 5)" in out
    assert "the 20 foreign keys into deleted tables are exactly the reviewed ones" in out
    assert "in the deletion set: 1" in out                                  # pool 194, reported
    assert "1 image keys only this work uses" in out and "1 shared with kept items" in out
    for pid in w["delivered"]:
        assert pid in out                                                   # delivered test work is listed
    assert "c@x.com" not in out                                             # no client email printed
    with psycopg.connect(dsn_before_008, autocommit=True) as c:
        assert _snapshot(c) == before


def test_it_deletes_exactly_the_label_studio_work_and_records_it(dsn_before_008):
    with psycopg.connect(dsn_before_008, autocommit=True) as c:
        w = _production_like(c)
        kept = {
            "project": c.execute("select to_jsonb(s) from project_submissions s where id = %s", (w["native"],)).fetchone(),
            "items": _rows(c, "project_items", "where project_id = %s", w["native"]),
            "pool": c.execute("select to_jsonb(p) from pools p where id = %s", (w["native_pool"],)).fetchone(),
            **{t: _rows(c, t) for t in ("task_answers", "answer_events", "clinicians", "operators", "api_keys")},
            "native_children": {t: _rows(c, t, "where pool_id = %s", w["native_pool"])
                                for t in ("task_completions", "task_flags", "review_items",
                                          "pool_eligibility", "calibration_attempts")},
            "native_project_rows": {t: _rows(c, t, "where project_id = %s", w["native"])
                                    for t in ("audit_events", "clinical_failures", "project_clinicians")},
        }

        c.execute(DELETE)

        assert c.execute("select count(*) from project_submissions where task_store <> 'native'").fetchone()[0] == 0
        assert c.execute("select count(*) from pools where project_id is null").fetchone()[0] == 0
        # Everything that hung off the Label Studio work went with it, delivered test work included.
        for table, col, ids in (("project_items", "project_id", w["ls_projects"]),
                                ("clinical_failures", "project_id", w["ls_projects"]),
                                ("project_clinicians", "project_id", w["ls_projects"]),
                                ("audit_events", "project_id", w["ls_projects"]),
                                ("task_completions", "pool_id", w["ls_pools"]),
                                ("task_flags", "pool_id", w["ls_pools"]),
                                ("review_items", "pool_id", w["ls_pools"]),
                                ("pool_eligibility", "pool_id", w["ls_pools"]),
                                ("calibration_attempts", "pool_id", w["ls_pools"])):
            assert c.execute(f"select count(*) from {table} where {col} = any(%s::uuid[])", (ids,)).fetchone()[0] == 0, table
        assert c.execute("select count(*) from ls_task_map").fetchone()[0] == 0
        assert c.execute("select count(*) from clinical_failures where project_id is null").fetchone()[0] == 0
        # The native project, its pool and everything of theirs, the people and the keys are untouched.
        assert c.execute("select to_jsonb(s) from project_submissions s where id = %s", (w["native"],)).fetchone() == kept["project"]
        assert _rows(c, "project_items", "where project_id = %s", w["native"]) == kept["items"]
        assert c.execute("select to_jsonb(p) from pools p where id = %s", (w["native_pool"],)).fetchone() == kept["pool"]
        for t in ("task_answers", "answer_events", "clinicians", "operators", "api_keys"):
            assert _rows(c, t) == kept[t], t
        for t, rows in kept["native_children"].items():
            assert _rows(c, t, "where pool_id = %s", w["native_pool"]) == rows, t
        for t, rows in kept["native_project_rows"].items():
            assert _rows(c, t, "where project_id = %s", w["native"]) == rows, t
        # What was deleted is recorded, in rows the deletion itself cannot remove.
        audit = c.execute("select action, project_id, value from audit_events where actor_name = %s", (ACTOR,)).fetchall()
        assert all(r[1] is None for r in audit)
        assert sorted(r[2]["project_id"] for r in audit if r[0] == "label_studio_project_deleted") == sorted(w["ls_projects"])
        assert sorted(r[2]["pool_id"] for r in audit if r[0] == "label_studio_pool_deleted") == sorted(w["ls_pools"])
        worked = next(r[2] for r in audit if r[2].get("pool_id") == w["ls_pools"][0])
        assert (worked["task_completions"], worked["review_items"], worked["eligible_clinicians"],
                worked["calibration_attempts"]) == (1, 1, 1, 1)
        done1 = next(r[2] for r in audit if r[2].get("project_id") == w["delivered"][0])
        assert (done1["stage"], done1["items"], done1["items_by_status"], done1["project_clinicians"]) == \
            ("delivered", 2, {"done": 2}, 1)
        # Only the image no kept item uses is left for the storage cleanup.
        assert done1["orphaned_image_keys"] == ["cid/only-this.png"]
        assert "@" not in json.dumps([r[2] for r in audit])           # no clinician or client identity copied


def _break(c, w, change):
    """One kind of data the deletion was not reviewed against."""
    ls_item = c.execute("select id, task_no from project_items where project_id = %s limit 1",
                        (w["delivered"][0],)).fetchone()
    native_no = w["native_items"][0][1]
    if change == "another_ls_project":
        _project(c, "label_studio", "delivered", ls_project_id=152)
    elif change == "another_ls_pool":
        _pool(c, ls_project_id=150, closed=True)
    elif change == "native_answer_in_ls_pool":
        c.execute("insert into task_answers (task_no, project_id, pool_id, kind, result, answers)"
                  " values (%s, %s, %s, 'judgement', '[]', '{}')", (native_no, w["native"], w["ls_pools"][1]))
    elif change == "native_answer_through_ls_review_row":
        review = c.execute("select id from review_items where pool_id = %s", (w["ls_pools"][0],)).fetchone()[0]
        c.execute("insert into task_answers (task_no, project_id, pool_id, kind, result, answers, review_item_id)"
                  " values (%s, %s, %s, 'approved_authoring', '[]', '{}', %s)",
                  (native_no, w["native"], w["native_pool"], review))
    elif change == "answer_on_ls_item":
        c.execute("insert into task_answers (task_no, project_id, pool_id, kind, result, answers)"
                  " values (%s, %s, %s, 'judgement', '[]', '{}')", (ls_item[1], w["delivered"][0], w["native_pool"]))
    elif change == "event_on_ls_item":
        c.execute("insert into answer_events (task_no, project_id, kind) values (%s, %s, 'rebuild')",
                  (ls_item[1], w["delivered"][0]))
    elif change == "native_pool_on_ls_project":
        c.execute("update pools set project_id = %s where id = %s", (w["ls_projects"][1], w["native_pool"]))
    elif change == "native_item_supersedes_ls_item":
        c.execute("update project_items set supersedes = %s where project_id = %s", (ls_item[0], w["native"]))
    elif change == "native_audit_on_ls_item":
        c.execute("insert into audit_events (item_id, project_id, action, source) values (%s, %s, 'label', 'native')",
                  (ls_item[0], w["native"]))
    elif change == "native_pool_records_ls_task_number":
        c.execute("insert into review_items (pool_id, ls_task_id) values (%s, %s)", (w["native_pool"], ls_item[1]))
    elif change == "unreviewed_foreign_key":
        c.execute("create table pool_notes (pool_id uuid references pools (id) on delete cascade)")
    elif change == "delete_trigger":
        c.execute("create function t_noop() returns trigger language plpgsql as 'begin return old; end'")
        c.execute("create trigger noop before delete on review_items for each row execute function t_noop()")
    elif change == "rewrite_rule":
        c.execute("create rule touch as on delete to pools do also update operators set name = 'x'")


# Each kind of unexpected data, and the guard that must catch it.
CHANGES = {
    "another_ls_project": "expected 4 Label Studio projects, found 5",
    "another_ls_pool": "expected 5 Label Studio pools, found 6",
    "native_answer_in_ls_pool": "holds 1 native answers",
    "native_answer_through_ls_review_row": "holds 1 native answers",
    "answer_on_ls_item": "holds 1 native answers",
    "event_on_ls_item": "holds 1 answer events",
    "native_pool_on_ls_project": "1 native pools point at a Label Studio project",
    "native_item_supersedes_ls_item": "1 items of other projects supersede",
    "native_audit_on_ls_item": "1 audit rows of other projects",
    "native_pool_records_ls_task_number": "1 rows in other pools record",
    "unreviewed_foreign_key": "unreviewed foreign keys: pool_notes.pool_id -> pools",
    "delete_trigger": "delete triggers would run: noop on review_items",
    "rewrite_rule": "rewrite rules would run: touch on pools",
}


@pytest.mark.parametrize("change", CHANGES)
def test_unexpected_data_stops_the_precheck_and_the_deletion_with_nothing_changed(dsn_before_008, change):
    with psycopg.connect(dsn_before_008, autocommit=True) as c:
        w = _production_like(c)
        _break(c, w, change)
        before = _snapshot(c)
    stops, out = _precheck(dsn_before_008)
    assert stops, out
    with pytest.raises(psycopg.errors.RaiseException, match="stop and review") as e:
        _run(dsn_before_008, DELETE)
    assert CHANGES[change] in str(e.value)
    with psycopg.connect(dsn_before_008, autocommit=True) as c:
        assert _snapshot(c) == before


def test_a_second_run_finds_nothing_and_refuses(dsn_before_008):
    with psycopg.connect(dsn_before_008, autocommit=True) as c:
        _production_like(c)
    _run(dsn_before_008, DELETE)
    with psycopg.connect(dsn_before_008, autocommit=True) as c:
        before = _snapshot(c)
    assert any("found 0" in s for s in _precheck(dsn_before_008)[0])
    with pytest.raises(psycopg.errors.RaiseException, match="expected 4 Label Studio projects, found 0"):
        _run(dsn_before_008, DELETE)
    with psycopg.connect(dsn_before_008, autocommit=True) as c:
        assert _snapshot(c) == before


def test_migration_008_refuses_while_label_studio_work_remains(dsn_before_008):
    with psycopg.connect(dsn_before_008, autocommit=True) as c:
        _production_like(c)
    with pytest.raises(psycopg.errors.RaiseException, match="run scripts/sql/delete_label_studio_test_data.sql first"):
        _run(dsn_before_008, M008)
    with psycopg.connect(dsn_before_008, autocommit=True) as c:
        assert c.execute("select to_regclass('ls_task_map')").fetchone()[0] is not None
        assert c.execute("select count(*) from information_schema.columns where table_name = 'project_submissions'"
                         " and column_name = 'ls_project_id'").fetchone()[0] == 1
        rep = S.inspect(c)
    assert any("migration 008 would refuse" in r for r in S.gate(rep, "workforce_022"))


def test_after_the_deletion_008_makes_the_task_store_the_only_store(dsn_before_008):
    with psycopg.connect(dsn_before_008, autocommit=True) as c:
        _production_like(c)
    _run(dsn_before_008, DELETE)
    _run(dsn_before_008, M008)
    _run(dsn_before_008, M008)                                       # safe to re-run
    with psycopg.connect(dsn_before_008, autocommit=True) as c:
        assert c.execute("select to_regclass('ls_task_map')").fetchone()[0] is None
        assert c.execute("select count(*) from information_schema.columns where table_name = 'project_submissions'"
                         " and column_name = 'ls_project_id'").fetchone()[0] == 0
        pid = c.execute("insert into project_submissions (name, email, company, description)"
                        " values ('P', 'c@x', 'C', 'd') returning task_store").fetchone()[0]
        assert pid == "native"
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("insert into project_submissions (name, email, company, description, task_store)"
                      " values ('P', 'c@x', 'C', 'd', 'label_studio')")
        rep = S.inspect(c)
    assert rep["steps"]["health_008"]["state"] == "applied"
    assert rep["checks"]["baseline_missing"] == []
    assert S.gate(rep, "health_008") == []


def _all_rows(c):
    tables = [t for (t,) in c.execute("select tablename from pg_tables where schemaname = 'public' order by 1")]
    return {t: _rows(c, t) for t in tables}


def test_every_table_outside_the_deletion_is_unchanged_row_for_row(dsn_before_008):
    with psycopg.connect(dsn_before_008, autocommit=True) as c:
        w = _production_like(c)
        ls_items = [str(i) for (i,) in c.execute("select id from project_items where project_id = any(%s::uuid[])",
                                                  (w["ls_projects"],))]
        before = _all_rows(c)
        c.execute(DELETE)
        after = _all_rows(c)
    touched = {"project_submissions", "project_items", "project_clinicians", "audit_events",
               "clinical_failures", "ls_task_map", "pools", "pool_eligibility", "calibration_attempts",
               "task_completions", "task_flags", "review_items"}
    for t in before:
        if t not in touched:
            assert after[t] == before[t], t                  # answers, events, people, keys, sessions...
    gone = set(w["ls_projects"]) | set(w["ls_pools"]) | set(ls_items)
    for t in touched:
        kept_before = [r for r in before[t] if not any(g in r for g in gone)]
        kept_after = [r for r in after[t] if ACTOR not in r]
        assert kept_after == kept_before, t


def test_a_failure_part_way_through_rolls_everything_back(dsn_before_008):
    """The project audit rows are written, then the pool audit insert fails: nothing stays."""
    with psycopg.connect(dsn_before_008, autocommit=True) as c:
        _production_like(c)
        c.execute("alter table audit_events add constraint no_pool_audit check (action <> 'label_studio_pool_deleted') not valid")
        before = _all_rows(c)
    with pytest.raises(psycopg.errors.CheckViolation):
        _run(dsn_before_008, DELETE)
    with psycopg.connect(dsn_before_008, autocommit=True) as c:
        assert _all_rows(c) == before
        assert c.execute("select to_regprocedure('pg_temp.ls_removal_kept(uuid[], uuid[], uuid[], text)')").fetchone()[0] is None


@pytest.mark.parametrize("change", [
    "update clinicians set name = 'Dr X'",
    "update operators set active = false",
    "update api_keys set revoked = true",
    "update task_answers set withdrawn_reason = 'x'",
    "delete from answer_events",
    "update task_completions set kind = 'review' where pool_id = %(native_pool)s",
    "update pools set name = 'Other' where id = %(native_pool)s",
    "update project_items set status = 'pending' where project_id = %(native)s",
    "update sessions set revoked_at = now()",
])
def test_the_preservation_check_sees_any_change_outside_the_work(dsn_before_008, change):
    with psycopg.connect(dsn_before_008, autocommit=True) as c:
        w = _production_like(c)
        c.execute("insert into sessions (clinician_id, token, expires_at) values (%s, 'tok', now() + interval '1 day')",
                  (w["doc"],))
        c.execute(KEPT_FN)
        args = (w["ls_projects"], w["ls_pools"], [i for (i,) in c.execute(
            "select id from project_items where project_id = any(%s::uuid[])", (w["ls_projects"],))], ACTOR)
        kept = "select pg_temp.ls_removal_kept(%s::uuid[], %s::uuid[], %s::uuid[], %s)"
        before = c.execute(kept, args).fetchone()[0]
        # Changing the Label Studio work itself is not a change to what is kept.
        c.execute("update task_completions set kind = 'review' where pool_id = %s", (w["ls_pools"][0],))
        c.execute("update project_submissions set stage_note = 'x' where id = %s", (w["delivered"][0],))
        assert c.execute(kept, args).fetchone()[0] == before
        c.execute(change, {"native": w["native"], "native_pool": w["native_pool"]})
        assert c.execute(kept, args).fetchone()[0] != before


@pytest.mark.parametrize("name", ["removal", "rollback", "refusal"])
def test_the_restore_rehearsal_passes_on_production_shaped_data(dsn_before_008, name):
    from scripts import rehearse_label_studio_removal as R
    with psycopg.connect(dsn_before_008, autocommit=True) as c:
        _production_like(c)
    lines = []
    check = R.Checks(out=lines.append)
    R.REHEARSALS[name](dsn_before_008, check)
    assert check.failed == [], "\n".join(lines)
    assert not any("c@x.com" in line or "Dr" in line for line in lines)      # counts, never row contents


def test_the_restore_rehearsal_notices_a_deletion_that_overreaches(dsn_before_008, monkeypatch):
    from scripts import rehearse_label_studio_removal as R
    with psycopg.connect(dsn_before_008, autocommit=True) as c:
        _production_like(c)
    monkeypatch.setattr(R, "DELETE", DELETE.replace("delete from pools where id = any(v_pools);",
                                                    "delete from pools where id = any(v_pools);\n"
                                                    "  delete from api_keys;"))
    monkeypatch.setattr(R, "DELETE", R.DELETE.replace(
        "if pg_temp.ls_removal_kept(v_projects, v_pools, v_items, ACTOR) <> v_kept then",
        "if false then"))
    check = R.Checks(out=lambda *_: None)
    R.rehearse_removal(dsn_before_008, check)
    assert any(f.startswith("api_keys") for f in check.failed)
