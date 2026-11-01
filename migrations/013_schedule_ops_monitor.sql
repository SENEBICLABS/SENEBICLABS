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
--    procedure is in docs/operations.md, "Switching it on", step 2. Run it from bash as a
--    script, not pasted into a shell: it relies on `read` and `printf` being bash builtins
--    so the key never reaches a process's arguments, and on a trap to shred the key file
--    when the call fails or is interrupted. In outline:
--
--      read -rsp 'operator key: ' K    # a bash builtin: no process, no argv, no echo
--      ...write it to a tmpfs file, have psql read the file with \set, shred the file...
--
--    One thing that cannot be avoided and is therefore written down: `vault.create_secret`
--    takes the key as a SQL literal, so the statement text contains it. psql does not bind
--    parameters.
--
--    **The failing call is the dangerous one, not the successful one.** Production has
--    `log_statement = ddl` and `log_min_duration_statement = -1`, so a successful call is
--    logged by neither, but it also has `log_min_error_statement = error`, which logs the
--    full text of any statement that raises. So a `vault.create_secret` that fails, for
--    instance because the name is already taken, writes the key into the Postgres log.
--    Checking only the first two settings is not enough, and a key whose creation failed
--    must be revoked and replaced rather than retried. docs/operations.md has all three
--    settings and the revoke-and-remint procedure.
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
--   * **that the Data API is still not exposing `net` or `vault`.** The check below answers
--     this once, when 013 is applied, and nothing re-checks it afterwards.
--
-- The question that used to be here, whether `service_role` can read
-- `vault.decrypted_secrets`, has been answered: it can, it is a direct Supabase grant that
-- `postgres` cannot revoke, and the comment on the check below records what that does and
-- does not mean. There is nothing left to test about it.
--
-- Both are in the activation smoke test in docs/operations.md.

begin;

-- Stop clearly rather than apply half of this. Four separate reasons to refuse, each with
-- its own remedy, because "it did not work" is not a useful thing to read at the end of a
-- release.
do $$
declare
  exposed   text;
  offending text[];
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

  -- What PostgREST is told to expose. Two schemas here hold this monitor's key, and
  -- exposing either one through the Data API turns a bounded exposure into a public one:
  --
  --   `net`    `net.http_request_queue` holds the key in a request header until pg_net's
  --            worker sends it, and Supabase's PUBLIC grants on that schema cannot be
  --            revoked by `postgres`.
  --   `vault`  worse, and the reason this check covers it. Checked read-only against the
  --            production project on 2026-11-01:
  --
  --              has_table_privilege('service_role','vault.decrypted_secrets','select') = true
  --              has_table_privilege('anon',        'vault.decrypted_secrets','select') = false
  --
  --            Supabase grants `service_role` SELECT *and DELETE* on that view when it
  --            creates the extension, as a direct grant from `supabase_admin`, not through
  --            role membership. `postgres` did not make that grant and so cannot revoke it:
  --            the statement raises nothing and removes nothing. It is harmless only
  --            because `service_role` is NOLOGIN and the Data API does not expose `vault`,
  --            so the service key cannot reach it. Expose `vault` and the service key reads
  --            and deletes every secret in it, this monitor's key included.
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
  -- positive the old regex had, an unrelated setting whose value happened to be `net`, and
  -- keeps a lookalike schema such as `network_logs` or `vaults` from reading as a hit.
  select string_agg(split_part(e, '=', 2), ',')
    into exposed
    from pg_db_role_setting s
    join pg_roles r on r.oid = s.setrole
   cross join lateral unnest(s.setconfig) as e
   where r.rolname = 'authenticator'
     and e like 'pgrst.db_schemas=%';

  -- Both schemas are checked in one pass and both are named in the refusal, because
  -- exposing both is one dashboard change and a refusal that mentioned only the first
  -- would be fixed once and then refuse again.
  select array_agg(distinct btrim(sch) order by btrim(sch))
    into offending
    from unnest(string_to_array(exposed, ',')) sch
   where btrim(sch) in ('net', 'vault');

  if exposed is null then
    -- Not established, so not passed. PostgREST can also be configured from its own
    -- environment rather than from a role setting, in which case there is nothing here to
    -- read and this is the honest answer.
    raise notice E'UNKNOWN, NOT PASSED: Could not establish the effective exposed schemas '
                 'from the database (no pgrst.db_schemas setting on the authenticator role '
                 'in any scope). This is the state of the production project as of '
                 '2026-11-01, where PostgREST is configured from the platform rather than '
                 'from a role setting.\n'
                 '  * This check has therefore verified NOTHING, and the schedule below is '
                 'being installed WITHOUT it. Do not read this installation succeeding as '
                 'evidence that "net" and "vault" are not exposed.\n'
                 '  * Confirm by hand, now, in Supabase Dashboard -> Settings -> API -> '
                 'Exposed schemas, that neither "net" nor "vault" is listed. If either is, '
                 'roll this back (see the footer of this file) until it is removed.\n'
                 '  * Record who confirmed it and when, in the activation checklist in '
                 'docs/operations.md. Nothing re-checks this afterwards.';
  elsif offending is not null then
    raise exception 'a schema holding this monitor''s key is exposed through the Data API: %',
                    array_to_string(offending, ', ')
      using errcode = 'insufficient_privilege',
            hint = 'Remove it from Settings -> API -> Exposed schemas first. While "net" is '
                   'exposed, anon and authenticated can read net.http_request_queue, which '
                   'holds this monitor''s key in a request header until the worker sends it. '
                   'While "vault" is exposed it is worse: service_role holds a direct SELECT '
                   'and DELETE grant on vault.decrypted_secrets that postgres cannot revoke, '
                   'so the service key reads and deletes every secret in the Vault. '
                   'Nothing in 013 has been applied.';
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
--   select name, enabled, runs, last_run_at, last_success_at, last_status_code,
--          last_reported_status, last_failing_checks, last_error, consecutive_failures,
--          pending_request_id
--     from ops_monitor_state;
--   select status, start_time from cron.job_run_details
--     where jobid = (select jobid from cron.job where jobname = 'ops-monitor')
--     order by start_time desc limit 5;
--
-- `ops_monitor_state` IS THE RECORD. `ops_monitor_tick()` returns a description of what it
-- did, but pg_cron stores the row count of a `SELECT`, so `cron.job_run_details.
-- return_message` reads `1 row` on every tick whatever happened, and the return value is
-- discarded. Measured on production on 2026-11-01: both the first tick and the second
-- recorded `1 row`, and `sweep-reservations` from 011 does the same. What that table is
-- still good for is `status`, which says whether the function raised at all.
--
-- The first two ticks look alarming and are not. pg_net cannot return a reply inside the
-- call that sent the request, so each tick collects the previous tick's:
--
--   after tick 1  runs=1, pending_request_id set, last_success_at still null: there was
--                 nothing to collect yet
--   after tick 2  last_success_at set, last_status_code=503, last_reported_status=alert,
--                 last_failing_checks={ops_monitor_stalled} -- the first probe caught this
--                 monitor before it had ever succeeded, so ops-health was correctly
--                 alerting about it
--   after tick 3  last_status_code=200, last_reported_status=ok, last_failing_checks empty:
--                 writing last_success_at cleared ops_monitor_stalled
--
-- That bootstrap 503 is the monitor reporting on itself, and it is the reason a 503 with a
-- valid `checks` array counts as a successful probe rather than a failure. The field that
-- says a probe succeeded is `consecutive_failures` staying at 0, not the status code.
-- `GET /project/admin/ops-health` reports the same thing as `ops_monitor_stalled` and
-- `ops_monitor_probe`.
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
-- row from `vault.secrets`, and revoke the operator key so it stops working everywhere.
--
-- Revoking is a POST carrying the id in a body, not a DELETE on a path; there is no DELETE
-- route for operators. Both calls need the root key, so the monitor's own key cannot revoke
-- anything, and the id for the second comes from the first:
--
--   curl -s $API/project/admin/operators -H "X-Admin-Key: $ADMIN_API_KEY"
--   curl -s -X POST $API/project/admin/operators/revoke \
--     -H "X-Admin-Key: $ADMIN_API_KEY" -H 'Content-Type: application/json' \
--     -d '{"operator_id":"<id from the list>"}'
--
-- It marks the row inactive, so the key stops working on its next use rather than at once.
-- docs/operations.md, "Revoking the monitor's key", is the fuller version.
