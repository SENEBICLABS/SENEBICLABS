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


# ── the service key's privileges on the record and the sweep (migration 015) ────
#
# 010 revokes from `public, anon, authenticated` and never from `service_role`, so Supabase's
# default privileges for new objects in `public` survive: insert, update, delete and truncate
# on the table, and EXECUTE on the function. This disposable Postgres has no such default
# ACL, so each test recreates the state before proving the fix, as the grant tests above do.

M015 = HEALTH / "migrations" / "015_ops_sweep_state_service_role_acl.sql"


def _svc(db):
    row = db.execute(
        "select has_table_privilege('service_role','public.ops_sweep_state','select'),"
        "       has_table_privilege('service_role','public.ops_sweep_state','insert'),"
        "       has_table_privilege('service_role','public.ops_sweep_state','update'),"
        "       has_table_privilege('service_role','public.ops_sweep_state','delete'),"
        "       has_table_privilege('service_role','public.ops_sweep_state','truncate'),"
        "       has_function_privilege('service_role','public.sweep_reservations()','execute'),"
        "       has_function_privilege('service_role','public.expire_reservations()','execute')"
    ).fetchone()
    return dict(zip(("select", "insert", "update", "delete", "truncate",
                     "can_sweep", "can_expire"), row))


def _as_supabase_left_it(db):
    """What production read after 010: the defaults it never revoked from service_role."""
    db.execute("grant insert, update, delete, truncate, references, trigger"
               " on ops_sweep_state to service_role")
    db.execute("grant execute on function sweep_reservations() to service_role")


def _as_supabase_grants_the_lease_table(db):
    """`item_reservations` as production has it, which the two functions have to write.

    The note above the grant tests applies here too: on the production project `service_role`
    holds select, insert, update and delete on `item_reservations` from Supabase's defaults,
    and only row level security keeps it out. This disposable Postgres grants none of that,
    so a call that should be refused for want of EXECUTE on the *function* would otherwise
    fail on the table instead, and the test would pass for the wrong reason.
    """
    db.execute("grant select, insert, update, delete on item_reservations to service_role")
    db.execute("grant select on project_items, pools, clinicians to service_role")


INTENDED = {"select": True, "insert": False, "update": False, "delete": False,
            "truncate": False, "can_sweep": False, "can_expire": True}


def test_a_grant_does_not_remove_what_the_platform_already_handed_out(db):
    """Why 010's `grant select` was not enough, as a property of Postgres.

    Replays 010's pattern — revoke from public, anon and authenticated, then grant select to
    service_role — against a role that already holds the defaults, and shows the grant
    leaving them in place. Deliberately not an assertion about 010's text: if 010 is ever
    amended to revoke from service_role too, this still passes. Nothing here requires that
    file to stay wrong.
    """
    _as_supabase_left_it(db)
    assert _svc(db)["truncate"] is True, "the starting state this test needs was not created"

    db.execute("revoke all on ops_sweep_state from public, anon, authenticated")
    db.execute("grant select on ops_sweep_state to service_role")

    after = _svc(db)
    assert after["select"] is True
    assert after["truncate"] is True, \
        "a grant removed a privilege, which would mean 010 never needed a fix"
    assert after["can_sweep"] is True, "the function grant survived the table revoke, as it must"


def test_015_leaves_select_on_the_record_and_no_sweep(db):
    _as_supabase_left_it(db)
    db.execute(M015.read_text())
    assert _svc(db) == INTENDED

    acl = db.execute("select array_to_string(relacl::text[], ', ') from pg_class c"
                     " join pg_namespace n on n.oid=c.relnamespace"
                     " where n.nspname='public' and c.relname='ops_sweep_state'").fetchone()[0]
    assert "service_role=r/" in acl, acl
    fn = db.execute("select coalesce(array_to_string(proacl::text[], ', '), '(default)')"
                    " from pg_proc p join pg_namespace n on n.oid=p.pronamespace"
                    " where n.nspname='public' and p.proname='sweep_reservations'").fetchone()[0]
    assert "service_role" not in fn, fn


def test_015_keeps_the_drains_fallback_working(db):
    """The regression that would hurt, and the reason the two functions differ.

    `expire_reservations()` is granted to the service key on purpose in 009: the scoring
    drain calls it through RPC as the fallback that retires lapsed leases when the schedule
    is down. Revoking it would break that silently.
    """
    _as_supabase_left_it(db)
    _as_supabase_grants_the_lease_table(db)
    db.execute(M015.read_text())
    assert _svc(db)["can_expire"] is True, "015 broke the scoring drain's fallback sweep"
    # And the service key really can call it, not merely hold the privilege bit.
    db.execute("set role service_role")
    try:
        db.execute("select expire_reservations()")
    finally:
        db.execute("reset role")


def test_the_service_key_cannot_call_the_sweep_after_015(db):
    """The serious half. 010 says the only way to refresh the record is to be the scheduler;
    an EXECUTE grant to the service key is a way for anything holding it to refresh the
    heartbeat and hide a dead schedule from reservations_sweep_stalled."""
    _as_supabase_left_it(db)
    _as_supabase_grants_the_lease_table(db)
    db.execute("set role service_role")
    db.execute("select sweep_reservations()")          # possible before 015
    db.execute("reset role")

    db.execute(M015.read_text())
    db.execute("set role service_role")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute("select sweep_reservations()")
    finally:
        db.execute("reset role")


def test_015_converges_from_broad_grants_and_repeat_application(db):
    db.execute("grant all on ops_sweep_state to service_role")
    db.execute("grant execute on function sweep_reservations() to service_role")
    assert _svc(db)["delete"] is True and _svc(db)["can_sweep"] is True
    for _ in range(2):
        db.execute(M015.read_text())
        assert _svc(db) == INTENDED


def test_re_running_010_after_015_does_not_restore_the_grants(db):
    """010 advertises itself as safe to re-run, so this has to hold or the fix is temporary.

    It holds because `create table if not exists` is a no-op on an existing table and
    `create or replace function` preserves the existing ACL. The next test pins the one case
    where that is not true.
    """
    _as_supabase_left_it(db)
    db.execute(M015.read_text())
    db.execute((HEALTH / "migrations" / "010_reservation_sweep.sql").read_text())
    assert _svc(db) == INTENDED, "re-running 010 resurrected the unwanted privileges"


def test_dropping_and_recreating_the_sweep_would_restore_the_grant(db):
    """The residual risk 015 documents and cannot close.

    `create or replace function` keeps a function's ACL; `drop` plus `create` resets it to
    the schema's default privileges, which on Supabase hand EXECUTE back to service_role.
    Nothing in this repository drops it, and every migration here uses `create or replace`,
    but a future one that did would silently undo 015. Pinned so that it is a known
    property rather than a surprise.
    """
    _as_supabase_left_it(db)
    db.execute(M015.read_text())
    assert _svc(db)["can_sweep"] is False

    db.execute("alter default privileges in schema public grant execute on functions to service_role")
    db.execute("drop function sweep_reservations()")
    db.execute("create function sweep_reservations() returns integer language sql as"
               " $$ select 0 $$")
    assert _svc(db)["can_sweep"] is True, \
        "drop+create no longer resets the ACL, so 015's documented caveat is stale"


def test_015_refuses_when_010s_objects_are_absent(dsn_before_native):
    with psycopg.connect(dsn_before_native, autocommit=True) as db:
        assert db.execute("select to_regclass('public.ops_sweep_state')").fetchone()[0] is None
        with pytest.raises(psycopg.errors.UndefinedTable) as e:
            db.execute(M015.read_text())
        assert "ops_sweep_state is missing" in str(e.value)
        assert "Nothing in 015 has been applied" in str(e.value)


def test_015_touches_no_other_objects_acls(db):
    """Scope guard: ops_monitor_state and item_reservations must come out identical."""
    def acls():
        return db.execute("""
            select coalesce(string_agg(c.relname || '=' ||
                     coalesce(array_to_string(c.relacl::text[], ','), 'default'), ' | '
                     order by c.relname), '')
              from pg_class c join pg_namespace n on n.oid=c.relnamespace
             where n.nspname='public'
               and c.relname in ('ops_monitor_state','item_reservations','project_items')
        """).fetchone()[0]
    before = acls()
    db.execute(M015.read_text())
    assert acls() == before, "015 changed an ACL it should not have"

    stmts = [l.strip() for l in M015.read_text().splitlines()
             if l.strip() and not l.lstrip().startswith("--")
             and l.strip().split()[0].lower() in ("revoke", "grant")]
    assert stmts == ["revoke all on public.ops_sweep_state from service_role;",
                     "grant select on public.ops_sweep_state to service_role;",
                     "revoke all on function public.sweep_reservations() from service_role;"], stmts
    assert "expire_reservations() from" not in M015.read_text(), \
        "015 must never revoke expire_reservations()"
