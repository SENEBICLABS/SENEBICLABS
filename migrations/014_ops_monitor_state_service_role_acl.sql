-- Migration 014: the monitor's record is read-only to the service key, as 012 intended
-- Paste into the Supabase SQL editor (Dashboard -> SQL Editor -> New query -> Run).
-- Safe to re-run. Apply after 012. Independent of 013: apply it before or after.
--
-- WHY
--
-- 012 says "Service key only, and only what it actually needs", and casts `ops_monitor_state`
-- as something `ops-health` reads. It is not read-only. Measured on production right after
-- 012 was applied:
--
--   select=True insert=False update=False delete=False TRUNCATE=True refs=True trigger=True
--
-- 012 revokes from `public, anon, authenticated` and then grants `select` to `service_role`,
-- but never revokes from `service_role`, so Supabase's default privileges for new tables in
-- `public` survive the grant:
--
--   pg_default_acl, granted by postgres, objtype=r:
--     anon=Dxtm/postgres, authenticated=Dxtm/postgres, service_role=Dxtm/postgres
--
-- `D` is TRUNCATE. The revoke of `anon` and `authenticated` cleared theirs; the service key
-- kept its own, and a `grant select` adds to a privilege set rather than replacing it.
--
-- WHAT THAT WOULD COST, WHICH IS WHY IT IS WORTH A MIGRATION
--
-- Truncating this table does not corrupt anything, and that is the problem. It removes the
-- configuration row, so `ops_monitor_tick()` returns `skipped: not configured` and
-- `ops-health` reports `ops_monitor_stalled` as a **warning** saying the table holds no
-- ops-health row. Destroying the monitor's record therefore makes monitoring quieter rather
-- than louder, which is the one failure shape every check in this platform is written to
-- avoid.
--
-- It is bounded today, and the bound is not the grant: `service_role` is NOLOGIN, reaches
-- only PostgREST, and PostgREST has no TRUNCATE verb. This closes it at the grant anyway,
-- because the next thing that gets a direct connection as that role should not inherit it.
--
-- WHAT THIS DELIBERATELY DOES NOT DO
--
-- `ops_sweep_state` from migration 010 has the same omission and worse: there the service
-- key holds insert, update and delete as well. That is live, predates this work, and is not
-- in scope here; it wants its own change so that this one stays reviewable in a sitting.
--
-- It also does not touch the default privileges on `public`. Narrowing those would change
-- the posture of every future table in the schema, which is a decision about the whole
-- platform rather than about this monitor.

begin;

-- Stop clearly rather than leave a half-applied privilege change.
do $$
begin
  if to_regclass('public.ops_monitor_state') is null then
    raise exception 'ops_monitor_state is missing from this database'
      using errcode = 'undefined_table',
            hint = 'Apply migration 012 first. Nothing in 014 has been applied.';
  end if;
end $$;

-- Revoke first, then grant exactly the one privilege that is wanted.
--
-- The order is the whole point. `grant select` on its own is what 012 did, and a grant adds
-- to whatever the role already holds; only a revoke takes anything away. Doing it in this
-- order is also what makes the file safe to re-run: it converges on select-only from any
-- starting state, including from a state somebody has since widened by hand.
revoke all on public.ops_monitor_state from service_role;

-- `ops-health` reads this row under the service key and reports `ops_monitor_stalled` and
-- `ops_monitor_probe` from it (app/services/ops_health.py). Reading is all it does: the row
-- is written only by `ops_monitor_tick()`, which runs as the owner of these objects on the
-- schedule 013 installs, and tests/test_ops_health.py asserts that no route in the API
-- writes it. A heartbeat the API could refresh would be a heartbeat that proves nothing.
grant select on public.ops_monitor_state to service_role;

commit;

-- Re-running 012 after this does not undo it, which is worth knowing because 012 advertises
-- itself as safe to re-run. `create table if not exists` is a no-op on an existing table, so
-- no default privileges are applied a second time, and 012's own `grant select` adds nothing
-- that is not already there. Asserted against a real Postgres in
-- tests/sql/test_ops_monitor_sql.py.
--
-- To verify, every privilege that matters, in one row:
--
--   select has_table_privilege('service_role', 'public.ops_monitor_state', 'select')     as sel,
--          has_table_privilege('service_role', 'public.ops_monitor_state', 'insert')     as ins,
--          has_table_privilege('service_role', 'public.ops_monitor_state', 'update')     as upd,
--          has_table_privilege('service_role', 'public.ops_monitor_state', 'delete')     as del,
--          has_table_privilege('service_role', 'public.ops_monitor_state', 'truncate')   as trunc,
--          has_table_privilege('service_role', 'public.ops_monitor_state', 'references') as refs,
--          has_table_privilege('service_role', 'public.ops_monitor_state', 'trigger')    as trig,
--          (select array_to_string(relacl::text[], ', ') from pg_class c
--             join pg_namespace n on n.oid = c.relnamespace
--            where n.nspname = 'public' and c.relname = 'ops_monitor_state') as raw_acl;
--
-- Expected: sel true, everything else false, and the raw ACL reading
-- `postgres=arwdDxtm/postgres, service_role=r/postgres`.
--
-- TO ROLL BACK
--
-- There is nothing to roll back to that is worth having: the previous state was select plus
-- TRUNCATE, REFERENCES and TRIGGER by accident. If some future caller genuinely needs one of
-- them, grant that one by name rather than restoring the set.
