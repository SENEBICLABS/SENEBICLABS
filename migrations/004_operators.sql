-- Migration 004: per-operator admin keys
-- Paste into the Supabase SQL editor (Dashboard -> SQL Editor -> New query -> Run).
-- Safe to re-run.

-- ── Operators (one admin key per person) ───────────────────────────────────────
-- Each operator authenticates with their own key (X-Admin-Key), so every admin decision
-- is attributed to a person and one key can be revoked without rotating everyone's.
-- Only the SHA-256 of the key is stored. The deployment's ADMIN_API_KEY remains the root
-- key and is not stored here.
create table if not exists operators (
  id            uuid primary key default gen_random_uuid(),
  name          text not null,
  email         text,
  key_hash      text not null unique,
  active        boolean not null default true,
  created_at    timestamptz not null default now(),
  last_used_at  timestamptz
);

-- RLS: service key only. A key hash must never be readable with the anon key.
alter table operators enable row level security;
