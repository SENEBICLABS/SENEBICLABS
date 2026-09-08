-- Migration 002: clinical failure library + regression benchmark
-- Paste into the Supabase SQL editor (Dashboard -> SQL Editor -> New query -> Run).
-- Safe to re-run: every statement is IF NOT EXISTS.

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
