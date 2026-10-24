-- Migration 006: items are versioned, never silently replaced
-- Paste into the Supabase SQL editor (Dashboard -> SQL Editor -> New query -> Run).
-- Safe to re-run. Apply after 005.
--
-- Re-uploading an item used to delete it and insert a new one. On the native store that
-- would have deleted every clinician answer recorded against it. Now an upload is one of:
--
--   created      no item at that index yet
--   unchanged    the same content again (a retry, a double click): nothing is written
--   updated      different content, but nobody has started on the item: changed in place
--   superseded   different content, and work exists: the old item is kept, with its
--                answers and history, marked superseded and linked from the new version
--
-- One function decides and writes, under a lock on (project, index), so concurrent uploads
-- of the same row cannot both win. Everything that counts or delivers items ignores
-- superseded ones; the admin export keeps them, so the history is never out of reach.
--
-- Answers can no longer be deleted along with an item: deleting a worked item is refused.
-- Deleting a whole project still removes everything of it, deliberately and together.

begin;

alter table project_items add column if not exists content_hash text;
alter table project_items add column if not exists version integer not null default 1;
alter table project_items add column if not exists supersedes uuid
  references project_items (id) on delete set null;
alter table project_items add column if not exists superseded_at timestamptz;

-- One current item per index. A failure here means the project already has two current
-- items at one index (concurrent ingests before this constraint existed): resolve them
-- first. The preflight check (scripts/schema_check.py) reports them.
create unique index if not exists project_items_one_current_per_idx
  on project_items (project_id, idx) where status <> 'superseded';

-- Answers and their events outlive an item's deletion attempt. NO ACTION rather than
-- RESTRICT so that deleting a project, which reaches the answers through their own
-- project_id cascade in the same statement, still works.
do $$
declare c record;
begin
  for c in
    select con.conname, rel.relname
      from pg_constraint con
      join pg_class rel on rel.oid = con.conrelid
      join pg_class ref on ref.oid = con.confrelid
     where con.contype = 'f' and ref.relname = 'project_items'
       and rel.relname in ('task_answers', 'answer_events')
       and con.confdeltype = 'c'
  loop
    execute format('alter table %I drop constraint %I', c.relname, c.conname);
    execute format('alter table %I add constraint %I foreign key (task_no) references project_items (task_no)',
                   c.relname, c.conname);
  end loop;
end $$;

-- The scoring path's own answer store must never be fed a superseded item.
create or replace function native_task_states(p_project uuid)
returns table (task_no integer, total_annotations integer, is_labeled boolean)
language sql stable as $$
  select i.task_no,
         count(a.id)::integer,
         count(a.id) >= native_ceiling(i.project_id)
    from project_items i
    left join task_answers a on a.task_no = i.task_no and a.withdrawn_at is null
   where i.project_id = p_project and i.status not in ('pending', 'superseded')
   group by i.task_no, i.project_id
   order by i.task_no
$$;

-- Record one item's content at an index, as described at the top of this file.
create or replace function ingest_item_version(
  p_project uuid, p_idx integer, p_content jsonb, p_content_hash text
) returns table (outcome text, item_id uuid, task_no integer, previous_item_id uuid)
language plpgsql as $$
declare
  v_cur project_items%rowtype;
  v_new project_items%rowtype;
  v_worked boolean;
begin
  if p_content is null or jsonb_typeof(p_content) <> 'object' or coalesce(p_content_hash, '') = '' then
    raise exception 'ingest_item_version: content must be an object and its hash given';
  end if;
  -- Serialise every writer of this (project, index), including one that finds no row yet.
  perform pg_advisory_xact_lock(hashtext(p_project::text), p_idx);

  select * into v_cur from project_items i
   where i.project_id = p_project and i.idx = p_idx and i.status <> 'superseded'
   for update;

  if not found then
    insert into project_items (project_id, idx, content, content_hash)
    values (p_project, p_idx, p_content, p_content_hash) returning * into v_new;
    return query select 'created'::text, v_new.id, v_new.task_no, null::uuid;
    return;
  end if;

  if v_cur.content_hash = p_content_hash then
    return query select 'unchanged'::text, v_cur.id, v_cur.task_no, null::uuid;
    return;
  end if;

  -- Has anyone started on it? Any of these is work that must not be overwritten.
  v_worked := v_cur.status <> 'pending'
    or exists (select 1 from task_answers a where a.task_no = v_cur.task_no)
    or exists (select 1 from task_completions c where c.ls_task_id = v_cur.task_no)
    or exists (select 1 from task_flags f where f.ls_task_id = v_cur.task_no)
    or exists (select 1 from review_items r where r.ls_task_id = v_cur.task_no)
    or v_cur.label is not null;

  if not v_worked then
    update project_items i set content = p_content, content_hash = p_content_hash
     where i.id = v_cur.id;
    return query select 'updated'::text, v_cur.id, v_cur.task_no, null::uuid;
    return;
  end if;

  update project_items i set status = 'superseded', superseded_at = now() where i.id = v_cur.id;
  insert into project_items (project_id, idx, content, content_hash, version, supersedes)
  values (p_project, p_idx, p_content, p_content_hash, v_cur.version + 1, v_cur.id)
  returning * into v_new;
  return query select 'superseded'::text, v_new.id, v_new.task_no, v_cur.id;
end $$;

-- A superseded item takes no more answers: the content they would judge has been replaced.
create or replace function native_task_for_pool(p_task_no integer, p_pool_id uuid, p_clinician_id uuid)
returns uuid language sql stable as $$
  select i.project_id
    from project_items i
    join pools p on p.id = p_pool_id and p.project_id = i.project_id
    join project_submissions s on s.id = i.project_id and s.task_store = 'native'
   where i.task_no = p_task_no
     and i.status <> 'superseded'
     and exists (select 1 from pool_eligibility e
                  where e.pool_id = p_pool_id and e.clinician_id = p_clinician_id and e.eligible)
$$;

revoke all on function native_task_states(uuid) from public, anon, authenticated;
revoke all on function native_task_for_pool(integer, uuid, uuid) from public, anon, authenticated;
revoke all on function ingest_item_version(uuid, integer, jsonb, text) from public, anon, authenticated;

commit;
