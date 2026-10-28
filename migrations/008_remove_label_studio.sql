-- Migration 008: the task store is the only store; remove HEALTH's Label Studio schema
-- Paste into the Supabase SQL editor. One transaction; safe to re-run.
--
-- Apply it only after:
--   1. scripts/sql/delete_label_studio_test_data.sql has removed the Label Studio work
--      (this migration refuses while any remains), and
--   2. the HEALTH release without Label Studio is deployed: the code before it reads
--      project_submissions.ls_project_id, which this drops.
--
-- What it does:
--   * every project is on the task store, and the database now says so: task_store
--     defaults to 'native' and may only be 'native' (the constraint keeps 005's name, so
--     re-running 005 leaves it alone);
--   * drops ls_task_map, the Label Studio task-id history (005), now empty;
--   * drops project_submissions.ls_project_id, which only Label Studio work used.
--
-- What it leaves, on purpose:
--   * the task_store column: the workforce app still reads it. Drop it once that app no
--     longer does;
--   * the workforce app's own columns (pools.ls_project_id, and ls_task_id /
--     ls_annotation_id on task_completions, review_items and task_flags). They now hold
--     task numbers and answer ids, under names kept from Label Studio; they belong to
--     that repo's migrations.
-- Migrations 001 to 007 are history and are not edited.

begin;

do $$
declare v_n int;
begin
  select (select count(*) from project_submissions where task_store <> 'native')
       + (select count(*) from pools where project_id is null) into v_n;
  if v_n <> 0 then
    raise exception 'migration 008: % Label Studio projects or pools remain; run scripts/sql/delete_label_studio_test_data.sql first', v_n;
  end if;
end $$;

alter table project_submissions alter column task_store set default 'native';
alter table project_submissions drop constraint if exists project_submissions_task_store_check;
alter table project_submissions add constraint project_submissions_task_store_check
  check (task_store = 'native');

drop table if exists ls_task_map;

alter table project_submissions drop column if exists ls_project_id;

commit;
