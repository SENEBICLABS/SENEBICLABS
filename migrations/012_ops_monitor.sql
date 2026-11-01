-- Migration 012: the platform keeps its own record of whether anything is watching it
-- Paste into the Supabase SQL editor (Dashboard -> SQL Editor -> New query -> Run).
-- Safe to re-run. Apply after 011. Needs nothing but Postgres; 013 puts it on a schedule.
--
-- WHY
--
-- Everything that notices a fault on this platform is in `ops-health`, and the only thing
-- that calls `ops-health` is a GitHub Actions workflow. On 2026-11-01 that workflow was
-- found to have been printing `ops secrets not set; skipping` on every run since it was
-- written: `OPS_API_BASE` and `OPS_ADMIN_KEY` were never set, so nothing had ever read the
-- endpoint. Every alarm in `ops_health.py` was being raised to nobody, and there was no way
-- to tell that from inside the system, because nothing recorded whether the check had run.
--
-- That is the gap this closes. Not a second alerting path: a durable record, written from
-- inside the database on a schedule that needs no third party, saying when the health
-- endpoint last answered and what it said. A stalled monitor then becomes a fact in a table
-- rather than an absence nobody can see.
--
-- WHAT IT IS NOT
--
-- It does not send anybody an email. Postgres has nowhere to send one. The record exists so
-- that something which *can* raise an alarm has something truthful to read:
--
--   * `ops-health` reports `ops_monitor_stalled` from this row, so any uptime monitor
--     pointed at that endpoint with the key learns that the in-database monitor stopped,
--     without parsing the body (the endpoint answers 503);
--   * `select * from ops_monitor_state` answers the same question with one query and no
--     credential beyond a database connection;
--   * the GitHub workflow, once its three secrets exist, fails its run and emails the owner.
--
-- The two halves cover each other's failure, and that is the whole design: if the API is
-- down, this record says so from inside the database; if this schedule dies, `ops-health`
-- says so to whoever reads it from outside. Neither one alone is sufficient, and neither
-- one is being asked to be.
--
-- It also does not run the scoring drain. `POST /internal/process-answers` scores answers
-- and re-sends client deliveries, and `pg_net` is fire and forget: the status code arrives
-- in a table minutes later and the body is never read. Driving client-visible writes that
-- way would be worse than the workflow, which reads the reply and fails loudly. The drain
-- stays on the workflow, and the workflow still needs its secrets.
--
-- HOW IT WORKS, AND THE ONE AWKWARD PART
--
-- `pg_net` is asynchronous by design. `net.http_get` queues a request and returns an id
-- immediately; a background worker sends it after this transaction commits and writes the
-- reply into `net._http_response`. So no single call can both send a request and see its
-- answer, and a function that tried would record a timeout on every run.
--
-- `ops_monitor_tick()` is therefore written as two halves of one call: it first collects the
-- reply to the request the *previous* tick issued, then issues a new one. At a ten minute
-- cadence the reply has had ten minutes to arrive against a ten second timeout, so a
-- request with no reply is genuinely lost rather than merely early. The cost is that every
-- result is one tick old, which is accounted for in the staleness bound.

begin;

-- One row per thing being probed, holding both its configuration and what the last probe
-- found. Configuration and state together because there is exactly one of each and a join
-- between two single-row tables would be ceremony.
--
-- Nothing clinical is here, nothing identifying, and no credential. `secret_name` is the
-- *name* of a Vault secret, never its value: the key itself is only ever read inside the
-- function below, into a local variable, and is never stored, returned or raised.
create table if not exists ops_monitor_state (
  name                  text        primary key,

  -- configuration, set by an operator
  url                   text        not null,
  secret_name           text        not null,
  -- Off until an operator switches it on, and that default is deliberate. Applying this
  -- migration must not make `ops-health` start reporting a monitor that cannot run yet:
  -- 013 and the Vault secret are separate steps, and a release that half-applied would
  -- otherwise turn the endpoint into an alarm about its own installation.
  enabled               boolean     not null default false,

  -- the request this tick issued, waiting for the next tick to collect it
  pending_request_id    bigint,
  pending_since         timestamptz,

  -- what the last completed probe found
  last_run_at           timestamptz,       -- a probe was issued
  last_success_at       timestamptz,       -- the endpoint answered: this is the heartbeat
  last_status_code      integer,
  last_reported_status  text,              -- ops-health's own verdict: ok, warn or alert
  last_failing_checks   text[],            -- the names only, which carry no content
  last_failure_at       timestamptz,
  last_error            text,
  consecutive_failures  integer     not null default 0,
  runs                  bigint      not null default 0
);

-- The platform baseline, as on every other table here: row level security on with no
-- policies, so a key that is not the service key reads nothing even where a grant was
-- inherited from somewhere else.
alter table ops_monitor_state enable row level security;

-- The thing being probed. Seeded rather than demanded, so activation is one fewer step, and
-- `on conflict do nothing` so re-running this file never overwrites an operator's own URL.
--
-- The URL is not a secret: it is in .github/workflows/ops.yml, and `ops-health` is an
-- operator endpoint which is deliberately not in the client reference (docs/api.md or
-- ui/app/docs/). The key that opens it is the secret, and that lives in Vault.
--
-- Nothing checks that this URL is the ops-health of *this* project, and nothing can: a
-- database cannot know which deployment it belongs to. What the tick checks instead is the
-- shape of what answers, so a URL pointing somewhere else is reported rather than believed.
insert into ops_monitor_state (name, url, secret_name)
     values ('ops-health',
             'https://api.senebiclabs.com/api/v1/project/admin/ops-health',
             'ops_monitor_admin_key')
on conflict (name) do nothing;

-- Collect the previous tick's reply, then issue the next request.
--
-- Returns a short description of what it did, for a human calling it by hand. That value
-- does NOT reach `cron.job_run_details.return_message`: pg_cron stores the row count of a
-- `SELECT`, so that column reads `1 row` on every tick whatever happened. `ops_monitor_state`
-- is the record, and it is written to *record* failures rather than raise them:
-- pg_cron runs each tick in its own transaction, and an exception would roll the record back
-- with it, leaving the one table that is supposed to say what went wrong saying nothing at
-- all. Where a failure is also worth seeing in the Postgres log it is raised as a warning,
-- which does not roll anything back. Nothing raised or returned here ever contains the key.
create or replace function ops_monitor_tick() returns text
language plpgsql
as $$
declare
  s                 ops_monitor_state;
  v_code            integer;
  v_timed_out       boolean;
  v_error           text;
  v_content         text;
  v_body            jsonb;
  v_reported        text;
  v_failing         text[];
  v_valid           boolean;
  v_secret          text;
  v_request         bigint;
  v_collected       text := 'nothing pending';
begin
  -- One tick at a time. A transaction-scoped lock is released when this transaction ends,
  -- including when it fails, so a tick that dies half way cannot wedge every later one the
  -- way a session-scoped lock could. Two ticks overlapping would otherwise both collect the
  -- same reply and then both overwrite `pending_request_id`, orphaning a request and
  -- recording its absence as a failure on the tick after.
  if not pg_try_advisory_xact_lock(hashtext('ops_monitor_tick')::bigint) then
    return 'skipped: another tick is running';
  end if;

  -- `for update` and not merely `select`: the row is read, decided on and written in one
  -- transaction, and the lock above is advisory rather than enforced by the row.
  select * into s from ops_monitor_state where name = 'ops-health' for update;
  if not found then
    raise warning 'ops_monitor_tick: no ops-health row in ops_monitor_state';
    return 'skipped: not configured';
  end if;
  if not s.enabled then
    -- Let go of anything in flight before standing down. Returning with the request still
    -- held would leave two things behind: a `pending_request_id` nothing will ever collect,
    -- and its reply sitting in `net._http_response` until pg_net's own six hour prune
    -- rather than until the tick that asked for it. Switching the monitor back on days
    -- later would then collect a request whose reply had long since been pruned and record
    -- "the pg_net worker may not be running", which is a true-sounding diagnosis of the
    -- wrong system.
    if s.pending_request_id is not null then
      if to_regclass('net._http_response') is not null then
        delete from net._http_response where id = s.pending_request_id;
      end if;
      update ops_monitor_state
         set pending_request_id = null, pending_since = null
       where name = 'ops-health';
      return 'skipped: disabled, released the request in flight';
    end if;
    return 'skipped: disabled';
  end if;

  -- Checked before the collect and not only before the send, because `pg_net` disappearing
  -- between two ticks would otherwise make the read below raise `undefined_table`, and an
  -- exception rolls this transaction back: the one table that is meant to say what went
  -- wrong would say nothing, every tick, forever.
  if to_regclass('net._http_response') is null then
    update ops_monitor_state
       set pending_request_id   = null,
           pending_since        = null,
           last_failure_at      = now(),
           last_status_code     = null,
           last_reported_status = null,
           last_failing_checks  = null,
           last_error           = 'pg_net is not installed on this database, so no request '
                                  'can be sent. Enable it once as the project owner, then '
                                  'apply migration 013',
           consecutive_failures = s.consecutive_failures + 1
     where name = 'ops-health';
    raise warning 'ops_monitor_tick: pg_net is not installed';
    return 'failed: pg_net is missing';
  end if;

  -- ── collect the previous tick's reply ────────────────────────────────────────
  if s.pending_request_id is not null then
    select r.status_code, r.timed_out, r.error_msg, r.content
      into v_code, v_timed_out, v_error, v_content
      from net._http_response r
     where r.id = s.pending_request_id;

    if not found then
      -- No reply recorded at all. Early on that only means the worker has not got to it,
      -- so a request younger than a minute is left alone and collected next tick. Older
      -- than that and it is lost: `pg_net`'s worker is not running, or the response was
      -- pruned (`pg_net.ttl`, six hours by default), or the extension was dropped.
      if s.pending_since > now() - interval '1 minute' then
        return 'waiting for the previous reply';
      end if;
      update ops_monitor_state
         set pending_request_id   = null,
             pending_since        = null,
             last_failure_at      = now(),
             last_status_code     = null,
             last_reported_status = null,
             last_failing_checks  = null,
             last_error           = 'no reply was ever recorded for the request: the pg_net '
                                    'worker may not be running',
             consecutive_failures = s.consecutive_failures + 1
       where name = 'ops-health';
      raise warning 'ops_monitor_tick: request % left no reply', s.pending_request_id;
      return 'failed: no reply recorded';

    elsif coalesce(v_timed_out, false) then
      update ops_monitor_state
         set pending_request_id   = null,
             pending_since        = null,
             last_failure_at      = now(),
             last_status_code     = null,
             last_reported_status = null,
             last_failing_checks  = null,
             last_error           = 'the request timed out',
             consecutive_failures = s.consecutive_failures + 1
       where name = 'ops-health';
      v_collected := 'failed: timed out';

    elsif v_code is null then
      -- A transport failure: DNS, TLS, connection refused. `error_msg` is curl's own text
      -- and carries the URL at most, never a header.
      update ops_monitor_state
         set pending_request_id   = null,
             pending_since        = null,
             last_failure_at      = now(),
             last_status_code     = null,
             last_reported_status = null,
             last_failing_checks  = null,
             last_error           = left(coalesce(v_error, 'the request failed'), 500),
             consecutive_failures = s.consecutive_failures + 1
       where name = 'ops-health';
      v_collected := 'failed: no response';

    elsif v_code between 200 and 299 or v_code = 503 then
      -- Something answered with a status `ops-health` itself uses. Whether it *was*
      -- `ops-health` is a separate question, and one this has to ask.
      --
      -- A status code alone proves only that something at the configured URL replied. A
      -- marketing page, a proxy's sign-in redirect or a parked domain answers 200 with
      -- HTML, and recording that as the heartbeat gives a monitor which is permanently
      -- green and has never once spoken to this platform. 503 is no safer: the route
      -- answers 503 with `{"detail": "Database unavailable."}` when it cannot reach the
      -- database at all, and a load balancer answers 503 with its own page, neither of
      -- which is `ops-health` reporting on the platform.
      --
      -- So the heartbeat requires the shape of the real answer: JSON carrying a `checks`
      -- array. That keeps the deliberate 503 behaviour intact, because `ops-health`'s own
      -- 503 carries the full body and passes, while making it mean what it was always
      -- supposed to mean: this endpoint, not merely this port.
      begin
        v_body := v_content::jsonb;
      exception when others then
        v_body := null;
      end;
      -- `coalesce`, and not decoration. When the body did not parse, `v_body` is null, so
      -- `v_body -> 'checks'` is null, `jsonb_typeof` of that is null, and `null = 'array'`
      -- is null rather than false: `if not v_valid` on a null is not taken, and every
      -- unparseable body fell straight through into the heartbeat branch. That is exactly
      -- the bug this validation exists to fix, reintroduced one line below the fix.
      v_valid := coalesce(jsonb_typeof(v_body -> 'checks') = 'array', false);

      if not v_valid then
        update ops_monitor_state
           set pending_request_id   = null,
               pending_since        = null,
               last_failure_at      = now(),
               last_status_code     = v_code,
               last_reported_status = null,
               last_failing_checks  = null,
               last_error           = 'something answered HTTP ' || v_code || ' at the '
                                      'configured URL but not with an ops-health body (no '
                                      'checks array), so nothing here has confirmed this '
                                      'platform. Check the url column of ops_monitor_state, '
                                      'and whether a proxy is answering in front of the API',
               consecutive_failures = s.consecutive_failures + 1
         where name = 'ops-health';
        v_collected := 'failed: HTTP ' || v_code || ' but not an ops-health body';
      else
        -- The endpoint answered, which is what the heartbeat is about. A 503 here is not a
        -- failure of the monitor: it is `ops-health` doing its job and reporting an alert,
        -- and that verdict is recorded so the history shows what it said rather than only
        -- that it spoke.
        v_reported := v_body ->> 'status';
        select array_agg(c ->> 'name' order by c ->> 'name')
          into v_failing
          from jsonb_array_elements(v_body -> 'checks') c
         where coalesce(c ->> 'status', 'ok') <> 'ok';

        update ops_monitor_state
           set pending_request_id   = null,
               pending_since        = null,
               last_success_at      = now(),
               last_status_code     = v_code,
               last_reported_status = v_reported,
               last_failing_checks  = v_failing,
               last_error           = null,
               consecutive_failures = 0
         where name = 'ops-health';
        v_collected := 'answered ' || v_code || coalesce(': ' || v_reported, '');
      end if;

    else
      -- Anything else is the endpoint refusing or breaking. 401 and 403 are the ones worth
      -- naming, because they mean the key in Vault was revoked or replaced and the monitor
      -- will keep failing until it is reissued; the generic message would send an operator
      -- looking at the wrong thing.
      update ops_monitor_state
         set pending_request_id   = null,
             pending_since        = null,
             last_failure_at      = now(),
             last_status_code     = v_code,
             last_reported_status = null,
             last_failing_checks  = null,
             last_error           = case
               when v_code in (401, 403) then
                 'the monitor key was refused (HTTP ' || v_code || '): the operator key in '
                 'the Vault secret has probably been revoked or replaced'
               else 'the endpoint answered HTTP ' || v_code end,
             consecutive_failures = s.consecutive_failures + 1
       where name = 'ops-health';
      v_collected := 'failed: HTTP ' || v_code;
    end if;

    -- Tidy up after ourselves. `net._http_response` is pruned on `pg_net.ttl`, but a row
    -- this function has finished with has no further reader, and leaving it would keep the
    -- response body of every probe for six hours for no purpose.
    delete from net._http_response where id = s.pending_request_id;
  end if;

  -- ── issue the next request ───────────────────────────────────────────────────
  --
  -- Each precondition records its own reason rather than a generic failure, because "the
  -- monitor is not working" is not a useful thing to read when the remedy is one specific
  -- missing step.
  --
  -- Read the key into a local variable and nowhere else. It is not stored, not returned,
  -- and not named in any message this function raises. `vault.decrypted_secrets` is
  -- readable only by the roles Supabase grants it to, which does not include `anon`,
  -- `authenticated` or `service_role`; this function runs as its owner's invoker, which is
  -- the role that schedules it in 013.
  if to_regclass('vault.decrypted_secrets') is null then
    v_secret := null;
  else
    execute 'select decrypted_secret from vault.decrypted_secrets where name = $1'
       into v_secret using s.secret_name;
  end if;

  if v_secret is null or v_secret = '' then
    update ops_monitor_state
       set last_failure_at      = now(),
           last_status_code     = null,
           last_reported_status = null,
           last_failing_checks  = null,
           last_error           = 'the Vault secret named in secret_name does not exist or '
                                  'is empty, so there is no key to call the endpoint with. '
                                  'Create it with vault.create_secret',
           consecutive_failures = s.consecutive_failures + 1
     where name = 'ops-health';
    raise warning 'ops_monitor_tick: the Vault secret % is missing or empty', s.secret_name;
    return 'failed: no secret';
  end if;

  -- Ten seconds. The API is on a plan that spins down after fifteen minutes of quiet, so a
  -- cold start can take several seconds; this is generous enough not to call that a fault
  -- and short enough that a hung request is collected as a timeout rather than left open.
  -- A side effect worth naming: probing every ten minutes keeps that plan from spinning
  -- down at all, so the first clinician of the morning does not pay for the cold start.
  select net.http_get(
           url := s.url,
           headers := jsonb_build_object('X-Admin-Key', v_secret),
           timeout_milliseconds := 10000)
    into v_request;

  update ops_monitor_state
     set pending_request_id = v_request,
         pending_since      = now(),
         last_run_at        = now(),
         runs               = s.runs + 1
   where name = 'ops-health';

  return v_collected || '; sent ' || v_request;
end;
$$;

-- Service key only, and only what it actually needs.
--
-- As in 009 and 010, the revoke is what makes the grant necessary rather than optional: it
-- removes the default EXECUTE every new function carries, and the blanket privileges
-- Supabase hands `anon` and `authenticated` on new objects in `public`, which on this
-- project are real and are held off `item_reservations` by row level security alone.
--
-- `ops_monitor_tick()` is granted to nobody at all. It reads a decrypted Vault secret and
-- sends it over the network; the only caller that should ever have it is the schedule in
-- 013, which runs as the owner of these objects. The monitor's record is a different
-- matter: `ops-health` has to read it, and `ops-health` runs under the service key.
revoke all on ops_monitor_state from public, anon, authenticated;
revoke all on function ops_monitor_tick() from public, anon, authenticated;
-- Named separately, and not redundant with the PUBLIC revoke above. On a clean install it
-- removes nothing, because `service_role` holds nothing of its own. Its job is the second
-- run: re-applying this file after somebody granted the service key EXECUTE directly is
-- what restores the posture, and a PUBLIC revoke does not touch a grant made to a named
-- role. tests/sql/test_ops_monitor_sql.py grants it first for exactly that reason.
revoke all on function ops_monitor_tick() from service_role;

grant select on ops_monitor_state to service_role;

commit;

-- Switching it on is migration 013, after two owner actions it names. Until then this row
-- sits with `enabled = false` and `ops-health` reports `ops_monitor_stalled` as a warning
-- saying exactly that: see docs/operations.md, "Switching the in-database monitor on".
