-- Migration 003: lock down the failure library
-- Paste into the Supabase SQL editor (Dashboard -> SQL Editor -> New query -> Run).
--
-- Migration 002 created clinical_failures without row level security, unlike every other
-- table in the schema. With RLS off, the anon key can read and write it; with RLS on and
-- no policy, only the service role (which bypasses RLS) can — which is what the API uses.
-- A client's failure library is their competitive information, so this matters.
--
-- Safe to re-run.

alter table clinical_failures enable row level security;
