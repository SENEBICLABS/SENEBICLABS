-- Migration 010: the sweep keeps its own record, and only one sweeper runs at a time
-- Paste into the Supabase SQL editor (Dashboard -> SQL Editor -> New query -> Run).
-- Safe to re-run. Apply after 009.
--
-- WHY
--
-- A lapsed lease stops consuming capacity by predicate (`expires_at > now()`), so serving
-- is correct whether or not anything ever sweeps. The sweep exists so that
-- `reservations_lapsed_unswept` means a fault rather than a cadence, and that only holds
-- if the sweep runs on a known interval.
--
-- It rode on the scoring drain, which runs from a GitHub Actions schedule. That schedule
-- is written as every ten minutes and was measured firing between 2.8 and 7.1 hours apart,
-- against a warning threshold of fifteen minutes. So for as long as there was work to
-- sweep, the check would have been reporting the scheduler rather than the platform.
--
-- This migration adds the two things the sweep needs in order to run on its own: a record
-- of when it last finished, and a guard so two sweepers cannot overlap. Migration 011 puts
-- it on a schedule inside Postgres, which needs no credential and cannot be throttled by
-- anyone else. 010 is useful on its own and runs on any Postgres; 011 needs pg_cron.
--
-- The scoring drain keeps calling `expire_reservations()` directly, which is the fallback
-- if the schedule ever stops. It must NOT be changed to call `sweep_reservations()`: that
-- would refresh the record below, and a drain that happened to run would then hide a dead
-- schedule from the very check that is there to notice one.

begin;

-- When the scheduled sweep last finished, and what it did. One row per sweeper.
--
-- Nothing clinical is here and nothing identifying: a name, a timestamp, and a count of
-- rows retired. It exists so an operator can answer "is the tidy-up alive" without being
-- given the database.
create table if not exists ops_sweep_state (
  name          text        primary key,
  last_run_at   timestamptz not null,
  last_expired  integer     not null,
  runs          bigint      not null default 0
);

-- The platform baseline, as on every other table here: row level security on with no
-- policies, so a key that is not the service key reads nothing even where a grant was
-- inherited from somewhere else.
alter table ops_sweep_state enable row level security;

-- The scheduled entry point. `expire_reservations()` stays exactly as 009 wrote it; this
-- wraps it with the two properties a scheduler needs.
create or replace function sweep_reservations() returns integer
language plpgsql
as $$
declare n integer;
begin
  -- One sweeper at a time, whatever ends up scheduling it. A transaction-scoped lock is
  -- released when this transaction ends, including when it fails, so a sweep that dies
  -- half way cannot wedge every later one the way a session-scoped lock could.
  --
  -- Returning null rather than a count is the difference between "nothing to do" and
  -- "did not run", and it leaves the record untouched: a run that skipped is not evidence
  -- that the sweep is alive, so it must not refresh the timestamp that says so.
  if not pg_try_advisory_xact_lock(hashtext('sweep_reservations')::bigint) then
    return null;
  end if;

  -- If this raises, everything below is rolled back with it and the record keeps the
  -- timestamp of the last sweep that actually finished. A failure never reads as a
  -- success. pg_cron runs each job in its own transaction, so that rollback is the whole
  -- job and the next tick starts clean.
  n := expire_reservations();

  insert into ops_sweep_state as s (name, last_run_at, last_expired, runs)
       values ('reservations', now(), n, 1)
  on conflict (name) do update
     set last_run_at  = now(),
         last_expired = excluded.last_expired,
         runs         = s.runs + 1;
  return n;
end;
$$;

-- Service key only, and only what each caller actually needs.
--
-- As in 009, the revoke is what makes the grant necessary rather than optional: it removes
-- the default EXECUTE that every new function carries, and `service_role` holds nothing of
-- its own unless it is said here. The difference from 009 is that `sweep_reservations()`
-- gets no grant at all. It runs as the role that schedules it in 011, which owns these
-- objects, so no API caller needs to reach it and none should: the only way to refresh the
-- record below is to be the scheduler.
revoke all on ops_sweep_state from public, anon, authenticated;
revoke all on function sweep_reservations() from public, anon, authenticated;

-- The monitor reads the record. It never writes it and never calls the sweep.
grant select on ops_sweep_state to service_role;

commit;
