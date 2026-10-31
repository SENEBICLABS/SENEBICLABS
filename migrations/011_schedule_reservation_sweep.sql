-- Migration 011: the sweep runs on a schedule inside Postgres
-- Paste into the Supabase SQL editor (Dashboard -> SQL Editor -> New query -> Run).
-- Safe to re-run. Apply after 010.
--
-- OWNER ACTION FIRST
--
-- pg_cron has to be enabled once, by the project owner, in the Supabase dashboard:
-- Integrations -> Cron, or Database -> Extensions and search for pg_cron. This file does
-- not enable it, and that is deliberate. The `postgres` role on this project is not a
-- superuser, so whether it may create the extension is not something a migration should
-- assume, and a migration that gets half way through enabling an extension leaves a worse
-- state behind than one that stops and says what to do. Run this file again afterwards.
--
-- Checked against the production project before this was written: pg_cron 1.6.4 is
-- available and already listed in shared_preload_libraries, so it will actually run rather
-- than merely install; and `cron.database_name` is `postgres`, which is the same database
-- that holds these functions. That last one is the usual pg_cron surprise, because jobs
-- only ever run in the one configured database, and a job scheduled anywhere else is
-- accepted and then silently never runs.
--
-- WHY A SCHEDULE HERE RATHER THAN A SERVICE
--
-- `expire_reservations()` is pure SQL against the database it lives in. Every other way of
-- calling it on a timer adds a network hop and a credential to do what Postgres can do
-- internally: a Render cron job needs a connection string or an admin key in its
-- environment, and an external pinger needs a privileged key held by a third party. This
-- needs neither. There is no credential to leak because nothing leaves the database.

begin;

-- Stop clearly rather than apply half of this.
--
-- Two separate reasons to refuse, each with its own remedy, because "it did not work" is
-- not a useful thing to read at the end of a release.
do $$
begin
  if not exists (select 1 from pg_extension where extname = 'pg_cron') then
    raise exception 'pg_cron is not enabled on this database'
      using errcode = 'feature_not_supported',
            hint = 'Enable it once as the project owner (Supabase dashboard: Integrations '
                   '-> Cron, or Database -> Extensions, search for pg_cron), then run this '
                   'migration again. Nothing in 011 has been applied.';
  end if;

  if to_regprocedure('public.sweep_reservations()') is null then
    raise exception 'sweep_reservations() is missing from this database'
      using errcode = 'undefined_function',
            hint = 'Apply migration 010 first. Scheduling a call to a function that does '
                   'not exist would give a job that fails on every tick.';
  end if;
end $$;

-- The schedule, and the retention of its own history.
--
-- Idempotent by construction: each job is removed by name before it is created, so running
-- this file twice cannot leave two jobs sweeping the same table. pg_cron's own `schedule`
-- may replace a job of the same name, but whether it does has varied, so this does not
-- rely on it.
--
-- Both jobs run as the role that schedules them, which is the role applying this migration
-- and the owner of these objects. That matters twice over: `item_reservations` has row
-- level security on with no policies, so a sweeper without bypass would update nothing and
-- report success, and `sweep_reservations()` is granted to nobody.
do $$
begin
  if exists (select 1 from cron.job where jobname = 'sweep-reservations') then
    perform cron.unschedule('sweep-reservations');
  end if;
  -- Every five minutes. The monitor warns at twenty, which is four missed ticks, so a
  -- single slow or skipped run is not an alarm.
  perform cron.schedule('sweep-reservations', '*/5 * * * *',
                        'select public.sweep_reservations()');

  if exists (select 1 from cron.job where jobname = 'purge-cron-history') then
    perform cron.unschedule('purge-cron-history');
  end if;
  -- pg_cron records every run and prunes nothing, so the history grows without end. At
  -- five minute intervals that is 288 rows a day. Keep a week, which is long enough to
  -- read back what happened over a weekend, and let the rest go.
  perform cron.schedule('purge-cron-history', '17 3 * * *',
    'delete from cron.job_run_details where end_time < now() - interval ''7 days''');
end $$;

commit;

-- To verify, after the first five minutes have passed:
--
--   select jobname, schedule, active from cron.job;
--   select status, return_message, start_time
--     from cron.job_run_details order by start_time desc limit 5;
--   select * from ops_sweep_state;
--
-- `GET /project/admin/ops-health` answers the same question as `reservations_sweep_stalled`
-- without a database connection.
