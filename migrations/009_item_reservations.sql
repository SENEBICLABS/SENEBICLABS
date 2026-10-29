-- Migration 009: a case is reserved when it is served, not when it is submitted
-- Paste into the Supabase SQL editor (Dashboard -> SQL Editor -> New query -> Run).
-- Safe to re-run. Apply after 008.
--
-- WHY
--
-- `/next` chose the lowest open task number and returned it without recording anything,
-- so every clinician asking at the same moment was handed the same case. Measured on a
-- throwaway stack: five clinicians, hundreds of free cases, five identical task numbers;
-- and in a four-clinician run, 343 submissions refused against 120 accepted. The refusals
-- were themselves correct, because `native_submit_judgement` guards the ceiling under a
-- row lock, but by then the clinician had already read the case and formed a judgement.
-- That is time we pay for and throw away, and it is the clinician's goodwill we spend.
--
-- Serving now records a reservation: one short transaction, then a time-bounded lease.
-- No lock is held while a clinician reads. An expired lease stops consuming capacity by
-- predicate (`expires_at > now()`), so recovery is correct even if no sweep ever runs.
--
-- A RESERVATION IS NOT A REVIEW. Capacity is live reservations plus non-withdrawn
-- answers; only the answers are clinical evidence, and only they are scored or delivered.

begin;

create table if not exists item_reservations (
  id            bigint generated always as identity primary key,
  task_no       integer not null references project_items (task_no) on delete cascade,
  project_id    uuid    not null references project_submissions (id) on delete cascade,
  pool_id       uuid    not null references pools (id) on delete cascade,
  clinician_id  uuid    not null references clinicians (id) on delete cascade,

  -- The version the clinician was shown. A lease never survives a supersede: an answer
  -- formed on withdrawn content must not fill a slot on its replacement.
  item_version  integer not null default 1,

  -- What the lease is for. An adjudication is not one of the readings a client
  -- commissioned: it is the decision that settles them. Counting it as a reading would
  -- push a held case past its own ceiling, so `item_slots_taken` counts only 'review'.
  kind          text    not null default 'review'
                  check (kind in ('review', 'adjudication')),

  status        text    not null default 'active'
                  check (status in ('active', 'submitted', 'released', 'expired')),
  created_at    timestamptz not null default now(),
  expires_at    timestamptz not null,
  renewals      integer not null default 0,
  released_at   timestamptz,
  release_reason text
);

do $$
begin
  alter table item_reservations add column if not exists kind text not null default 'review';
  begin
    alter table item_reservations add constraint item_reservations_kind
      check (kind in ('review', 'adjudication'));
  exception when duplicate_object then null;
  end;
end $$;

-- Independence enforced by the database rather than by application discipline: one
-- clinician holds at most one live lease on a case. A retried request collides with this
-- index instead of creating a second lease, so serving is idempotent by construction.
create unique index if not exists item_reservations_one_live
  on item_reservations (task_no, clinician_id) where status = 'active';

create index if not exists item_reservations_live
  on item_reservations (task_no) where status = 'active';
create index if not exists item_reservations_by_clinician
  on item_reservations (clinician_id, status);
create index if not exists item_reservations_by_project
  on item_reservations (project_id, status);
create index if not exists item_reservations_expiry
  on item_reservations (expires_at) where status = 'active';

alter table item_reservations enable row level security;

comment on table item_reservations is
  'A case served to a clinician, leased for a bounded time. Capacity = live reservations '
  '+ non-withdrawn answers. A reservation is never clinical evidence.';


-- How many of a case's review slots are already spoken for: answers that count, plus
-- leases that have not expired. One definition, so serving and submitting cannot drift.
--
-- A lease is ignored once its holder has answered, otherwise one clinician who submitted
-- would occupy two slots and the case would be declared full a review short. Correctness
-- therefore does not depend on anything calling `settle_reservation`: that call tidies the
-- row, it does not make the arithmetic right.
create or replace function item_slots_taken(p_task_no integer)
returns integer language sql stable as $$
  select (select count(*) from task_answers
            where task_no = p_task_no and withdrawn_at is null)
       + (select count(*) from item_reservations r
            where r.task_no = p_task_no and r.status = 'active' and r.expires_at > now()
              and r.kind = 'review'
              and not exists (select 1 from task_answers a
                               where a.task_no = r.task_no
                                 and a.clinician_id = r.clinician_id
                                 and a.withdrawn_at is null))
$$;


-- What the clinician platform needs to count a pool honestly: per released case, the
-- slots already spoken for, and whether this clinician is the one holding one.
--
-- It exists so the arithmetic is not written twice. A pool card saying work is waiting
-- while every free slot is leased sends a clinician to an empty queue, which is the same
-- class of mistake as the one migration 009 fixes, and the fix is the same: one
-- definition, in `item_slots_taken`, used by both the count and the serving.
create or replace function item_lease_states(p_project uuid, p_clinician uuid)
returns table (task_no integer, slots_taken integer, mine boolean)
language sql stable as $$
  select i.task_no,
         item_slots_taken(i.task_no),
         -- `mine` is work in their hands, so a lease they have already answered is not
         -- theirs any more: offering it back would send them to a case they cannot submit.
         exists (select 1 from item_reservations r
                  where r.task_no = i.task_no and r.clinician_id = p_clinician
                    and r.status = 'active' and r.expires_at > now()
                    and r.kind = 'review'
                    and not exists (select 1 from task_answers a
                                     where a.task_no = i.task_no
                                       and a.clinician_id = p_clinician
                                       and a.withdrawn_at is null))
    from project_items i
   where i.project_id = p_project
     and i.status in ('queued', 'in_progress')
     and i.superseded_at is null
   order by i.task_no
$$;


-- Serve the next case to one clinician, atomically.
--
-- Capacity comes from `native_ceiling` and eligibility from the same rule
-- `native_task_for_pool` applies, so nothing a caller passes can widen either.
--
-- Partly reviewed cases are offered first. Spreading clinicians across fresh cases would
-- also avoid collisions, but it leaves a project full of half-reviewed work and nothing
-- the client can be sent; filling a case to its ceiling is what produces a settled
-- finding. So the fourth and fifth clinicians on a three-review case get other work,
-- which is exactly the behaviour we want.
--
-- `for update skip locked` is what fixes the collision: two callers arriving together
-- lock different rows rather than queueing on the same one. The transaction ends when
-- this function returns; the lease, not a lock, holds the case while the clinician reads.
create or replace function reserve_next_item(
  p_clinician_id  uuid,
  p_pool_id       uuid,
  p_lease_minutes integer default 60
) returns table (
  task_no      integer,
  item_id      uuid,
  item_version integer,
  expires_at   timestamptz
) language plpgsql as $$
-- The output column names above shadow `item_reservations`' own columns inside this
-- body, and `on conflict (task_no, ...)` cannot be table-qualified. Every variable here
-- is p_ or v_ prefixed, so resolving a bare name to the column is always what is meant.
#variable_conflict use_column
declare
  v_project uuid;
  v_cap     integer;
  v_expires timestamptz;
  v_cand    record;
  v_task    integer;
  v_item    uuid;
  v_version integer;
begin
  if p_lease_minutes is null or p_lease_minutes < 5 or p_lease_minutes > 480 then
    raise exception 'reserve_next_item: lease must be 5..480 minutes, got %', p_lease_minutes;
  end if;

  -- Eligibility, in the same terms as every other write: the pool is open, it belongs to
  -- a project on the task store, and this clinician is eligible for it.
  select p.project_id into v_project
    from pools p
    join project_submissions s on s.id = p.project_id and s.task_store = 'native'
   where p.id = p_pool_id
     and p.closed_at is null
     and exists (select 1 from pool_eligibility e
                  where e.pool_id = p_pool_id and e.clinician_id = p_clinician_id and e.eligible);
  if v_project is null then
    return;                                  -- not eligible: no work, and we never say why
  end if;

  v_cap := native_ceiling(v_project);
  v_expires := now() + make_interval(mins => p_lease_minutes);

  -- A lease they already hold is served back to them before anything else. Reconnecting,
  -- reloading the page or coming back after lunch must return the same case: drawing a
  -- fresh one would leave the first held and unanswered, so a clinician who refreshed
  -- twice would be holding three slots and reading one.
  select r.task_no, i.id, r.item_version into v_task, v_item, v_version
    from item_reservations r
    join project_items i on i.task_no = r.task_no
   where r.clinician_id = p_clinician_id
     and r.project_id = v_project
     and r.status = 'active'
     and r.expires_at > now()
     and i.status in ('queued', 'in_progress')
     and i.superseded_at is null
     and not exists (select 1 from task_answers a
                      where a.task_no = r.task_no and a.clinician_id = p_clinician_id
                        and a.withdrawn_at is null)
   order by r.created_at
   limit 1;
  if v_task is not null then
    return query select v_task, v_item, v_version, (
      select r.expires_at from item_reservations r
       where r.task_no = v_task and r.clinician_id = p_clinician_id and r.status = 'active');
    return;
  end if;

  -- Candidates, best first: cases with work already on them, then untouched ones. Both
  -- passes are index-driven, and the partial pass is bounded by work in flight rather
  -- than by the size of the project.
  for v_cand in
    select c.task_no, c.id, c.version from (
      select i.task_no, i.id, i.version, 1 as pass
        from project_items i
       where i.project_id = v_project
         and i.status in ('queued', 'in_progress')
         and i.superseded_at is null
         and exists (select 1 from task_answers a
                      where a.task_no = i.task_no and a.withdrawn_at is null)
      union all
      select i.task_no, i.id, i.version, 2 as pass
        from project_items i
       where i.project_id = v_project
         and i.status in ('queued', 'in_progress')
         and i.superseded_at is null
         and not exists (select 1 from task_answers a
                          where a.task_no = i.task_no and a.withdrawn_at is null)
    ) c
    order by c.pass, c.task_no
    limit 200
  loop
    -- Anything this clinician has already done with the case puts it out of reach: an
    -- answer, a pass of any kind, a flag, or a lease they are already holding. The
    -- clinician platform's own rule is the same, by design, and `task_flags` is part of
    -- the schema baseline, so a missing table is a loud failure here rather than a quiet
    -- re-offering of a case somebody declined.
    if exists (select 1 from task_answers a
                where a.task_no = v_cand.task_no and a.clinician_id = p_clinician_id
                  and a.withdrawn_at is null)
       or exists (select 1 from task_completions tc
                   where tc.ls_task_id = v_cand.task_no and tc.clinician_id = p_clinician_id)
       or exists (select 1 from task_flags tf
                   where tf.ls_task_id = v_cand.task_no and tf.clinician_id = p_clinician_id)
       or exists (select 1 from item_reservations r
                   where r.task_no = v_cand.task_no and r.clinician_id = p_clinician_id
                     and r.status = 'active' and r.expires_at > now())
    then
      continue;
    end if;

    -- A senior has settled it; there is nothing left to review.
    if exists (select 1 from task_completions
                where ls_task_id = v_cand.task_no and kind = 'adjudication') then
      continue;
    end if;

    -- Take the row, or move on if another request has it this instant.
    select i.task_no, i.id, i.version into v_task, v_item, v_version
      from project_items i where i.task_no = v_cand.task_no
      for update skip locked;
    if v_task is null then
      continue;
    end if;

    -- Re-checked under the lock: this is the test that actually enforces the ceiling.
    if item_slots_taken(v_task) >= v_cap then
      v_task := null;
      continue;
    end if;

    insert into item_reservations
      (task_no, project_id, pool_id, clinician_id, item_version, expires_at)
    values
      (v_task, v_project, p_pool_id, p_clinician_id, v_version, v_expires)
    on conflict (task_no, clinician_id) where status = 'active' do nothing;

    return query select v_task, v_item, v_version, v_expires;
    return;
  end loop;

  return;                                     -- nothing eligible; the caller answers 204
end;
$$;


-- An earlier revision of this migration took four arguments. `create or replace` cannot
-- change a signature, so without this drop the old function would survive as an overload
-- and a four-argument call would keep resolving to it, silently skipping the bounds added
-- below. Dropping it is safe: nothing stores a reference to a function.
drop function if exists renew_reservation(uuid, integer, integer, integer);

-- Assign a held case to one senior, atomically.
--
-- THE POLICY, which this function implements rather than invents:
--
--   One senior settles a case, and that decision is final. `task_completions`' unique
--   index `task_completions_one_adjudication` (workforce migration 019) already allows
--   exactly one adjudication per case, whoever makes it, so a second independent
--   adjudicator is not merely unnecessary, it is forbidden. A senior who read the case as
--   an ordinary reader may not settle it: they are no longer an independent third reader
--   of a disagreement they are part of. Where no senior can settle it, an operator does,
--   through HEALTH's own `/admin/review` (docs/eval-operations.md).
--
-- What was missing was the assignment. A held case is at its reviewer ceiling by
-- definition, so `reserve_next_item` refuses it, and it was therefore served to every
-- free senior at once. The second to submit was refused by the unique index, correctly,
-- after reading the case and forming a judgement. The decision was never at risk; a
-- senior's time was.
--
-- Abandonment is handled by the lease and nothing else. A senior who closes the tab holds
-- the case until their lease lapses, and then another senior may take it. That is the
-- answer to "what if they walk away": the case is never locked to a person, only to a
-- person for a bounded time.
--
-- Outcomes:
--   the task number, or no row at all, which means there is nothing for this senior to
--   settle: not held, already settled, held by another senior right now, their own
--   reading, or they are not senior here.
create or replace function reserve_adjudication(
  p_clinician_id  uuid,
  p_pool_id       uuid,
  p_lease_minutes integer default 60
) returns table (
  task_no      integer,
  item_id      uuid,
  item_version integer,
  expires_at   timestamptz
) language plpgsql as $$
#variable_conflict use_column
declare
  v_project uuid;
  v_expires timestamptz;
  v_cand    record;
  v_task    integer;
  v_item    uuid;
  v_version integer;
begin
  if p_lease_minutes is null or p_lease_minutes < 5 or p_lease_minutes > 480 then
    raise exception 'reserve_adjudication: lease must be 5..480 minutes, got %', p_lease_minutes;
  end if;

  -- Senior for this pool, read now rather than inferred from holding anything.
  select p.project_id into v_project
    from pools p
    join project_submissions s on s.id = p.project_id and s.task_store = 'native'
   where p.id = p_pool_id
     and p.closed_at is null
     and exists (select 1 from pool_eligibility e
                  where e.pool_id = p_pool_id and e.clinician_id = p_clinician_id
                    and e.eligible and e.senior);
  if v_project is null then
    return;
  end if;

  v_expires := now() + make_interval(mins => p_lease_minutes);

  -- A hold they already have comes back to them, so a reload resumes the decision.
  select r.task_no, i.id, r.item_version into v_task, v_item, v_version
    from item_reservations r
    join project_items i on i.task_no = r.task_no
   where r.clinician_id = p_clinician_id and r.project_id = v_project
     and r.kind = 'adjudication' and r.status = 'active' and r.expires_at > now()
     and i.status = 'needs_adjudication'
   order by r.created_at
   limit 1;
  if v_task is not null then
    return query select v_task, v_item, v_version, (
      select r.expires_at from item_reservations r
       where r.task_no = v_task and r.clinician_id = p_clinician_id and r.status = 'active');
    return;
  end if;

  for v_cand in
    select i.task_no, i.id, i.version
      from project_items i
     where i.project_id = v_project
       and i.status = 'needs_adjudication'
       and i.superseded_at is null
     order by i.task_no
     limit 200
  loop
    -- Already settled: there is nothing left to decide.
    if exists (select 1 from task_completions
                where ls_task_id = v_cand.task_no and kind = 'adjudication') then
      continue;
    end if;
    -- They read it themselves, so they are not independent of the split.
    if exists (select 1 from task_completions
                where ls_task_id = v_cand.task_no and clinician_id = p_clinician_id)
       or exists (select 1 from task_answers a
                   where a.task_no = v_cand.task_no and a.clinician_id = p_clinician_id
                     and a.withdrawn_at is null) then
      continue;
    end if;
    -- Another senior is settling it right now.
    if exists (select 1 from item_reservations r
                where r.task_no = v_cand.task_no and r.kind = 'adjudication'
                  and r.status = 'active' and r.expires_at > now()) then
      continue;
    end if;

    select i.task_no, i.id, i.version into v_task, v_item, v_version
      from project_items i where i.task_no = v_cand.task_no
      for update skip locked;
    if v_task is null then
      continue;
    end if;

    -- Re-checked under the lock, which is what makes two seniors arriving together safe.
    if exists (select 1 from item_reservations r
                where r.task_no = v_task and r.kind = 'adjudication'
                  and r.status = 'active' and r.expires_at > now())
       or exists (select 1 from task_completions
                   where ls_task_id = v_task and kind = 'adjudication') then
      v_task := null;
      continue;
    end if;

    insert into item_reservations
      (task_no, project_id, pool_id, clinician_id, item_version, expires_at, kind)
    values
      (v_task, v_project, p_pool_id, p_clinician_id, v_version, v_expires, 'adjudication')
    on conflict (task_no, clinician_id) where status = 'active' do nothing;

    return query select v_task, v_item, v_version, v_expires;
    return;
  end loop;

  return;
end;
$$;


-- Renew a lease for a review that is legitimately taking a long time.
--
-- Three separate bounds, because one is not enough. The renewal count stops a tab from
-- renewing forever; the absolute age stops a long-lived lease being kept alive by a
-- client that renews more often than expected; and the per-renewal length stops any one
-- extension being unbounded. A case must come back to the pool eventually whatever the
-- caller does, because a held case is work nobody else can do.
--
-- Outcomes the caller maps to responses:
--   renewed        the lease now runs to the new expiry
--   not_found      no live lease on this case for this clinician
--   expired        it had already lapsed; it is not revived, because the slot may be gone
--   limit_reached  renewed as many times as allowed
--   too_old        the lease has been alive longer than any review should take
--   stale          the case has been re-uploaded since it was served
--   invalid        the requested lease length is outside the permitted range
create or replace function renew_reservation(
  p_clinician_id     uuid,
  p_task_no          integer,
  p_lease_minutes    integer default 60,
  p_max_renewals     integer default 4,
  p_max_total_minutes integer default 480
) returns text language plpgsql as $$
declare
  v_id       bigint;
  v_renewals integer;
  v_expires  timestamptz;
  v_created  timestamptz;
  v_version  integer;
  v_current  integer;
begin
  if p_lease_minutes is null or p_lease_minutes < 5 or p_lease_minutes > 480 then
    return 'invalid';
  end if;
  if p_max_total_minutes is null or p_max_total_minutes < p_lease_minutes
     or p_max_total_minutes > 1440 then
    return 'invalid';
  end if;

  select r.id, r.renewals, r.expires_at, r.created_at, r.item_version
    into v_id, v_renewals, v_expires, v_created, v_version
    from item_reservations r
   where r.task_no = p_task_no and r.clinician_id = p_clinician_id and r.status = 'active'
   for update;

  if v_id is null then return 'not_found'; end if;

  -- An expired lease is not renewable. The slot may already belong to someone else, and
  -- quietly extending it would leave two clinicians believing they hold the same one.
  if v_expires <= now() then
    update item_reservations set status = 'expired' where id = v_id;
    return 'expired';
  end if;

  -- The case must still be the one they were shown. Superseding releases the lease by
  -- trigger, so this is the belt to that braces: if a version ever changes by another
  -- path, a renewal must not extend a hold on content nobody is reading.
  select i.version into v_current from project_items i where i.task_no = p_task_no;
  if v_current is distinct from v_version then
    update item_reservations
       set status = 'released', released_at = now(), release_reason = 'stale_version'
     where id = v_id;
    return 'stale';
  end if;

  if v_renewals >= p_max_renewals then return 'limit_reached'; end if;

  -- The absolute bound. No review of a single case runs for a working day, and a lease
  -- that has lived that long is a forgotten tab or a loop, not a careful clinician.
  if v_created + make_interval(mins => p_max_total_minutes) <= now() then
    update item_reservations
       set status = 'expired', released_at = now(), release_reason = 'max_age'
     where id = v_id;
    return 'too_old';
  end if;

  update item_reservations
     set expires_at = least(now() + make_interval(mins => p_lease_minutes),
                            v_created + make_interval(mins => p_max_total_minutes)),
         renewals = renewals + 1
   where id = v_id;
  return 'renewed';
end;
$$;


-- Give a case back without answering it: a skip, a sign-out, a tab that told us it closed.
create or replace function release_reservation(
  p_clinician_id uuid, p_task_no integer, p_reason text default 'released'
) returns text language plpgsql as $$
begin
  update item_reservations
     set status = 'released', released_at = now(), release_reason = left(p_reason, 200)
   where task_no = p_task_no and clinician_id = p_clinician_id and status = 'active';
  return case when found then 'released' else 'not_found' end;
end;
$$;


-- Mark the lease spent once its answer is recorded, so the row stops counting as a
-- reservation and starts counting as an answer. Called from the submit path; harmless if
-- there was no lease, which keeps older clients and the admin tools working.
create or replace function settle_reservation(
  p_clinician_id uuid, p_task_no integer
) returns text language plpgsql as $$
begin
  update item_reservations
     set status = 'submitted', released_at = now()
   where task_no = p_task_no and clinician_id = p_clinician_id and status = 'active';
  return case when found then 'submitted' else 'not_found' end;
end;
$$;


-- Superseding an item replaces the content a clinician was asked to judge, so holding a
-- lease on it is holding nothing. `native_task_for_pool` would refuse the submission, but
-- only after the reading was done. Releasing here sends them back for fresh work instead,
-- and as a trigger it covers every path that supersedes, including an operator's own SQL.
create or replace function release_leases_on_supersede() returns trigger
language plpgsql as $$
begin
  update item_reservations
     set status = 'released', released_at = now(), release_reason = 'superseded'
   where task_no = new.task_no and status = 'active';
  return new;
end;
$$;

drop trigger if exists item_reservations_supersede on project_items;
create trigger item_reservations_supersede
  after update of status on project_items
  for each row when (new.status = 'superseded' and old.status <> 'superseded')
  execute function release_leases_on_supersede();


-- What a monitor needs to tell "busy" from "stuck", per project, computed here rather
-- than by dragging every item through PostgREST.
--
-- The distinction that matters: a project with no servable items is normal when its work
-- is simply finished, and a fault when the work is there but every free slot is held by a
-- lease that is never coming back. `lease_blocked_items` separates the two, and
-- `oldest_blocking_lease` is what tells an operator whether it has been that way for
-- minutes or for hours. The thresholds live with the other thresholds, in ops_health.
create or replace function reservation_health()
returns table (
  project_id            uuid,
  open_items            bigint,
  servable_items        bigint,
  lease_blocked_items   bigint,
  oldest_blocking_lease timestamptz
) language sql stable as $$
  with open_items as (
    select i.task_no, i.project_id,
           item_slots_taken(i.task_no) < native_ceiling(i.project_id) as servable
      from project_items i
      join project_submissions s on s.id = i.project_id and s.task_store = 'native'
     where i.status in ('queued', 'in_progress')
       and i.superseded_at is null
  ),
  blocked as (
    select o.task_no, o.project_id, min(r.created_at) as since
      from open_items o
      join item_reservations r
        on r.task_no = o.task_no and r.status = 'active' and r.expires_at > now()
       and r.kind = 'review'
     where not o.servable
     group by o.task_no, o.project_id
  )
  select o.project_id,
         count(*),
         count(*) filter (where o.servable),
         (select count(*) from blocked b where b.project_id = o.project_id),
         (select min(b.since) from blocked b where b.project_id = o.project_id)
    from open_items o
   group by o.project_id
$$;


-- Housekeeping only. Correctness does not depend on it: every query that cares already
-- tests `expires_at > now()`. It keeps the table readable for operators and keeps the
-- lapsed-lease metric honest.
create or replace function expire_reservations() returns integer
language plpgsql as $$
declare n integer;
begin
  update item_reservations set status = 'expired'
   where status = 'active' and expires_at <= now();
  get diagnostics n = row_count;
  return n;
end;
$$;

-- Service key only. PostgREST would otherwise expose these to the anon key.
--
-- The grant is explicit and is not optional. Revoking from `public` removes the default
-- EXECUTE that every new function carries, and `service_role` holds nothing of its own
-- unless it is said here. Without these two lines the functions exist, PostgREST lists
-- them, and every call comes back 42501: serving answers 502 on every case. Migrations
-- 005 and 006 got away with relying on a blanket grant applied afterwards; this one does
-- not rely on anything.
revoke all on function item_slots_taken(integer) from public, anon, authenticated;
revoke all on function item_lease_states(uuid, uuid) from public, anon, authenticated;
revoke all on function reservation_health() from public, anon, authenticated;
revoke all on function reserve_next_item(uuid, uuid, integer) from public, anon, authenticated;
revoke all on function reserve_adjudication(uuid, uuid, integer) from public, anon, authenticated;
revoke all on function renew_reservation(uuid, integer, integer, integer, integer) from public, anon, authenticated;
revoke all on function release_reservation(uuid, integer, text) from public, anon, authenticated;
revoke all on function settle_reservation(uuid, integer) from public, anon, authenticated;
revoke all on function expire_reservations() from public, anon, authenticated;

grant execute on function
  item_slots_taken(integer),
  item_lease_states(uuid, uuid),
  reservation_health(),
  reserve_next_item(uuid, uuid, integer),
  reserve_adjudication(uuid, uuid, integer),
  renew_reservation(uuid, integer, integer, integer, integer),
  release_reservation(uuid, integer, text),
  settle_reservation(uuid, integer),
  expire_reservations()
to service_role;

commit;
