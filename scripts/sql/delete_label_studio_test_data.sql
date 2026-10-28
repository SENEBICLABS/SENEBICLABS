-- Delete the internal Label Studio test work: every project still marked
-- task_store = 'label_studio' and every Label Studio pool (a pool with no project_id).
-- [approval: production write, after a verified backup and a READY from
--  scripts/label_studio_precheck.py]
--
-- No client project has ever run on this platform. Every Label Studio project is internal
-- test work, whatever its stage says: 'delivered' and 'production' are test workflow
-- states, and those projects are deleted like the rest. Deleting all of it leaves the
-- database with no Label Studio work, which migration 008 then enforces.
--
-- What goes, through the existing foreign keys (reviewed: every key into a table this
-- deletes rows from is listed in the guard below):
--   a project: its items, project_clinicians, audit_events, task_answers, answer_events
--              and ls_task_map rows. Its clinical_failures rows are deleted here too
--              (their foreign key would otherwise keep them with project_id set null);
--   a pool:    its pool_eligibility, calibration_attempts, task_completions, task_flags
--              and review_items.
-- Nothing else is touched: no clinician, operator, API key, native project or native pool.
-- Item images stay in storage: the audit rows record the keys only these items used, for
-- a separate cleanup through the Storage API.
--
-- What stays: one audit_events row per deleted project and pool, with project_id null so
-- the deletion does not remove it, recording what was deleted and how much went with it.
-- No clinical content and no clinician identity is copied into it.
--
-- Guards: it raises, and so changes nothing, unless
--   * it finds exactly the work it was reviewed against (EXPECT_PROJECTS, EXPECT_POOLS);
--   * the foreign keys into every table it deletes from are exactly the reviewed ones, and
--     no user trigger or rewrite rule there could do anything else;
--   * none of that work holds a native answer or event, and nothing outside it points into
--     it: no pool with a project, no other project's item or audit row, no answer through a
--     review row, no other pool's work by task number;
--   * afterwards nothing of Label Studio remains, and every row of every other table, and
--     every row outside this work in the tables it deletes from, is byte for byte what it
--     was (pg_temp.ls_removal_kept, computed the same way before and after).
-- If a guard fails, stop and look: do not edit the numbers to make it pass.
--
-- Run it in the Supabase SQL editor as one script. Undo is a restore from the backup taken
-- before it (docs/operations.md#backups). It runs in one repeatable-read snapshot: a
-- concurrent change to a row it locks makes it fail and change nothing; run it again.

begin isolation level repeatable read;

-- Everything the deletion must leave alone, as one value: for every table in public, the
-- count and an md5 of its rows, restricted in the tables this deletes from to rows outside
-- the Label Studio work. Only this session can see it, and it goes when the session ends.
create function pg_temp.ls_removal_kept(p_projects uuid[], p_pools uuid[], p_items uuid[], p_actor text)
returns jsonb language plpgsql as $f$
declare
  v_t text;
  v_where text;
  v_n bigint;
  v_md5 text;
  v_kept jsonb := '{}';
begin
  for v_t in select tablename from pg_tables where schemaname = 'public' order by 1 loop
    case v_t
      when 'project_submissions' then v_where := 'not id = any($1)';
      when 'project_items', 'project_clinicians' then v_where := 'not project_id = any($1)';
      when 'clinical_failures' then v_where := 'project_id is null or not project_id = any($1)';
      when 'audit_events' then
        v_where := '(project_id is null or not project_id = any($1)) and not (actor_name is not distinct from $4'
                || ' and action in (''label_studio_project_deleted'', ''label_studio_pool_deleted''))';
      when 'ls_task_map' then v_where := 'not item_id = any($3)';
      when 'pools' then v_where := 'not id = any($2)';
      when 'pool_eligibility', 'calibration_attempts', 'task_completions', 'task_flags', 'review_items' then
        v_where := 'not pool_id = any($2)';
      else v_where := 'true';
    end case;
    execute format('select count(*), md5(coalesce(string_agg(r, E''\n'' order by r), '''')) from'
                   ' (select to_jsonb(t)::text as r from public.%I t where %s) x', v_t, v_where)
       into v_n, v_md5 using p_projects, p_pools, p_items, p_actor;
    v_kept := v_kept || jsonb_build_object(v_t, jsonb_build_array(v_n, v_md5));
  end loop;
  return v_kept;
end $f$;

do $$
declare
  EXPECT_PROJECTS constant int := 4;
  EXPECT_POOLS    constant int := 5;
  ACTOR           constant text := 'Label Studio removal (reviewed SQL)';
  v_projects uuid[];
  v_pools    uuid[];
  v_items    uuid[];
  v_task_nos int[];
  v_kept     jsonb;
  v_n        int;
  v_what     text;
begin
  -- Every foreign key into a table this deletes from, as reviewed. One more, one fewer,
  -- or a different ON DELETE, and the effect of the deletes below is no longer known.
  select string_agg(format('%s.%s -> %s (%s)', tbl, col, ref, del), ', ') into v_what
    from ((select ref.relname::text as ref, rel.relname::text as tbl, a.attname::text as col,
                  con.confdeltype::text as del
             from pg_constraint con
             join pg_class rel on rel.oid = con.conrelid
             join pg_class ref on ref.oid = con.confrelid
             join pg_namespace n on n.oid = ref.relnamespace and n.nspname = 'public'
             join pg_attribute a on a.attrelid = con.conrelid and a.attnum = con.conkey[1]
            where con.contype = 'f'
              and ref.relname in ('project_submissions', 'project_items', 'project_clinicians',
                                  'audit_events', 'clinical_failures', 'ls_task_map',
                                  'task_answers', 'answer_events', 'pools', 'pool_eligibility',
                                  'calibration_attempts', 'task_completions', 'task_flags',
                                  'review_items'))
          except
          (values ('project_submissions', 'project_items',        'project_id',     'c'),
                  ('project_submissions', 'project_clinicians',   'project_id',     'c'),
                  ('project_submissions', 'audit_events',         'project_id',     'c'),
                  ('project_submissions', 'task_answers',         'project_id',     'c'),
                  ('project_submissions', 'answer_events',        'project_id',     'c'),
                  ('project_submissions', 'clinical_failures',    'project_id',     'n'),
                  ('project_submissions', 'pools',                'project_id',     'r'),
                  ('project_items',       'project_items',        'supersedes',     'n'),
                  ('project_items',       'audit_events',         'item_id',        'c'),
                  ('project_items',       'ls_task_map',          'item_id',        'c'),
                  ('project_items',       'task_answers',         'task_no',        'a'),
                  ('project_items',       'answer_events',        'task_no',        'a'),
                  ('pools',               'pool_eligibility',     'pool_id',        'c'),
                  ('pools',               'calibration_attempts', 'pool_id',        'c'),
                  ('pools',               'task_completions',     'pool_id',        'c'),
                  ('pools',               'task_flags',           'pool_id',        'c'),
                  ('pools',               'review_items',         'pool_id',        'c'),
                  ('pools',               'task_answers',         'pool_id',        'n'),
                  ('review_items',        'task_answers',         'review_item_id', 'n'),
                  ('task_answers',        'answer_events',        'answer_id',      'c'))) x;
  if v_what is not null then
    raise exception 'unreviewed foreign keys: %: stop and review', v_what;
  end if;
  select count(*) into v_n
    from pg_constraint con join pg_class ref on ref.oid = con.confrelid
    join pg_namespace n on n.oid = ref.relnamespace and n.nspname = 'public'
   where con.contype = 'f'
     and ref.relname in ('project_submissions', 'project_items', 'pools', 'review_items', 'task_answers');
  if v_n <> 20 then
    raise exception 'expected the 20 reviewed foreign keys, found %: stop and review', v_n;
  end if;
  select string_agg(format('%s on %s', t.tgname, c.relname), ', ') into v_what
    from pg_trigger t join pg_class c on c.oid = t.tgrelid
    join pg_namespace n on n.oid = c.relnamespace and n.nspname = 'public'
   where not t.tgisinternal and (t.tgtype & 8) <> 0
     and c.relname in ('project_submissions', 'project_items', 'project_clinicians',
                       'audit_events', 'clinical_failures', 'ls_task_map', 'task_answers',
                       'answer_events', 'pools', 'pool_eligibility', 'calibration_attempts',
                       'task_completions', 'task_flags', 'review_items');
  if v_what is not null then
    raise exception 'delete triggers would run: %: stop and review', v_what;
  end if;
  select string_agg(format('%s on %s', rulename, tablename), ', ') into v_what
    from pg_rules
   where schemaname = 'public'
     and tablename in ('project_submissions', 'project_items', 'project_clinicians',
                       'audit_events', 'clinical_failures', 'ls_task_map', 'task_answers',
                       'answer_events', 'pools', 'pool_eligibility', 'calibration_attempts',
                       'task_completions', 'task_flags', 'review_items');
  if v_what is not null then
    raise exception 'rewrite rules would run: %: stop and review', v_what;
  end if;

  -- Lock everything this will delete, so nothing changes between check and write.
  select coalesce(array_agg(s.id order by s.id), '{}') into v_projects
    from (select id from project_submissions where task_store <> 'native' for update) s;
  select coalesce(array_agg(p.id order by p.id), '{}') into v_pools
    from (select id from pools where project_id is null for update) p;
  select coalesce(array_agg(i.id), '{}'), coalesce(array_agg(i.task_no), '{}')
    into v_items, v_task_nos
    from (select id, task_no from project_items where project_id = any(v_projects) for update) i;

  if cardinality(v_projects) <> EXPECT_PROJECTS then
    raise exception 'expected % Label Studio projects, found %: stop and review',
      EXPECT_PROJECTS, cardinality(v_projects);
  end if;
  if cardinality(v_pools) <> EXPECT_POOLS then
    raise exception 'expected % Label Studio pools, found %: stop and review',
      EXPECT_POOLS, cardinality(v_pools);
  end if;

  select count(*) into v_n from task_answers
   where pool_id = any(v_pools) or project_id = any(v_projects) or task_no = any(v_task_nos)
      or review_item_id in (select id from review_items where pool_id = any(v_pools));
  if v_n <> 0 then
    raise exception 'the Label Studio work holds % native answers: stop and review', v_n;
  end if;
  select count(*) into v_n from answer_events
   where project_id = any(v_projects) or task_no = any(v_task_nos);
  if v_n <> 0 then
    raise exception 'the Label Studio work holds % answer events: stop and review', v_n;
  end if;
  select count(*) into v_n from pools where project_id = any(v_projects);
  if v_n <> 0 then
    raise exception '% native pools point at a Label Studio project: stop and review', v_n;
  end if;
  select count(*) into v_n from project_items
   where supersedes = any(v_items) and not project_id = any(v_projects);
  if v_n <> 0 then
    raise exception '% items of other projects supersede Label Studio items: stop and review', v_n;
  end if;
  select count(*) into v_n from audit_events
   where item_id = any(v_items) and (project_id is null or not project_id = any(v_projects));
  if v_n <> 0 then
    raise exception '% audit rows of other projects point at Label Studio items: stop and review', v_n;
  end if;
  -- Task numbers have no foreign key in the workforce tables: check them by value.
  select (select count(*) from task_completions where not pool_id = any(v_pools) and ls_task_id = any(v_task_nos))
       + (select count(*) from task_flags       where not pool_id = any(v_pools) and ls_task_id = any(v_task_nos))
       + (select count(*) from review_items     where not pool_id = any(v_pools) and ls_task_id = any(v_task_nos))
    into v_n;
  if v_n <> 0 then
    raise exception '% rows in other pools record a Label Studio item''s task number: stop and review', v_n;
  end if;

  v_kept := pg_temp.ls_removal_kept(v_projects, v_pools, v_items, ACTOR);

  insert into audit_events (item_id, project_id, action, actor_id, actor_name, source, value)
  select null, null, 'label_studio_project_deleted', 'operator', ACTOR, 'admin',
         jsonb_build_object(
           'project_id', s.id, 'name', s.name, 'stage', s.stage, 'status', s.status,
           'ls_project_id', s.ls_project_id,
           'items', (select count(*) from project_items i where i.project_id = s.id),
           'items_by_status', (select coalesce(jsonb_object_agg(status, n), '{}') from
                                (select status, count(*) as n from project_items i
                                  where i.project_id = s.id group by status) x),
           'project_clinicians', (select count(*) from project_clinicians c where c.project_id = s.id),
           'audit_events', (select count(*) from audit_events e where e.project_id = s.id),
           'clinical_failures', (select count(*) from clinical_failures f where f.project_id = s.id),
           -- Storage keys only this project's items use: what the storage cleanup removes.
           'orphaned_image_keys', (select coalesce(jsonb_agg(distinct i.content->>'_image_key'), '[]')
                                     from project_items i
                                    where i.project_id = s.id and i.content ? '_image_key'
                                      and not exists (select 1 from project_items o
                                                       where not o.project_id = any(v_projects)
                                                         and o.content->>'_image_key' = i.content->>'_image_key')),
           'reason', 'Internal Label Studio test work, removed with Label Studio')
    from project_submissions s where s.id = any(v_projects);

  insert into audit_events (item_id, project_id, action, actor_id, actor_name, source, value)
  select null, null, 'label_studio_pool_deleted', 'operator', ACTOR, 'admin',
         jsonb_build_object(
           'pool_id', p.id, 'name', p.name, 'ls_project_id', p.ls_project_id,
           'closed_at', p.closed_at,
           'task_completions', (select count(*) from task_completions c where c.pool_id = p.id),
           'review_items', (select count(*) from review_items r where r.pool_id = p.id),
           'task_flags', (select count(*) from task_flags f where f.pool_id = p.id),
           'eligible_clinicians', (select count(*) from pool_eligibility e where e.pool_id = p.id),
           'calibration_attempts', (select count(*) from calibration_attempts a where a.pool_id = p.id),
           'reason', 'Label Studio pool, removed with Label Studio')
    from pools p where p.id = any(v_pools);

  delete from pools where id = any(v_pools);
  delete from clinical_failures where project_id = any(v_projects);
  delete from project_submissions where id = any(v_projects);

  -- Nothing of Label Studio may remain, and nothing else may have changed.
  select (select count(*) from project_submissions where task_store <> 'native')
       + (select count(*) from pools where project_id is null) into v_n;
  if v_n <> 0 then
    raise exception '% Label Studio rows remain: nothing was changed', v_n;
  end if;
  if pg_temp.ls_removal_kept(v_projects, v_pools, v_items, ACTOR) <> v_kept then
    raise exception 'data outside the Label Studio work changed: nothing was changed';
  end if;

  raise notice 'deleted % Label Studio projects and % pools', cardinality(v_projects), cardinality(v_pools);
end $$;

drop function pg_temp.ls_removal_kept(uuid[], uuid[], uuid[], text);

commit;
