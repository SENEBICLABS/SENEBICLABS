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
--    touching the root key or anyone else's.
--
--    **Not through the SQL editor.** The dashboard keeps a history of what was run whether
--    or not the query is saved, so a key pasted there is a key stored in the dashboard for
--    as long as that history lives. Do it from `psql` instead, where the value can be kept
--    out of the shell history, out of every process's arguments and off the disk. The
--    procedure is in docs/operations.md, "Switching it on", step 2, and in outline:
--
--      set +o history                  # this shell stops recording
--      read -rsp 'operator key: ' K    # a builtin: no process, no argv, no echo
--      ...write it to a tmpfs file, have psql read the file with \set, shred the file...
--
--    One thing that cannot be avoided and is therefore written down: `vault.create_secret`
--    takes the key as a SQL literal, so the statement text contains it. psql does not bind
--    parameters. If the project is logging statements (`log_statement = 'all'`, or a
--    `log_min_duration_statement` short enough to catch it) the key lands in the Postgres
--    log. Confirm that setting before running it, which docs/operations.md says how to do.
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
--     because that would turn a bounded exposure into a public one. **That check runs once,
--     here, at install time.** Nothing re-checks it afterwards: exposing `net` later is a
--     dashboard setting that no migration and no code in this repository will notice, and
--     there is no scheduled check for it. It is on the list in docs/operations.md of what
--     to confirm by hand, next to the provider dashboards;
--   * the roles that *can* connect are `postgres` and anything an operator creates. A role
--     holding the connection string already reads every table in the database, including
--     `operators`, so it is not learning anything this key would not have given it;
--   * the key is a dedicated operator key with no powers beyond the operator endpoints, and
--     revoking it is one row in `operators`.
--
-- The alternative is not having a monitor. This is the trade, made knowingly.
--
-- AND THE REPLY, WHICH IS NOT A CREDENTIAL BUT IS NOT NOTHING
--
-- `net._http_response` holds the whole body of each reply, and an `ops-health` body carries
-- operational identifiers: project ids, task numbers and event ids. No clinical content, no
-- clinician names, no client URLs or secrets. The tick deletes the row as soon as it has
-- read it, so the window is one tick, and the disabled path in 012 releases a reply it will
-- never collect rather than leaving it to pg_net's own six hour prune. It sits in the same
-- `net` schema with the same unrevokable PUBLIC grants as the queue above, so the same
-- bounded set of roles can read it for that window.
--
-- WHAT STILL HAS TO BE CHECKED AGAINST SUPABASE
--
-- Two things in this design cannot be tested anywhere but the real project, because
-- `pg_net` and `supabase_vault` are not installable on a throwaway Postgres and the SQL
-- tests stub their interfaces:
--
--   * **that pg_net's background worker is actually running.** The check below confirms the
--     extension is installed, which is not the same thing. A worker that is not running
--     leaves requests in the queue, never writes a reply, and the monitor then records a
--     lost reply every twenty minutes;
--   * **that `vault.decrypted_secrets` is not readable by `service_role` or by `anon`.**
--     That is asserted nowhere in this repository and Supabase's documentation does not
--     state it either way. If it is readable by the service key, the API's own key can read
--     this monitor's operator key.
--
-- Both are in the activation smoke test in docs/operations.md, and the second one is a
-- single read-only query that should be run before this monitor is trusted.

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

  -- What PostgREST is told to expose. If `net` is in that list, the request queue is
  -- readable with an API key and the header above stops being a bounded exposure.
  --
  -- Read from every row rather than from one of them, because `limit 1` without an
  -- `order by` is not a choice, it is a coin toss. A role routinely has two rows in
  -- `pg_db_role_setting`: one cluster-wide (`setdatabase = 0`) and one per database, which
  -- is where Supabase puts `pgrst.db_schemas`. The first version of this guard read one
  -- arbitrary row, and against a database holding
  --
  --     setdatabase | setconfig
  --     0           | {statement_timeout=8s}
  --     5           | {"pgrst.db_schemas=public,net"}
  --
  -- it read the first, found no `net`, and installed, while `net` was exposed the whole
  -- time. Worse, finding *a* row made `exposed` non-null, so it also skipped the notice
  -- below that would have asked for the check to be done by hand. It failed open and quiet
  -- at the same moment, which is the one combination a guard must never manage.
  --
  -- So: pull the value of `pgrst.db_schemas` itself out of every row, and keep whether it
  -- was found at all separate from what it said. Comparing exact comma-separated tokens
  -- rather than matching a pattern over the whole setting string also drops the one false
  -- positive the old regex had, an unrelated setting whose value happened to be `net`.
  select string_agg(split_part(e, '=', 2), ',')
    into exposed
    from pg_db_role_setting s
    join pg_roles r on r.oid = s.setrole
   cross join lateral unnest(s.setconfig) as e
   where r.rolname = 'authenticator'
     and e like 'pgrst.db_schemas=%';

  if exposed is null then
    -- Not established, so not passed. PostgREST can also be configured from its own
    -- environment rather than from a role setting, in which case there is nothing here to
    -- read and this is the honest answer.
    raise notice 'Could not establish PostgREST''s exposed schemas from the database '
                 '(no pgrst.db_schemas setting on the authenticator role). Confirm by hand '
                 'that "net" is not listed under Settings -> API -> Exposed schemas before '
                 'relying on this monitor.';
  elsif exists (select 1 from unnest(string_to_array(exposed, ',')) sch
                 where btrim(sch) = 'net') then
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
-- Both statements, then the check that both took. Doing only the second is the quiet
-- failure: the job stays scheduled and ticks `skipped: disabled` for ever, while
-- `ops_monitor_stalled` drops from an alert to a warning, so the endpoint stops saying 503
-- about a monitor that is still installed and no longer watching anything.
--
--   select cron.unschedule('ops-monitor');
--   update ops_monitor_state set enabled = false where name = 'ops-health';
--
--   -- expect: 0 | f
--   select (select count(*) from cron.job where jobname = 'ops-monitor') as jobs_left,
--          (select enabled from ops_monitor_state where name = 'ops-health') as still_on;
--
-- `cron.unschedule` raises if the job does not exist, so a rollback run twice errors on the
-- first statement rather than quietly leaving the second undone; run them separately, or
-- read the check above to see which half is outstanding.
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
