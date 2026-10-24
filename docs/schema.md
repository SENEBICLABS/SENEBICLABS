# Database schema: the source of truth

HEALTH and the workforce app share one Supabase Postgres database. The schema is defined
by the migration files in the two repositories and nothing else. A change made by hand in
the SQL editor without a migration is a defect, because a database rebuilt from the
repositories would differ from production. Three such changes were found in October 2026
and are now declared (`health 007`, `workforce 022`).

## Who owns what

| Owner | Tables |
|---|---|
| HEALTH (`supabase_schema.sql`, `migrations/`) | `project_submissions`, `project_items`, `audit_events`, `operators`, `api_keys`, `clinical_failures`, `expert_applications`, `waitlist`, `project_clinicians`, legacy `clinicians` columns, and the native task store: `task_answers`, `answer_events`, `ls_task_map` |
| workforce (`database.sql`, `migrations/`) | `clinicians`, `pools`, `pool_eligibility`, `task_completions`, `task_flags`, `review_items`, `sessions`, `sign_in_tokens`, `invites`, `access_requests`, `agreement_acceptances`, `calibration_attempts` |

## Order

A fresh database is built by applying, in order:
1. HEALTH `supabase_schema.sql`, then HEALTH `migrations/002` to `004`;
2. workforce `database.sql`, then every workforce migration (up to `022`);
3. HEALTH migrations `005` onwards.

`scripts/native_e2e_stack.sh` and `tests/sql/conftest.py` do exactly this. Restoring the
16 Oct 2026 production backup into a database built this way loads every table and row
(tested 24 Oct 2026).

## This release's migrations

| Migration | Changes | Old code unaffected? | Undo |
|---|---|---|---|
| workforce `021_pool_project` | `pools.project_id`; declares `pools.eval_config` and `maximum_annotations` (already in production) | Yes | Leave it; it is inert to old code |
| HEALTH `005_native_task_store` | Task numbers, `task_store`, answer store, outbox, store functions | Yes | Leave it; restore from backup only if data is corrupt |
| HEALTH `006_item_versions` | Item versions, one current item per index, answers outlive item deletion attempts | Yes | As above |
| HEALTH `007_declare_api_keys` | Declares `api_keys` (already in production) | Yes | None needed |
| workforce `022_declare_production_schema` | Declares `agreement_acceptances` and `invites.token_hash`, makes the unused plain `token` optional | Yes | None needed |

Each runs in one transaction or is made only of `if not exists` statements, and each is
safe to re-run.

## Rules

- **Every schema change is a migration file** in the owning repository, applied in order.
  Never change production by hand without writing the file in the same change.
- **Check before and after.** `scripts/schema_check.py --dsn … --require <step>` reports
  which steps a database has and refuses a step whose preconditions fail (for example,
  duplicate current items at one index would make 006 fail). It cannot write.
- **Never drop.** Declarations use `if not exists`; columns no longer used are made
  optional, not removed. Removing data is a separate, explicit, approved change.
