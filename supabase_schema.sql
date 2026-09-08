-- Senebiclabs — Supabase schema
-- Run this in your Supabase project: SQL Editor → New query → paste → Run
-- Re-runnable: all statements use IF NOT EXISTS / OR REPLACE / DO NOTHING.

-- ── Expert applications ────────────────────────────────────────────────────────

create table if not exists expert_applications (
  id               uuid primary key default gen_random_uuid(),
  name             text not null,
  email            text not null,
  specialty        text not null,
  institution      text not null,
  country          text not null,
  license_number   text not null,
  years_experience text not null,
  note             text,
  status           text not null default 'pending', -- pending | approved | rejected
  lat              double precision,
  lng              double precision,
  created_at       timestamptz not null default now()
);

create index if not exists expert_applications_email_idx    on expert_applications (email);
create index if not exists expert_applications_status_idx   on expert_applications (status);
create index if not exists expert_applications_specialty_idx on expert_applications (specialty);

-- RLS: service key (backend) bypasses all policies.
-- Anon / authenticated keys have no access — data is admin-only.
alter table expert_applications enable row level security;

-- No public insert or select; all access goes through the FastAPI service key.
-- If you add an admin Supabase user later, create a policy scoped to their role here.


-- ── Waitlist ───────────────────────────────────────────────────────────────────

create table if not exists waitlist (
  id            uuid primary key default gen_random_uuid(),
  email         text not null,
  type          text not null check (type in ('patient', 'contributor', 'researcher', 'expert')),
  lat           double precision,
  lng           double precision,
  location_text text,
  created_at    timestamptz not null default now(),
  unique (email, type)
);

create index if not exists waitlist_type_idx  on waitlist (type);
create index if not exists waitlist_email_idx on waitlist (email);

-- RLS: same as above — service key only.
alter table waitlist enable row level security;


-- ── Project submissions (data annotation intake) ──────────────────────────────

create table if not exists project_submissions (
  id               uuid primary key default gen_random_uuid(),
  name             text not null,
  email            text not null,
  company          text not null,
  description      text not null,
  data_type        text,
  task_type        text,
  volume           text,
  timeline         text,
  data_sensitivity text,
  sample_link      text,
  budget_notes     text,
  status           text not null default 'new', -- new | scoping | active | delivered | closed
  created_at       timestamptz not null default now()
);

create index if not exists project_submissions_email_idx  on project_submissions (email);
create index if not exists project_submissions_status_idx on project_submissions (status);

-- Customer-portal pipeline stage (added after initial launch; safe to re-run).
-- submitted | scoping | agreement | pilot | production | delivered
alter table project_submissions add column if not exists stage      text not null default 'submitted';
alter table project_submissions add column if not exists stage_note  text;
alter table project_submissions add column if not exists updated_at  timestamptz not null default now();

-- Label Studio link + per-project pay/difficulty (added later; safe to re-run).
alter table project_submissions add column if not exists ls_project_id integer;
alter table project_submissions add column if not exists rate_per_item numeric;
alter table project_submissions add column if not exists difficulty    text;

-- Per-project eval config (Slice #1): label schema + fields + input mapping, so a new
-- eval client is onboarded by config, not code. See README "Onboard a new eval client".
alter table project_submissions add column if not exists eval_config jsonb;

-- RLS: service key only, same as the other tables.
alter table project_submissions enable row level security;


-- ── Project items (the units of work labeled on the platform) ─────────────────

create table if not exists project_items (
  id          uuid primary key default gen_random_uuid(),
  project_id  uuid not null references project_submissions(id) on delete cascade,
  idx         int  not null default 0,
  content     jsonb not null default '{}'::jsonb,   -- e.g. {"prompt": "...", "output": "..."}
  status      text not null default 'pending',      -- pending | done
  label       jsonb,                                -- e.g. {"score": 4, "unsafe": false, "rationale": "..."}
  labeled_by  text,
  labeled_at  timestamptz,
  created_at  timestamptz not null default now()
);

create index if not exists project_items_project_idx on project_items (project_id);
create index if not exists project_items_status_idx  on project_items (project_id, status);

-- Multi-clinician claiming + attribution (status: pending | in_progress | done)
alter table project_items add column if not exists assigned_to text;
alter table project_items add column if not exists claimed_at  timestamptz;

alter table project_items enable row level security;


-- ── Clinicians (the people who label) ─────────────────────────────────────────

create table if not exists clinicians (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  email       text,
  access_code text not null unique,
  active      boolean not null default true,
  created_at  timestamptz not null default now()
);

create index if not exists clinicians_code_idx on clinicians (access_code);

alter table clinicians enable row level security;

-- Slice #2 isolation: which clinicians are assigned to which project. Project creation
-- alone does NOT grant access — a clinician must be assigned here to see or label a
-- project. Enforced by _labeler_can_access() in the work endpoints.
create table if not exists project_clinicians (
  project_id   uuid not null references project_submissions(id) on delete cascade,
  clinician_id uuid not null references clinicians(id)          on delete cascade,
  assigned_at  timestamptz not null default now(),
  primary key (project_id, clinician_id)
);
alter table project_clinicians enable row level security;

-- Slice #7 audit trail: an append-only log of every annotation decision. The item's
-- current label lives on project_items; the immutable history lives here. Answers
-- "who labeled/reviewed this item, with what value, through which channel, and when".
create table if not exists audit_events (
  id          uuid primary key default gen_random_uuid(),
  item_id     uuid references project_items(id)       on delete cascade,
  project_id  uuid references project_submissions(id) on delete cascade,
  action      text not null,                    -- 'label' | 'review' | 'skip'
  actor_id    text,                             -- clinician id, 'admin', or LS email
  actor_name  text,
  source      text not null default 'app',      -- 'app' | 'label_studio'
  value       jsonb,                            -- snapshot of the label at this event
  created_at  timestamptz not null default now()
);
create index if not exists audit_events_item_idx    on audit_events (item_id, created_at);
create index if not exists audit_events_project_idx on audit_events (project_id, created_at desc);
alter table audit_events enable row level security;


-- ── Atomic task claim (the concurrency-safe work queue) ───────────────────────
-- Reclaims stale claims, returns the labeler's held item if any, else claims the
-- next pending item using FOR UPDATE SKIP LOCKED so two labelers never collide.

create or replace function claim_next_item(p_project uuid, p_labeler text, p_ttl_minutes int default 20)
returns table (id uuid, idx int, content jsonb)
language plpgsql
as $$
declare
  v_id uuid;
begin
  update project_items
     set status = 'pending', assigned_to = null, claimed_at = null
   where project_id = p_project
     and status = 'in_progress'
     and claimed_at < now() - make_interval(mins => p_ttl_minutes);

  select pi.id into v_id
    from project_items pi
   where pi.project_id = p_project
     and pi.status = 'in_progress'
     and pi.assigned_to = p_labeler
   order by pi.idx
   limit 1;

  if v_id is null then
    select pi.id into v_id
      from project_items pi
     where pi.project_id = p_project
       and pi.status = 'pending'
     order by pi.idx
     for update skip locked
     limit 1;

    if v_id is not null then
      update project_items
         set status = 'in_progress', assigned_to = p_labeler, claimed_at = now()
       where project_items.id = v_id;
    end if;
  end if;

  if v_id is null then
    return;
  end if;

  return query
    select pi.id, pi.idx, pi.content from project_items pi where pi.id = v_id;
end;
$$;


-- ── Useful admin views (run separately if wanted) ─────────────────────────────

-- Pending applications (for quick review)
-- create view pending_applications as
--   select id, name, email, specialty, institution, country, license_number,
--          years_experience, note, created_at
--   from expert_applications
--   where status = 'pending'
--   order by created_at asc;

-- Waitlist by type counts
-- create view waitlist_summary as
--   select type, count(*) as total
--   from waitlist
--   group by type
--   order by total desc;


-- ── Clinical failure library + regression benchmark ──────────────────────────
-- One table, two jobs, because they are the same object at different points in its
-- life: a clinician finds a clinically meaningful failure, and THAT CASE becomes the
-- regression test. Splitting them would mean copying the case and letting the copies
-- drift.
--
--   status       open -> the model still fails it; fixed -> a later version passed it;
--                regressed -> it passed once and fails again (the dangerous one).
--   in_benchmark whether it is served by POST /benchmark/run into the next evaluation.
--   content      the ORIGINAL model input, so a case can be re-run verbatim against a
--                new version. This is what makes the suite permanent.
--
-- Scoped by client_email: one client's failures are never visible to another, matching
-- the isolation the rest of the platform enforces.

create table if not exists clinical_failures (
  id               uuid primary key default gen_random_uuid(),
  client_email     text not null,
  case_key         text not null,          -- the CLIENT's case id, stable across runs
  project_id       uuid references project_submissions(id) on delete set null,
  model_version    text,                   -- version this failure was found in
  clinical_domain  text,
  case_type        text,
  severity         text,                   -- Minor | Moderate | High | Critical
  error_category   text,
  expected_triage  text,
  model_triage     text,
  content          jsonb not null default '{}'::jsonb,   -- the model input, re-runnable
  output           jsonb,                                -- what the model said
  assessment       jsonb,                                -- the clinician's full label
  rationale        text,
  status           text not null default 'open',         -- open | fixed | regressed
  in_benchmark     boolean not null default false,
  fixed_in_version text,
  occurrences      int not null default 1,
  first_seen       timestamptz not null default now(),
  last_seen        timestamptz not null default now(),
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);

-- A case has ONE record per client, updated as versions come and go, so the history of
-- "found -> fixed -> regressed" lives on the row rather than as duplicate rows.
create unique index if not exists clinical_failures_client_case
  on clinical_failures (client_email, case_key);

-- The queries the library exists to answer: most common high-severity failures, by
-- domain, and what is still open.
create index if not exists clinical_failures_client_sev
  on clinical_failures (client_email, severity);
create index if not exists clinical_failures_client_domain
  on clinical_failures (client_email, clinical_domain);
create index if not exists clinical_failures_client_status
  on clinical_failures (client_email, status);
create index if not exists clinical_failures_benchmark
  on clinical_failures (client_email, in_benchmark);
