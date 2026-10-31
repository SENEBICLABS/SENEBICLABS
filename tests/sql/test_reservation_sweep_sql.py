"""
The lease sweep, against a real Postgres: migrations 010 and 011.

What an in-memory fake cannot show, and what this file is for: that two sweepers running at
the same moment do not both sweep, that a skipped run leaves no trace saying it ran, that a
failing sweep is not recorded as a success, and that a key which is not the service key can
neither call the sweep nor read its record.

011 is not in the template (see conftest.NEEDS_EXTENSIONS) because it needs pg_cron, which
this disposable server does not have. That is not a gap here: the last test applies it and
asserts it refuses clearly, which is the behaviour the migration promises.
"""
import json
import pathlib
import uuid

import psycopg
import pytest

HEALTH = pathlib.Path(__file__).resolve().parents[2]
M011 = HEALTH / "migrations" / "011_schedule_reservation_sweep.sql"


def _project_with_lease(c, *, expires: str):
    """A project with one case and one lease on it, due in `expires` (a SQL interval).

    Built the long way, like the other reservation tests, rather than through
    `reserve_next_item`: the point here is a lease at a chosen expiry, not the serving path.
    """
    pid, cid, pool = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    c.execute(
        "insert into project_submissions (id, name, email, company, description, eval_config, task_store)"
        " values (%s, 'P', 'c@x.com', 'Co', 'd', %s, 'native')",
        (pid, json.dumps({"reviewers_per_item": 3, "schema": {"fields": {}}})))
    c.execute("insert into clinicians (id, name, email, access_code) values (%s, 'Dr', %s, %s)",
              (cid, f"{cid[:8]}@x.com", cid))
    c.execute("insert into pools (id, name, ls_project_id, calibration_items, project_id)"
              " values (%s, 'Pool', 0, '[]', %s)", (pool, pid))
    task = c.execute("insert into project_items (project_id, idx, content, status)"
                     " values (%s, 0, %s, 'queued') returning task_no",
                     (pid, json.dumps({"prompt": "q"}))).fetchone()[0]
    c.execute("insert into item_reservations (task_no, project_id, pool_id, clinician_id,"
              " status, expires_at) values (%s, %s, %s, %s, 'active',"
              f" now() + interval '{expires}')", (task, pid, pool, cid))
    return pid, task


def _state(db):
    return db.execute("select name, last_run_at, last_expired, runs from ops_sweep_state"
                      " where name = 'reservations'").fetchone()


# ── it does the work ────────────────────────────────────────────────────────────

def test_a_lapsed_lease_is_retired_and_a_live_one_is_not(db):
    _, lapsed = _project_with_lease(db, expires="-3 minutes")
    _, live = _project_with_lease(db, expires="30 minutes")

    assert db.execute("select sweep_reservations()").fetchone()[0] == 1

    by_task = dict(db.execute("select task_no, status from item_reservations").fetchall())
    assert by_task[lapsed] == "expired"
    assert by_task[live] == "active", "a lease that has not lapsed must be left alone"


def test_the_sweep_records_when_it_finished_and_what_it_did(db):
    _project_with_lease(db, expires="-3 minutes")
    db.execute("select sweep_reservations()")

    name, last_run_at, last_expired, runs = _state(db)
    assert name == "reservations" and last_expired == 1 and runs == 1
    assert db.execute("select %s > now() - interval '1 minute'", (last_run_at,)).fetchone()[0]


# ── running it again is safe ─────────────────────────────────────────────────────

def test_running_it_twice_retires_nothing_the_second_time(db):
    _project_with_lease(db, expires="-3 minutes")
    assert db.execute("select sweep_reservations()").fetchone()[0] == 1
    assert db.execute("select sweep_reservations()").fetchone()[0] == 0


def test_a_run_with_nothing_to_do_still_counts_as_a_run(db):
    """Zero swept is a healthy sweep, not a missing one, so it must refresh the record."""
    db.execute("select sweep_reservations()")
    first_run, first_count = _state(db)[1], _state(db)[2]
    assert first_count == 0

    db.execute("select pg_sleep(0.05)")
    db.execute("select sweep_reservations()")
    name, second_run, second_count, runs = _state(db)
    assert runs == 2 and second_count == 0
    assert second_run > first_run, "a sweep that found nothing still finished"


# ── two at once ─────────────────────────────────────────────────────────────────

def test_a_second_sweeper_skips_rather_than_sweeping_alongside_the_first(dsn):
    """The lock, held across two real connections.

    The first session keeps its transaction open, so it still holds the transaction-scoped
    lock while the second asks. The second must report that it did not run, rather than
    sweeping the same rows concurrently.
    """
    with psycopg.connect(dsn, autocommit=True) as setup:
        _project_with_lease(setup, expires="-3 minutes")

    with psycopg.connect(dsn) as first, psycopg.connect(dsn, autocommit=True) as second:
        # Without the lock the second sweeper blocks on the first's row locks instead of
        # returning, so bound the wait: hanging is the failure, not a slow pass.
        second.execute("set statement_timeout = '5s'")
        swept = first.execute("select sweep_reservations()").fetchone()[0]   # transaction open
        assert swept == 1

        skipped = second.execute("select sweep_reservations()").fetchone()[0]
        assert skipped is None, "a sweeper that could not get the lock must say it did not run"

        first.commit()

    with psycopg.connect(dsn, autocommit=True) as after:
        assert _state(after)[3] == 1, "the skipped run must not be counted as a run"


def test_a_skipped_run_does_not_refresh_the_last_success(dsn):
    """The requirement that makes the stalled check trustworthy.

    If a skipped run touched the timestamp, a sweep that was permanently blocked would keep
    reporting itself as alive.
    """
    with psycopg.connect(dsn, autocommit=True) as setup:
        setup.execute("select sweep_reservations()")
        before = _state(setup)[1]

    with psycopg.connect(dsn) as holder, psycopg.connect(dsn, autocommit=True) as other:
        holder.execute("select pg_advisory_xact_lock(hashtext('sweep_reservations')::bigint)")
        assert other.execute("select sweep_reservations()").fetchone()[0] is None
        assert _state(other)[1] == before, "a skipped run moved the last-success timestamp"
        holder.rollback()


def test_the_lock_is_released_even_when_the_sweep_fails(dsn):
    """A transaction-scoped lock, so a failed sweep cannot wedge every later one."""
    with psycopg.connect(dsn, autocommit=True) as c:
        _project_with_lease(c, expires="-3 minutes")
        c.execute("create or replace function expire_reservations() returns integer"
                  " language plpgsql as $$ begin raise exception 'boom'; end; $$")
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("select sweep_reservations()")

        # Restored, and the next sweep gets the lock and works.
        c.execute("create or replace function expire_reservations() returns integer"
                  " language plpgsql as $$ declare n integer; begin"
                  "  update item_reservations set status = 'expired'"
                  "   where status = 'active' and expires_at <= now();"
                  "  get diagnostics n = row_count; return n; end; $$")
        assert c.execute("select sweep_reservations()").fetchone()[0] == 1


# ── failure is never a success ──────────────────────────────────────────────────

def test_a_failing_sweep_records_nothing(db):
    db.execute("create or replace function expire_reservations() returns integer"
               " language plpgsql as $$ begin raise exception 'boom'; end; $$")
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("select sweep_reservations()")
    assert _state(db) is None, "a sweep that raised was recorded as having run"


def test_a_failing_sweep_leaves_the_previous_success_in_place(db):
    """The whole function is one statement, so the state write rolls back with the failure."""
    db.execute("select sweep_reservations()")
    good_run, good_runs = _state(db)[1], _state(db)[3]

    db.execute("create or replace function expire_reservations() returns integer"
               " language plpgsql as $$ begin raise exception 'boom'; end; $$")
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("select sweep_reservations()")

    name, last_run_at, last_expired, runs = _state(db)
    assert last_run_at == good_run and runs == good_runs, \
        "a failure moved the timestamp that says the sweep is healthy"


# ── who may call it, and who may read the record ────────────────────────────────
#
# A note on why these are written the way they are. Supabase hands anon and authenticated
# broad privileges on new objects in `public`: on the production project today they hold
# select, insert, update and delete on `item_reservations`, and only row level security
# keeps them out of it. This disposable Postgres grants no such thing. So a test that only
# checked "anon cannot read" would pass on a database where the revokes had never run, and
# prove nothing about them. Each test below therefore grants what the platform would first.

def test_reapplying_010_strips_the_blanket_grants_the_platform_hands_out(db):
    """The revokes in 010, doing the job they are there for."""
    db.execute("grant select, insert, update, delete on ops_sweep_state to anon, authenticated")
    db.execute("grant execute on function sweep_reservations() to anon, authenticated")
    assert db.execute("select has_table_privilege('anon', 'ops_sweep_state', 'select')").fetchone()[0]
    assert db.execute(
        "select has_function_privilege('anon', 'sweep_reservations()', 'execute')").fetchone()[0]

    # Safe to re-run, as the header promises, and re-running restores the posture.
    db.execute((HEALTH / "migrations" / "010_reservation_sweep.sql").read_text())

    for role in ("anon", "authenticated"):
        for priv in ("select", "insert", "update", "delete"):
            assert not db.execute("select has_table_privilege(%s, 'ops_sweep_state', %s)",
                                  (role, priv)).fetchone()[0], f"{role} kept {priv}"
        assert not db.execute(
            "select has_function_privilege(%s, 'sweep_reservations()', 'execute')",
            (role,)).fetchone()[0], f"{role} kept execute on the sweep"

    # And the monitor keeps exactly what it needs: the record, not the sweep.
    assert db.execute(
        "select has_table_privilege('service_role', 'ops_sweep_state', 'select')").fetchone()[0]
    assert not db.execute(
        "select has_function_privilege('service_role', 'sweep_reservations()', 'execute')"
    ).fetchone()[0], "the service key should not be able to refresh the record itself"


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_granting_execute_still_does_not_buy_a_sweep(db, role):
    """Defence in depth, and the reason a stray grant is not a breach.

    Even handed EXECUTE on the wrapper, these roles cannot sweep: 009 revoked
    `expire_reservations()` from them, and the wrapper calls it as the invoker.
    """
    db.execute(f"grant execute on function sweep_reservations() to {role}")
    db.execute(f"set role {role}")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.execute("select sweep_reservations()")
    db.execute("reset role")


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_row_level_security_blocks_the_record_even_where_a_grant_exists(db, role):
    """The line of defence that actually holds on Supabase, where the grant is there."""
    db.execute("select sweep_reservations()")
    assert db.execute("select count(*) from ops_sweep_state").fetchone()[0] == 1

    db.execute(f"grant select on ops_sweep_state to {role}")
    db.execute(f"set role {role}")
    assert db.execute("select count(*) from ops_sweep_state").fetchone()[0] == 0, \
        "row level security let a key that is not the service key read the sweep record"
    db.execute("reset role")


def test_the_service_key_may_read_the_record(db):
    """What the monitor needs. `ops_health` reads this row and nothing else here."""
    db.execute("select sweep_reservations()")
    db.execute("set role service_role")
    assert db.execute("select count(*) from ops_sweep_state").fetchone()[0] == 1
    db.execute("reset role")


def test_row_level_security_is_enabled_on_the_record(db):
    assert db.execute("select relrowsecurity from pg_class where relname = 'ops_sweep_state'"
                      ).fetchone()[0] is True


# ── the migration that needs pg_cron refuses rather than half-applying ──────────

def test_scheduling_without_pg_cron_fails_with_the_owner_action_in_the_message(db):
    """011's promise: on a database without the extension it stops and says what to do.

    This server has no pg_cron, which is exactly the case the message is written for.
    """
    assert db.execute("select count(*) from pg_extension where extname = 'pg_cron'"
                      ).fetchone()[0] == 0

    with pytest.raises(psycopg.errors.FeatureNotSupported) as e:
        db.execute(M011.read_text())

    db.execute("rollback")   # the file's own `begin;` is still open and aborted

    assert "pg_cron is not enabled" in str(e.value)
    hint = (e.value.diag.message_hint or "")
    assert "Extensions" in hint and "owner" in hint, hint
    assert "Nothing in 011 has been applied" in hint

    # And it really applied nothing.
    assert db.execute("select count(*) from information_schema.schemata"
                      " where schema_name = 'cron'").fetchone()[0] == 0


def test_scheduling_without_migration_010_names_that_instead(db):
    """The other half-configured state: pg_cron present, the function missing."""
    db.execute("drop function if exists sweep_reservations()")
    # A stand-in for the extension, so the first guard passes and the second is reached.
    db.execute("create schema if not exists cron")
    db.execute("insert into pg_extension (oid, extname, extowner, extnamespace, extrelocatable,"
               " extversion) values (99999, 'pg_cron', 10, 'cron'::regnamespace, false, '1.6.4')")

    with pytest.raises(psycopg.errors.UndefinedFunction) as e:
        db.execute(M011.read_text())
    db.execute("rollback")

    assert "sweep_reservations() is missing" in str(e.value)
    assert "migration 010" in (e.value.diag.message_hint or "")
