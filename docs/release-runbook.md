# Release runbook: native task store

Every production-changing step is marked **[approval]**: only the owner authorises it, and
each one is a separate go. Commands use `$DB` for the database's **direct** connection
string, `$API` for the API base URL, and `$ADMIN` for an operator key. Nothing here prints
secrets or client data.

Steps 1 to 5 were carried out for the native-pilot release in October 2026, when HEALTH
still served Label Studio projects alongside the task store; the migrations they apply are
in production. HEALTH has since removed Label Studio (see step 9), so it now **requires**
those migrations: it no longer runs on a schema without `task_store`. The only ordering
constraints are between migrations: workforce `021` before HEALTH `005` (005's functions
read `pools.project_id`), and `006` after `005`.

## 0. Release content [decided]

The clinician copy commit (`1b408fa`) was merged to the workforce `main` and deployed before
this release, so it is treated as shipped. The release is one branch per repo,
`release/native-pilot`, based on each repo's `origin/main`. Vercel builds `main` on push,
which is safe (see above), but merge deliberately rather than by accident.

## 1. Preflight (read-only)

```bash
# HEALTH
PYTHONPATH=. pytest -q --ignore=tests/e2e                         # expect all pass
scripts/native_e2e_stack.sh up                                    # then the printed rehearsal command
scripts/native_e2e_stack.sh down
# workforce
npx vitest run && npx tsc --noEmit && npx eslint . && npx next build
# production, read-only
python scripts/schema_check.py --dsn "$DB"                        # baseline_missing must be []
```

**Pass:** every suite green; `schema_check` shows no missing baseline columns and
`duplicate_current_idx` 0. **Stop** on anything else.

## 2. Backups [approval: uses production credentials, read-only]

The database connection string is never typed on a command line or pasted into chat. You
put it in a file only you can read: Supabase dashboard, then Connect, then **Session
pooler** (port 5432). The direct host is IPv6-only on the free plan, and the transaction
pooler (6543) cannot hold a consistent snapshot.

```bash
mkdir -p ~/.config/senebiclabs && chmod 700 ~/.config/senebiclabs
install -m 600 /dev/null ~/.config/senebiclabs/backup-db.url
nano ~/.config/senebiclabs/backup-db.url          # paste the session pooler string, save

D=~/senebiclabs-backups/<date>-release
python scripts/backup_verify.py --out $D/db --preflight-only   # metadata only; dumps nothing
python scripts/backup_verify.py --out $D/db       # reads the file above; refuses it if not mode 600
python scripts/storage_backup.py --out $D/images --expect $D/db/db-<stamp>.json
```

**Label Studio is not part of this step.** Everything in Label Studio was internal test
work: its projects, tasks and annotations can be recreated, so a fresh Label Studio export is
not a release gate. The exports already taken are kept unchanged. A final export is still
taken before the Label Studio VM is decommissioned (step 9).

**The preflight runs first, every time.** It connects read-only, with a 10-second connect
timeout and a 30-second statement timeout, and reads only catalog metadata: the server
version, the database size, and each table's and index's size. No application rows. It
then refuses, before anything is dumped, if any of these holds:
- the version is unknown, or outside 13 to 18;
- the matching `postgres:<major>` image is not already on this machine. It is never
  downloaded automatically, because its size cannot be known in advance. Pull it yourself
  when there is room, and run the preflight again;
- Docker is not available;
- any filesystem involved (the output folder, the temp folder, and Docker's data
  directories, including containerd's) lacks room for its share plus `--margin-mb`
  (default 1024). Shares on one filesystem are added together. The dump is bounded by the
  tables' on-disk size;
- RAM (**not counting swap**) lacks room for the in-memory restore (tables **and indexes**,
  an empty cluster, capped WAL, server memory) plus `--memory-margin-mb` (default 512).

The restore container is started with those caps: the in-memory data folder is capped at the
estimate, its memory too, and it gets no swap. If an estimate were ever wrong, the restore
fails inside its limit rather than starving the machine. `--preflight-only` prints the full
plan with every number and stops.

Only `db-<stamp>.dump` with `db-<stamp>.json` is a backup. A run that fails or is interrupted
keeps its evidence as `db-<stamp>.UNVERIFIED.dump` and `.UNVERIFIED.json`, which record the
stage that failed and the error. A run killed outright leaves `db-<stamp>.dump.partial`. Do
not use either as a backup, and do not delete them before you know why the run failed.
Nothing is ever overwritten. The restore container is removed however the run ends. If it
could not be removed, the run says `CLEANUP FAILED` with the command to remove it, and exits 1
even when the backup itself verified.

**Pass:**
- `backup_verify` exits 0, prints `BACKUP VERIFIED`, and its `db-<stamp>.json` says `"status": "verified"`. That means the restore matched every row count, every table's content checksum, and the full schema inventory (columns, constraints, indexes, functions, triggers, policies, RLS, sequences);
- `storage_backup` reports no failures, and `db_references_missing_from_backup` is empty. It refuses an `.UNVERIFIED.json` passed to `--expect`.

**Stop** otherwise. Record the dump's sha256 in the release log.

## 3. Migrations [approval, each one]

In the Supabase SQL editor, in this order, checking after each:

| Order | File | Check |
|---|---|---|
| 1 | workforce `migrations/021_pool_project.sql` | `schema_check --require workforce_021` |
| 2 | HEALTH `migrations/005_native_task_store.sql` | `--require health_005` |
| 3 | HEALTH `migrations/006_item_versions.sql` | `--require health_006` |
| 4 | HEALTH `migrations/007_declare_api_keys.sql` | `--require health_007` |
| 5 | workforce `migrations/022_declare_production_schema.sql` | `--require workforce_022` |

Then `notify pgrst, 'reload schema';`. Check that the live apps are unaffected: the
dashboard loads, a pool serves a case, and `/project/results` answers for an existing
project.

Before the first, `schema_check` (step 1) shows which of these are already applied. 007
and 022 only declare objects production already has. 022 changes `invites.token` only if
that column exists.

**If a migration fails:** the transactional ones (005, 006) leave nothing behind; fix and
re-run. The others are idempotent; re-run. **Never** restore the database for a failed
migration. Restore only for corrupted data.

## 4. Deploy [approval]

HEALTH on Render first, then the workforce app on Vercel.

**Pass:**
- `$API/api/v1/health` is ok;
- `POST $API/api/v1/project/internal/process-answers` without a key returns 403;
- `GET .../admin/ops-health` with `$ADMIN` returns 200;
- a pool serves and records a judgement.

**Roll back** by redeploying the previous revision of whichever app regressed. The schema
stays: old code ignores it.

## 5. Switch on monitoring [approval]

Prerequisites:
- step 4 is done, since `ops-health` exists only in the new HEALTH code;
- `.github/workflows/ops.yml` is committed to the default branch. GitHub runs scheduled
  workflows only from there.

Create a dedicated operator key (`POST /project/admin/operators`) and set the
`OPS_API_BASE`, `OPS_APP_URL` and `OPS_ADMIN_KEY` repository secrets. Run the `ops` workflow
by hand once.

**Pass:** the run is green. See [operations.md](operations.md).

## 6. A fresh native pilot [approval]

Use an internal account and synthetic cases. Do **not** migrate the old rehearsal or demo
projects. Use a text template. An image pilot waits until the portal upload and the
clinician's image view have been checked against real Storage, which no test has done yet.

1. `POST /project/projects` from a template. New projects are created on the task
   store; confirm `task_store` is `native` before any ingest.
2. Create its pool with `project_id` set
   ([native-task-store.md](native-task-store.md#starting-a-project)), then grant the internal
   clinicians who will test it, including a senior reviewer.
3. `POST /project/ingest` a handful of synthetic items.

Validate through the real apps:
- each clinician is served and submits;
- the reviewer ceiling holds;
- a deliberate split is held and settled by the senior;
- a second-reading pool (if relevant) approves;
- a re-upload of an answered item creates a new version and keeps the old answer;
- delivery with no clinician identity in `/results`;
- `ops-health` stays ok.

**Stop and close the pool** (`pools.closed_at`) on any of:
- a failed scoring event;
- any 5xx;
- any label not matching what the rehearsal produces for the same answers.

Keep the data, fix forward.

## 7. Watch [7 days]

The scheduled check stays green: no event pending over 10 minutes, nothing failed, no
delivery given up.

## 8. Transition away from Label Studio [done]

Every new project is created on the task store. The Label Studio projects were internal
test work and are not migrated; recreate any still useful as new projects.

## 9. Remove Label Studio

HEALTH's code no longer has Label Studio (`/ls/*`, `labelstudio.py`, the `LS_*` settings, the
compose files, the `Makefile` targets, and every fallback for a project on it). Removal is
complete only when every step below is done and verified; until then the deployed apps
and the infrastructure still carry it. Each **[approval]** is a separate go.

The Label Studio work is internal test data, disposable by decision: it is deleted, not
retired or migrated. No client project has ever run on this platform, so a Label Studio
project's stage means nothing here: `delivered` and `production` are test workflow states,
and those projects are deleted with the rest. On 28 Oct 2026 that was four projects (two of
them `delivered`) and five pools, none holding a native answer.

**1. Pre-check (read-only).**

```bash
python scripts/label_studio_precheck.py      # reads ~/.config/senebiclabs/backup-db.url (mode 600)
```

It never prints the connection string or the host, and runs in one read-only snapshot. It
applies the deletion script's own guards, taking the expected counts and the reviewed
foreign keys from that file, and lists every project and pool it would delete with what
hangs off each, the image keys only that work uses, and the kept counts the deletion asserts
are unchanged. Exit 0 and `READY` is the go for step 3; anything else is a stop. The recruiting
demo pool ("DEMO - lab interpretation") is one of the five: if demos still need it, recreate
it on the task store first, because it is deleted with the rest.

**2. Backup [approval: production credentials, read-only].** Step 2 of this runbook, fresh.
It is the only undo for step 3. Then rehearse step 3 on that backup, never on production:

```bash
python scripts/rehearse_label_studio_removal.py --dump ~/senebiclabs-backups/<date>/db/db-<stamp>.dump
```

It restores the verified dump into a throwaway Postgres of the same version, and in separate
copies of it: runs the deletion and checks that exactly the Label Studio work went and every
other row of every table is unchanged, then applies 008 and `schema_check`; makes a write fail
part way through and checks nothing changed; and adds an unreviewed rewrite rule and checks
the deletion refuses. It prints counts and table names only. `REHEARSED` is the go for step 3.
Run step 1 once more immediately before step 3.

**3. Delete the Label Studio work [approval: production write].** Run
`scripts/sql/delete_label_studio_test_data.sql` in the Supabase SQL editor. In one
transaction it deletes those projects and pools and everything that hangs off them (items,
answers, completions, review rows, flags, eligibility, calibration, failure-library rows,
their audit rows), and writes one `audit_events` row per deleted project and pool, with
`project_id` null so the deletion cannot remove it. It refuses, changing nothing, on anything
step 1 would stop on, and if any count outside that work changes. If it refuses, look at what
changed; do not edit its expected counts.

Verify (read-only): step 1 now stops with `found 0` projects and pools, its kept counts
match the ones printed before, and

```sql
select action, count(*) from audit_events
 where actor_name = 'Label Studio removal (reviewed SQL)' group by action;
-- label_studio_project_deleted 4, label_studio_pool_deleted 5
```

**4. Deploy HEALTH [approval].** Check: `$API/api/v1/ls/webhook` answers 404,
`ops-health` is ok, and a new project releases, scores and delivers through the task store
(step 6's checks, on an internal project).

**5. Migration 008 [approval: production write].** `migrations/008_remove_label_studio.sql`
in the SQL editor, after step 4 (the code before it reads `project_submissions.ls_project_id`,
which 008 drops). It refuses while any Label Studio work remains. It makes `task_store`
default to and only allow `'native'`, and drops `ls_task_map` and
`project_submissions.ls_project_id`. Check: `schema_check --require health_008` is READY.

**6. The workforce app [approval, separate PR in that repo].** Branch
`remove-label-studio-full` removes its Label Studio client, the Label Studio paths of the
task routes, the reconcile route, the pool gate's Label Studio and pre-migration fallbacks,
and the verification scripts that needed a Label Studio project. A pool with no `project_id`
is no longer served. Deploy it after step 3 (no pool without a project then exists), run
`scripts/native_e2e_stack.sh` against it, and check a pool serves and records a judgement.

**7. Infrastructure [approval each].**
1. Stop the Label Studio VM. Don't delete it yet. Run the scheduled check and the full
   rehearsal; both must stay green.
2. After a soak week: delete the VM, its disk and its snapshots; remove the
   `annotate.senebiclabs.com` DNS record.
3. Remove `LS_URL`, `LS_TOKEN`, `LS_WEBHOOK_SECRET`, `LS_CALLBACK_URL` from Render (the
   code ignores them) and `LABEL_STUDIO_*` from Vercel (after step 6). Revoke the Label
   Studio tokens, and drop them from local `.env` files and wherever secrets are kept.
4. Retire the Label Studio parts of the backup tooling (`~/senebiclabs-backups/backup.sh`,
   `ls_export.py`). The exports already taken are kept or discarded by decision.
5. The `client-images` bucket holds test images no row references (on 28 Oct, every object
   in it: the database referenced none). Delete them through the Storage API, only after a
   verified storage backup and only keys no `project_items` row references at that moment;
   `orphaned_image_keys` in the audit rows lists any that step 3 itself released.

**What stays in the schema, on purpose.** Migrations 001 to 007 are history and are not
edited. The `task_store` column stays (always `'native'`) while the workforce app reads it.
The workforce tables' `ls_task_id` / `ls_annotation_id` columns and `pools.ls_project_id`
now hold task numbers, answer ids and `0`; renaming or dropping them is that repo's
migration, after step 6.

## Rolling back, by situation

| Situation | Do | Data |
|---|---|---|
| A migration failed | Fix and re-run (transactional or idempotent) | Untouched |
| A deploy regressed, no native pool open | Redeploy the previous revision | Untouched |
| A pool is open | Close the pool first, then redeploy if needed. Code from before the native-pilot release does not know the task store and would treat the pool as a Label Studio pool | Answers stay in `task_answers`; `/admin/rebuild` re-scores when fixed code returns |
| Data corrupted | Restore the step-2 dump into a new database, replay legitimate later writes by `created_at` | See [operations.md](operations.md#backups) |
