-- Migration 013: the monitor runs on a schedule inside Postgres, and is switched on
-- Paste into the Supabase SQL editor (Dashboard -> SQL Editor -> New query -> Run).
-- Safe to re-run. Apply after 012.
--
-- TWO OWNER ACTIONS FIRST
--
-- 1. Enable `pg_net`, once, as the project owner: Supabase dashboard -> Database ->
--    Extensions, search for pg_net. Checked against the production project on 2026-11-01:
--    pg_net 0.20.0 is available and not yet installed, and `supabase_vault` 0.3.1 and
--    pg_cron 1.6.4 are already installed.
--
-- 2. Put a **dedicated operator key** in Vault under the name 012 seeded, which is
--    `ops_monitor_admin_key`. Mint the key first so it can be revoked on its own without
--    touching the root key or anyone else's:
--
--      curl -s -X POST https://api.senebiclabs.com/api/v1/project/admin/operators \
--        -H "X-Admin-Key: $ADMIN_API_KEY" -H 'Content-Type: application/json' \
--        -d '{"name":"in-database monitor"}'
--
--    then, in the SQL editor, with the key pasted in place of the placeholder:
--
--      select vault.create_secret('op_...', 'ops_monitor_admin_key',
--                                 'Operator key used by ops_monitor_tick()');
--
--    Do that in a query you do not save. The SQL editor keeps a history of what was run,
--    and a key pasted into a saved snippet is a key stored in the dashboard.
--
-- This file does not do either of them, and that is deliberate for the same reason 011 does
-- not enable pg_cron: the `postgres` role on this project is not a superuser, so whether it
-- may create an extension is not something a migration should assume, and a migration that
-- gets half way through enabling one leaves a worse state behind than one that stops and
-- says what to do. Creating the secret is not a migration's business at all, because the
-- value would then live in a file in this repository, which is the one place it must not be.
--
-- WHY A SCHEDULE HERE RATHER THAN A SERVICE
--
-- The same reasoning as 011, and one addition. A Render cron job would do this too, but
-- Render's cron jobs are paid, from one dollar a month, and it would need the operator key
-- in its environment. GitHub Actions is free and already has a workflow, and that workflow
-- is exactly what proved unreliable: written as every ten minutes, measured firing between
-- 2.8 and 7.1 hours apart, and silently skipping every run for want of three secrets. A
-- schedule inside Postgres cannot be throttled by anyone else and cannot skip itself.
--
-- WHAT THE KEY IS EXPOSED TO, STATED PLAINLY
--
-- `pg_net` holds the request, including its headers, in `net.http_request_queue` until the
-- background worker sends it, which is seconds. Supabase grants privileges on the `net`
-- schema to PUBLIC as `supabase_admin`, and those grants cannot be revoked by `postgres`
-- (Supabase documents this: a role may only revoke what it granted). So for those seconds,
-- any role that can open a direct database connection could read the header.
--
-- What that does and does not mean here:
--
--   * `anon`, `authenticated` and `service_role` cannot open a direct connection, and the
--     Data API does not expose the `net` schema, so no API key reaches it. The check below
--     refuses to install the schedule if `net` has been added to the exposed schemas,
--     because that would turn a bounded exposure into a public one;
--   * the roles that *can* connect are `postgres` and anything an operator creates. A role
--     holding the connection string already reads every table in the database, including
--     `operators`, so it is not learning anything this key would not have given it;
--   * the key is a dedicated operator key with no powers beyond the operator endpoints, and
--     revoking it is one row in `operators`.
--
-- The alternative is not having a monitor. This is the trade, made knowingly.

begin;

-- Stop clearly rather than apply half of this. Four separate reasons to refuse, each with
-- its own remedy, because "it did not work" is not a useful thing to read at the end of a
-- release.
do $$
declare exposed text;
begin
  if not exists (select 1 from pg_extension where extname = 'pg_cron') then
    raise exception 'pg_cron is not enabled on this database'
      using errcode = 'feature_not_supported',
            hint = 'Enable it once as the project owner (Supabase dashboard: Integrations '
                   '-> Cron, or Database -> Extensions), then run this migration again. '
                   'Nothing in 013 has been applied.';
  end if;

  if not exists (select 1 from pg_extension where extname = 'pg_net') then
    raise exception 'pg_net is not enabled on this database'
      using errcode = 'feature_not_supported',
            hint = 'Enable it once as the project owner (Supabase dashboard: Database -> '
                   'Extensions, search for pg_net), then run this migration again. '
                   'Scheduling a monitor that cannot send a request would give a job that '
                   'records a failure on every tick. Nothing in 013 has been applied.';
  end if;

  if to_regprocedure('public.ops_monitor_tick()') is null then
    raise exception 'ops_monitor_tick() is missing from this database'
      using errcode = 'undefined_function',
            hint = 'Apply migration 012 first. Nothing in 013 has been applied.';
  end if;

  -- The secret has to exist before the schedule, or the first tick records a failure and
  -- `ops-health` reports an alert about an installation that is simply unfinished.
  if to_regclass('vault.decrypted_secrets') is null
     or not exists (select 1 from vault.decrypted_secrets
                     where name = (select secret_name from ops_monitor_state
                                    where name = 'ops-health')
                       and coalesce(decrypted_secret, '') <> '') then
    raise exception 'the operator key is not in Vault yet'
      using errcode = 'invalid_parameter_value',
            hint = 'Mint a dedicated operator key (POST /project/admin/operators) and store '
                   'it with select vault.create_secret(''op_...'', ''ops_monitor_admin_key'', '
                   '''Operator key used by ops_monitor_tick()''), in a query you do not '
                   'save. Then run this migration again. Nothing in 013 has been applied.';
  end if;

  -- A best-effort read of what PostgREST is told to expose. If `net` is in that list, the
  -- request queue is readable with an API key and the header above stops being a bounded
  -- exposure. The setting is not always readable from here, and when it is not this says so
  -- and carries on rather than blocking on something it cannot see.
  select setconfig::text into exposed
    from pg_db_role_setting s
    join pg_roles r on r.oid = s.setrole
   where r.rolname = 'authenticator'
   limit 1;

  if exposed is null then
    raise notice 'Could not read PostgREST''s exposed schemas from here. Confirm by hand '
                 'that "net" is not listed under Settings -> API -> Exposed schemas.';
  elsif exposed ~ '(^|[=,{"[:space:]])net([,"}[:space:]]|$)' then
    raise exception 'the net schema is exposed through the Data API'
      using errcode = 'insufficient_privilege',
            hint = 'Remove "net" from Settings -> API -> Exposed schemas first. While it is '
                   'exposed, anon and authenticated can read net.http_request_queue, which '
                   'holds this monitor''s key in a request header until the worker sends '
                   'it. Nothing in 013 has been applied.';
  end if;
end $$;

-- The schedule.
--
-- Idempotent by construction: the job is removed by name before it is created, so running
-- this file twice cannot leave two monitors probing the same endpoint. pg_cron's own
-- `schedule` may replace a job of the same name, but whether it does has varied, so this
-- does not rely on it.
--
-- The job runs as the role that schedules it, which is the role applying this migration and
-- the owner of these objects. That matters twice over: `ops_monitor_tick()` is granted to
-- nobody, and `vault.decrypted_secrets` is readable only by the roles Supabase grants it
-- to, which does not include the API's service key.
do $$
begin
  if exists (select 1 from cron.job where jobname = 'ops-monitor') then
    perform cron.unschedule('ops-monitor');
  end if;
  -- Every ten minutes, on the same cadence the GitHub workflow claims. `ops-health` alerts
  -- at 35 minutes, which is three missed ticks plus most of a fourth: late enough that one
  -- slow reply is not an alarm, and early enough to be noticed within a working hour.
  --
  -- Deliberately not the same minutes as `sweep-reservations` from 011, which runs every
  -- five. They would coincide on every other tick, and a probe that reads the sweep's own
  -- record is better off not reading it in the moment the sweep is writing it.
  perform cron.schedule('ops-monitor', '3,13,23,33,43,53 * * * *',
                        'select public.ops_monitor_tick()');
end $$;

-- Switch it on. Last, so that a file which refused above leaves the monitor off rather than
-- enabled with nothing scheduled.
update ops_monitor_state set enabled = true where name = 'ops-health';

commit;

-- To verify, after the first two ticks have passed (up to twenty minutes):
--
--   select jobname, schedule, active from cron.job where jobname = 'ops-monitor';
--   select status, return_message, start_time from cron.job_run_details
--     where jobid = (select jobid from cron.job where jobname = 'ops-monitor')
--     order by start_time desc limit 5;
--   select name, enabled, last_run_at, last_success_at, last_status_code,
--          last_reported_status, last_failing_checks, last_error, consecutive_failures, runs
--     from ops_monitor_state;
--
-- The first tick returns `nothing pending; sent <id>`: it had no previous reply to collect.
-- The second returns `answered 200: ok; sent <id>`, or `answered 503: alert` with the
-- failing check names in `last_failing_checks`, which is the monitor working rather than
-- failing. `GET /project/admin/ops-health` reports the same thing as `ops_monitor_stalled`
-- and `ops_monitor_probe`.
--
-- TO ROLL BACK
--
--   select cron.unschedule('ops-monitor');
--   update ops_monitor_state set enabled = false where name = 'ops-health';
--
-- That is the whole rollback and it needs no deploy. The schedule stops, the row stops
-- being written, and `ops_monitor_stalled` returns to the warning it reported before 013,
-- which does not make the endpoint answer 503. Nothing else in the platform reads this
-- record, so nothing else changes. To go further: `drop function ops_monitor_tick()` and
-- `drop table ops_monitor_state` undo 012, after which `ops-health` reports the check as a
-- warning saying the table is missing, exactly as it does before 012 is applied. The Vault
-- secret and the operator key are separate: `select vault.update_secret(...)` or delete the
-- row from `vault.secrets`, and revoke the operator key through
-- `DELETE /project/admin/operators/{id}` so it stops working everywhere.
