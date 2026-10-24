-- Migration 007: declare the api_keys table that production already has
-- Paste into the Supabase SQL editor. Safe to re-run; on production it changes nothing.
--
-- app/services/api_keys.py has stored self-serve API keys here since the developer
-- console shipped, but the table was created by hand and no migration declared it, so a
-- database built from this repository could not issue a key. The columns are the ones
-- production has (read from a backup) and the code uses. Only a SHA-256 of a key is
-- stored; the key itself is shown to its owner once.

create table if not exists api_keys (
  id            uuid primary key default gen_random_uuid(),
  email         text not null,
  key_hash      text not null unique,
  last4         text,
  label         text,
  created_at    timestamptz not null default now(),
  last_used_at  timestamptz,
  revoked       boolean not null default false
);

-- Service key only, like every other table here.
alter table api_keys enable row level security;
