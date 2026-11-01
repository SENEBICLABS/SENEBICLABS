-- Migration 015: the sweep's record is read-only to the service key, and the sweep is not
-- callable by it, both as 010 intended
-- Paste into the Supabase SQL editor (Dashboard -> SQL Editor -> New query -> Run).
-- Safe to re-run. Apply after 010. Independent of 011 to 014.
--
-- WHY
--
-- 010 is explicit about what it wanted:
--
--   "The difference from 009 is that `sweep_reservations()` gets no grant at all. It runs as
--    the role that schedules it in 011, which owns these objects, so no API caller needs to
--    reach it and none should: the only way to refresh the record below is to be the
--    scheduler."
--
-- It did not get that. Measured on production on 2026-11-01:
--
--   ops_sweep_state     acl: postgres=arwdDxtm/postgres, service_role=arwdDxtm/postgres
--   sweep_reservations  acl: postgres=X/postgres,         service_role=X/postgres
--
-- Same cause as 014: 010 revokes from `public, anon, authenticated` and never from
-- `service_role`, so Supabase's default privileges for new objects in `public` survive. For
-- tables that leaves insert, update, delete and truncate; for functions it leaves EXECUTE.
--
-- WHY THE FUNCTION GRANT IS THE SERIOUS HALF
--
-- `ops_sweep_state` is the record `reservations_sweep_stalled` reads to decide whether the
-- lease sweep is alive. 010's own header says why nothing but the scheduler may write it:
--
--   "It must NOT be changed to call `sweep_reservations()`: that would refresh the record
--    below, and a drain that happened to run would then hide a dead schedule from the very
--    check that is there to notice one."
--
-- An EXECUTE grant to `service_role` permits exactly that. And unlike the table privileges,
-- there is a live path to it: `service_role` cannot open a database connection, but PostgREST
-- publishes functions in exposed schemas as RPC, and `public` is exposed. So
-- `POST /rest/v1/rpc/sweep_reservations` with the service key would refresh the heartbeat and
-- leave `reservations_sweep_stalled` reporting `ok` over a schedule that had stopped. Nothing
-- in this repository does that, and the check exists precisely because a silent stall is the
-- failure that costs most.
--
-- WHAT THIS DELIBERATELY DOES NOT TOUCH
--
-- `expire_reservations()` keeps its EXECUTE for `service_role`, and that is not an oversight:
-- the scoring drain calls it through RPC (app/api/v1/project.py, `db.rpc("expire_reservations")`)
-- as the fallback that retires lapsed leases when the schedule is down. Revoking it would
-- break that quietly, so this file asserts at the end that it is still there.
--
-- The two functions therefore need opposite treatment, which is the one thing to get right
-- here: 009 grants `expire_reservations()` to the service key on purpose, and 010 meant to
-- withhold `sweep_reservations()`.

begin;

-- Stop clearly rather than leave a half-applied privilege change.
do $$
begin
  if to_regclass('public.ops_sweep_state') is null then
    raise exception 'ops_sweep_state is missing from this database'
      using errcode = 'undefined_table',
            hint = 'Apply migration 010 first. Nothing in 015 has been applied.';
  end if;

  if to_regprocedure('public.sweep_reservations()') is null then
    raise exception 'sweep_reservations() is missing from this database'
      using errcode = 'undefined_function',
            hint = 'Apply migration 010 first. Nothing in 015 has been applied.';
  end if;

  -- Checked before anything is revoked, because the assertion at the end of this file is
  -- about a privilege that has to have existed in the first place.
  if to_regprocedure('public.expire_reservations()') is null then
    raise exception 'expire_reservations() is missing from this database'
      using errcode = 'undefined_function',
            hint = 'Apply migration 009 first. 015 asserts that the scoring drain can still '
                   'call it, which needs it to exist. Nothing in 015 has been applied.';
  end if;
end $$;

-- The record: readable by the service key, writable by nobody but the owner.
--
-- Revoke before grant, which is the whole point. A grant adds to a privilege set; only a
-- revoke takes anything away, and `grant select` alone is what 010 did. In this order the
-- file converges on select-only from any starting state, including one somebody has widened
-- by hand.
--
-- `ops_health.reservation_sweep()` reads `name`, `last_run_at` and `last_expired` under the
-- service key and reports `reservations_sweep_stalled` from them. Reading is all it does.
revoke all on public.ops_sweep_state from service_role;
grant select on public.ops_sweep_state to service_role;

-- The sweep itself: the scheduler's to call, nobody else's. This is the grant that let the
-- heartbeat be refreshed from outside the schedule.
revoke all on function public.sweep_reservations() from service_role;

-- And the one that must survive, asserted rather than assumed.
--
-- This is here because the mistake this migration is most likely to grow into is revoking
-- the wrong function. `expire_reservations()` is the scoring drain's fallback sweep, reached
-- by RPC under the service key; without it a lapsed lease is only retired when pg_cron runs,
-- and the drain's own relief stops working silently. If a later edit takes it away, this
-- raises and the whole file rolls back.
do $$
begin
  if not has_function_privilege('service_role', 'public.expire_reservations()', 'execute') then
    raise exception 'service_role can no longer execute expire_reservations()'
      using errcode = 'insufficient_privilege',
            hint = 'The scoring drain calls it through RPC as its fallback sweep. 015 must '
                   'not revoke it. Nothing in 015 has been applied.';
  end if;
end $$;

commit;

-- To verify:
--
--   select has_table_privilege('service_role', 'public.ops_sweep_state', 'select')   as sel,
--          has_table_privilege('service_role', 'public.ops_sweep_state', 'insert')   as ins,
--          has_table_privilege('service_role', 'public.ops_sweep_state', 'update')   as upd,
--          has_table_privilege('service_role', 'public.ops_sweep_state', 'delete')   as del,
--          has_table_privilege('service_role', 'public.ops_sweep_state', 'truncate') as trunc,
--          has_function_privilege('service_role', 'public.sweep_reservations()',  'execute') as can_sweep,
--          has_function_privilege('service_role', 'public.expire_reservations()', 'execute') as can_expire;
--
-- Expected: sel true, ins/upd/del/trunc false, can_sweep **false**, can_expire **true**.
-- The raw ACLs should read `service_role=r/postgres` on the table and `postgres=X/postgres`
-- alone on `sweep_reservations`.
--
-- Re-running 010 afterwards does not undo this, which matters because 010 advertises itself
-- as safe to re-run. `create table if not exists` is a no-op on an existing table, so no
-- default privileges are applied a second time, and `create or replace function` preserves
-- the existing ACL. Measured on Postgres 15:
--
--   after revoke       postgres=X/postgres        service_role execute: false
--   after replace      postgres=X/postgres        service_role execute: false
--   after drop+create  (default)                  service_role execute: TRUE
--
-- Both halves asserted against a real Postgres in tests/sql/test_reservation_sweep_sql.py.
--
-- The third line is the residual risk and is not fixed here, because it cannot be: dropping
-- and recreating `sweep_reservations()` resets its ACL to Supabase's defaults and hands the
-- EXECUTE back. Nothing in this repository drops it, and `create or replace` is what every
-- migration here uses, but a future migration that does `drop function` would silently undo
-- this. The lasting fix is narrowing the default privileges on `public`, which changes the
-- posture of every future object in the schema and so belongs in its own decision.
--
-- TO ROLL BACK
--
-- There is nothing worth rolling back to. The previous state was select plus insert, update,
-- delete and truncate on the record, and EXECUTE on the sweep, all of it by accident. If some
-- future caller genuinely needs one of them, grant that one by name.
