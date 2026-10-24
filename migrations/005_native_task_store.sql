-- Migration 005: the native task store (replaces Label Studio as the task and answer store)
-- Paste into the Supabase SQL editor (Dashboard -> SQL Editor -> New query -> Run).
-- Safe to re-run.
--
-- ORDER: apply the workforce repo's migrations/021_pool_project.sql FIRST. The functions
-- below read pools.project_id, which that migration adds. Both live in the one shared
-- database; this file is in HEALTH because HEALTH is the system of record for tasks.
--
-- What this adds, and why each piece exists, is in docs/native-task-store.md. In short:
--   task_no        every item gets a stable integer task number, so the workforce tables
--                  (task_completions, task_flags, review_items) keep their integer
--                  ls_task_id columns and need no type change
--   task_store     per project: 'label_studio' (today's path) or 'native'. Nothing switches
--                  until an operator switches it, and the native path never calls Label Studio
--   task_answers   the answer of record, in the Label Studio result-array format HEALTH's
--                  parser already reads. Replaces the Label Studio annotation
--   answer_events  a transactional outbox: written in the same transaction as the answer,
--                  so an answer can never exist without the event that makes HEALTH score it
--   ls_task_map    every Label Studio task id an item has ever had, with where we learned it

begin;

-- ── Task numbers ────────────────────────────────────────────────────────────────
-- From one billion upward. The workforce columns that hold a task id are `integer`
-- (max 2,147,483,647), so the number has to fit there. Label Studio's own task ids are
-- per-instance counters (the GCP instance reached 18,204; the Azure one restarted at 1),
-- so a native number can only collide with a Label Studio id if an instance ever issues
-- a billion tasks. The CHECK below makes the boundary a constraint rather than a hope,
-- and the workforce app tells the two kinds of id apart by it.
create sequence if not exists task_no_seq
  as integer start with 1000000000 minvalue 1000000000 maxvalue 2147483647 no cycle;

-- Adding the column with a volatile default numbers every existing row, each once.
alter table project_items
  add column if not exists task_no integer default nextval('task_no_seq');
alter table project_items alter column task_no set default nextval('task_no_seq');
alter table project_items alter column task_no set not null;
create unique index if not exists project_items_task_no_key on project_items (task_no);
do $$ begin
  alter table project_items add constraint project_items_task_no_native
    check (task_no >= 1000000000);
exception when duplicate_object then null; end $$;

-- ── Which store a project uses ──────────────────────────────────────────────────
alter table project_submissions
  add column if not exists task_store text not null default 'label_studio';
do $$ begin
  alter table project_submissions add constraint project_submissions_task_store_check
    check (task_store in ('label_studio', 'native'));
exception when duplicate_object then null; end $$;

-- ── Answers of record ───────────────────────────────────────────────────────────
-- One row per published answer: a clinician's judgement, or an approved piece of
-- authored work. `result` is the Label Studio result array, byte for byte what the
-- workforce app's answersToLsResult produces, so HEALTH's _parse_result, consensus,
-- adjudication, second reading and scoring read it unchanged. `answers` is the flat map
-- the clinician submitted, kept so the result can always be re-derived and checked.
-- Rows are never deleted to undo a write; a withdrawal is recorded instead.
create table if not exists task_answers (
  id               bigint generated always as identity primary key,
  task_no          integer not null references project_items (task_no) on delete cascade,
  project_id       uuid not null references project_submissions (id) on delete cascade,
  pool_id          uuid references pools (id) on delete set null,
  clinician_id     uuid references clinicians (id) on delete set null,
  kind             text not null check (kind in ('judgement', 'approved_authoring')),
  result           jsonb not null check (jsonb_typeof(result) = 'array'),
  answers          jsonb not null check (jsonb_typeof(answers) = 'object'),
  review_item_id   uuid references review_items (id) on delete set null,
  created_at       timestamptz not null default now(),
  withdrawn_at     timestamptz,
  withdrawn_reason text
);
create index if not exists task_answers_task on task_answers (task_no) where withdrawn_at is null;
create index if not exists task_answers_project on task_answers (project_id);
-- One judgement per clinician per task, and one approved answer per authored task.
create unique index if not exists task_answers_one_judgement
  on task_answers (task_no, clinician_id) where kind = 'judgement' and withdrawn_at is null;
create unique index if not exists task_answers_one_approval
  on task_answers (task_no) where kind = 'approved_authoring' and withdrawn_at is null;
alter table task_answers enable row level security;

-- ── Outbox ──────────────────────────────────────────────────────────────────────
-- pending -> processing -> done, or back to pending on a failure until max attempts,
-- then failed (visible to operators, retried only on request). At most ONE event per task
-- is ever processing, enforced by the unique index, so two workers can never score the
-- same task at once and the later write can never be the staler one.
create table if not exists answer_events (
  id            bigint generated always as identity primary key,
  task_no       integer not null references project_items (task_no) on delete cascade,
  project_id    uuid not null references project_submissions (id) on delete cascade,
  answer_id     bigint references task_answers (id) on delete cascade,
  kind          text not null check (kind in ('answer_recorded', 'approval_recorded', 'rebuild')),
  status        text not null default 'pending'
                  check (status in ('pending', 'processing', 'done', 'failed')),
  attempts      integer not null default 0,
  last_error    text,
  created_at    timestamptz not null default now(),
  locked_at     timestamptz,
  processed_at  timestamptz
);
create unique index if not exists answer_events_once
  on answer_events (answer_id, kind) where answer_id is not null;
create unique index if not exists answer_events_one_processing_per_task
  on answer_events (task_no) where status = 'processing';
create index if not exists answer_events_open
  on answer_events (status, id) where status in ('pending', 'processing', 'failed');
create index if not exists answer_events_project on answer_events (project_id, status);
alter table answer_events enable row level security;

-- ── Label Studio ids an item has had ────────────────────────────────────────────
-- Label Studio ids are not stable: the October 2026 rebuild re-imported every task under
-- new ids. The item's own id (carried into Label Studio as _item_id) is the only stable
-- key, so history is joined through this table and never by a bare integer.
create table if not exists ls_task_map (
  id             bigint generated always as identity primary key,
  item_id        uuid not null references project_items (id) on delete cascade,
  ls_instance    text not null,         -- which Label Studio, e.g. 'gcp-2026' or 'azure-2026'
  ls_project_id  integer not null,
  ls_task_id     integer not null,
  evidence       text not null,         -- the export file (and its sha256) the mapping came from
  recorded_at    timestamptz not null default now(),
  unique (ls_instance, ls_task_id)
);
create index if not exists ls_task_map_item on ls_task_map (item_id);
alter table ls_task_map enable row level security;

-- ── Functions ───────────────────────────────────────────────────────────────────

-- Reviews required per task: eval_config.reviewers_per_item, as HEALTH reads it
-- (ls._reviewers_target): missing or unreadable means 1.
create or replace function native_ceiling(p_project uuid) returns integer
language sql stable as $$
  select greatest(1, coalesce(
    case when (s.eval_config ->> 'reviewers_per_item') ~ '^\s*[0-9]+\s*$'
         then (s.eval_config ->> 'reviewers_per_item')::integer end, 1))
  from project_submissions s where s.id = p_project
$$;

-- A task this pool may write to: the item exists, belongs to the pool's project, the
-- project is on the native store, and the clinician is eligible for the pool. Returns the
-- item's project id, or null. One rule, used by every write below.
create or replace function native_task_for_pool(p_task_no integer, p_pool_id uuid, p_clinician_id uuid)
returns uuid language sql stable as $$
  select i.project_id
    from project_items i
    join pools p on p.id = p_pool_id and p.project_id = i.project_id
    join project_submissions s on s.id = i.project_id and s.task_store = 'native'
   where i.task_no = p_task_no
     and exists (select 1 from pool_eligibility e
                  where e.pool_id = p_pool_id and e.clinician_id = p_clinician_id and e.eligible)
$$;

-- One judgement: answer, completion and event in ONE transaction, the reviewer ceiling
-- checked under a lock on the task's own row (so clinicians on different tasks never
-- wait on each other). Outcomes the caller maps to responses:
--   claimed      recorded
--   already      this clinician has already answered this task
--   full         the task already has the reviews the client commissioned
--   settled      a senior has already settled this task
--   closed       the pool is closed
--   unavailable  the task has not been released to clinicians
--   wrong_phase  the project wants authored work and a second reading, not a judgement
--   invalid      the result is not a non-empty array
--   not_found    no such task for this clinician and pool (never says which)
create or replace function native_submit_judgement(
  p_clinician_id uuid, p_pool_id uuid, p_task_no integer, p_result jsonb, p_answers jsonb
) returns text language plpgsql as $$
declare
  v_project uuid;
  v_status text;
  v_answer bigint;
begin
  if p_result is null or jsonb_typeof(p_result) <> 'array' or jsonb_array_length(p_result) = 0
     or p_answers is null or jsonb_typeof(p_answers) <> 'object' then
    return 'invalid';
  end if;

  v_project := native_task_for_pool(p_task_no, p_pool_id, p_clinician_id);
  if v_project is null then return 'not_found'; end if;

  select status into v_status from project_items where task_no = p_task_no for update;

  if (select closed_at from pools where id = p_pool_id) is not null then return 'closed'; end if;
  if v_status = 'pending' then return 'unavailable'; end if;
  if coalesce((select (eval_config ->> 'review_required')::boolean
                 from project_submissions where id = v_project), false) then
    return 'wrong_phase';
  end if;
  if exists (select 1 from task_completions
              where ls_task_id = p_task_no and kind = 'adjudication') then
    return 'settled';
  end if;
  if exists (select 1 from task_answers
              where task_no = p_task_no and clinician_id = p_clinician_id
                and kind = 'judgement' and withdrawn_at is null) then
    return 'already';
  end if;
  if (select count(*) from task_answers where task_no = p_task_no and withdrawn_at is null)
     >= native_ceiling(v_project) then
    return 'full';
  end if;

  insert into task_answers (task_no, project_id, pool_id, clinician_id, kind, result, answers)
  values (p_task_no, v_project, p_pool_id, p_clinician_id, 'judgement', p_result, p_answers)
  returning id into v_answer;

  insert into task_completions (clinician_id, pool_id, ls_task_id, annotation_data, kind)
  values (p_clinician_id, p_pool_id, p_task_no, p_result, 'judgement');

  insert into answer_events (task_no, project_id, answer_id, kind)
  values (p_task_no, v_project, v_answer, 'answer_recorded');

  return 'claimed';
exception when unique_violation then
  -- The whole block is undone, answer and completion together.
  return 'already';
end $$;

-- Approving authored work publishes it: the approved answer, the review row's new state,
-- the reviewer's pass and the event, in ONE transaction. The author can never approve
-- their own work; that is checked here as well as in the app. Outcomes:
--   approved | conflict (not waiting for review) | own_work | closed | invalid | not_found
create or replace function native_approve_review(
  p_review_item_id uuid, p_reviewer_id uuid, p_action text, p_final jsonb, p_result jsonb
) returns text language plpgsql as $$
declare
  v_ri review_items%rowtype;
  v_project uuid;
  v_answer bigint;
begin
  if p_action not in ('approved', 'edited')
     or p_result is null or jsonb_typeof(p_result) <> 'array' or jsonb_array_length(p_result) = 0
     or p_final is null or jsonb_typeof(p_final) <> 'object' then
    return 'invalid';
  end if;

  select * into v_ri from review_items where id = p_review_item_id for update;
  if not found then return 'not_found'; end if;

  v_project := native_task_for_pool(v_ri.ls_task_id, v_ri.pool_id, p_reviewer_id);
  if v_project is null then return 'not_found'; end if;
  if v_ri.state <> 'needs_review' then return 'conflict'; end if;
  if v_ri.author_id = p_reviewer_id then return 'own_work'; end if;
  if (select closed_at from pools where id = v_ri.pool_id) is not null then return 'closed'; end if;

  perform 1 from project_items where task_no = v_ri.ls_task_id for update;

  -- The answer is the author's work; the reviewer's approval is recorded on review_items.
  insert into task_answers (task_no, project_id, pool_id, clinician_id, kind, result, answers,
                            review_item_id)
  values (v_ri.ls_task_id, v_project, v_ri.pool_id, v_ri.author_id, 'approved_authoring',
          p_result, p_final, v_ri.id)
  returning id into v_answer;

  update review_items
     set state = 'approved', reviewer_id = p_reviewer_id, reviewed_at = now(),
         review_action = p_action, final_data = p_final,
         ls_annotation_id = v_answer::integer, updated_at = now()
   where id = v_ri.id;

  insert into task_completions (clinician_id, pool_id, ls_task_id, annotation_data, kind)
  values (p_reviewer_id, v_ri.pool_id, v_ri.ls_task_id, p_result, 'review')
  on conflict do nothing;

  insert into answer_events (task_no, project_id, answer_id, kind)
  values (v_ri.ls_task_id, v_project, v_answer, 'approval_recorded');

  return 'approved';
exception when unique_violation then
  return 'conflict';
end $$;

-- What serving needs per released task, in the shape the workforce app's availability
-- rule already takes from Label Studio: {id, is_labeled, total_annotations}.
create or replace function native_task_states(p_project uuid)
returns table (task_no integer, total_annotations integer, is_labeled boolean)
language sql stable as $$
  select i.task_no,
         count(a.id)::integer,
         count(a.id) >= native_ceiling(i.project_id)
    from project_items i
    left join task_answers a on a.task_no = i.task_no and a.withdrawn_at is null
   where i.project_id = p_project and i.status <> 'pending'
   group by i.task_no, i.project_id
   order by i.task_no
$$;

-- Claim up to p_limit tasks' worth of events: the oldest pending event of each task, at
-- most one task per claim. A processing event older than p_stale_seconds is treated as
-- abandoned (its worker died) and returned to pending first, which is the restart
-- recovery. `upto_event_id` is the newest event for the task at claim time: everything up
-- to it is covered by this processing, because an answer and its event commit together.
create or replace function claim_answer_events(p_limit integer default 20, p_stale_seconds integer default 300)
returns table (event_id bigint, task_no integer, project_id uuid, upto_event_id bigint, attempts integer)
language plpgsql as $$
declare
  r record;
begin
  update answer_events e
     set status = 'pending', locked_at = null,
         last_error = coalesce(e.last_error, 'abandoned while processing')
   where e.status = 'processing'
     and e.locked_at < now() - make_interval(secs => p_stale_seconds);

  for r in
    select x.id, x.task_no, x.project_id
      from (select distinct on (e.task_no) e.id, e.task_no, e.project_id
              from answer_events e
             where e.status = 'pending'
             order by e.task_no, e.id) x
     order by x.id
     limit p_limit
  loop
    begin
      update answer_events e
         set status = 'processing', locked_at = now(), attempts = e.attempts + 1
       where e.id = r.id and e.status = 'pending';
      if found then
        event_id := r.id;
        task_no := r.task_no;
        project_id := r.project_id;
        select max(a.id) into upto_event_id from answer_events a where a.task_no = r.task_no;
        select e.attempts into attempts from answer_events e where e.id = r.id;
        return next;
      end if;
    exception when unique_violation then
      null;   -- another worker is processing this task; leave it to them
    end;
  end loop;
end $$;

-- Finish a claimed event. On success every pending or failed event for the same task up
-- to p_upto_event_id is covered too, because processing re-scores the task from ALL of
-- its answers: an earlier failure is resolved by a later success, not left on display.
-- On failure the event goes back to pending, or to failed once it has used p_max_attempts.
create or replace function finish_answer_event(
  p_event_id bigint, p_ok boolean, p_upto_event_id bigint default null,
  p_error text default null, p_max_attempts integer default 5
) returns void language plpgsql as $$
declare
  v_task integer;
begin
  select task_no into v_task from answer_events where id = p_event_id;
  if p_ok then
    update answer_events
       set status = 'done', processed_at = now(), locked_at = null, last_error = null
     where id = p_event_id;
    update answer_events
       set status = 'done', processed_at = now(),
           last_error = 'covered by event ' || p_event_id
     where task_no = v_task and status in ('pending', 'failed')
       and id <= coalesce(p_upto_event_id, p_event_id);
  else
    update answer_events
       set status = case when attempts >= p_max_attempts then 'failed' else 'pending' end,
           locked_at = null, last_error = left(coalesce(p_error, 'failed'), 500)
     where id = p_event_id;
  end if;
end $$;

-- Re-score every released task in a project from its stored answers (operator recovery,
-- and the replacement for POST /ls/pull). Returns how many events were queued.
create or replace function request_rebuild(p_project uuid) returns integer
language plpgsql as $$
declare
  n integer;
begin
  insert into answer_events (task_no, project_id, kind)
  select i.task_no, i.project_id, 'rebuild'
    from project_items i
   where i.project_id = p_project and i.status <> 'pending';
  get diagnostics n = row_count;
  return n;
end $$;

-- Put failed events back in the queue for another round of attempts.
create or replace function retry_failed_events(p_project uuid) returns integer
language plpgsql as $$
declare
  n integer;
begin
  update answer_events set status = 'pending', attempts = 0
   where project_id = p_project and status = 'failed';
  get diagnostics n = row_count;
  return n;
end $$;

-- Service key only. PostgREST would otherwise expose these to the anon key.
revoke all on function native_ceiling(uuid) from public, anon, authenticated;
revoke all on function native_task_for_pool(integer, uuid, uuid) from public, anon, authenticated;
revoke all on function native_submit_judgement(uuid, uuid, integer, jsonb, jsonb) from public, anon, authenticated;
revoke all on function native_approve_review(uuid, uuid, text, jsonb, jsonb) from public, anon, authenticated;
revoke all on function native_task_states(uuid) from public, anon, authenticated;
revoke all on function claim_answer_events(integer, integer) from public, anon, authenticated;
revoke all on function finish_answer_event(bigint, boolean, bigint, text, integer) from public, anon, authenticated;
revoke all on function request_rebuild(uuid) from public, anon, authenticated;
revoke all on function retry_failed_events(uuid) from public, anon, authenticated;

commit;
