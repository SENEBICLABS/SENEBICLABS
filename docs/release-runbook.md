# Release runbook: native task store

Every production-changing step is marked **[approval]**: only the owner authorises it, and
each one is a separate go. Commands use `$DB` for the database's **direct** connection
string, `$API` for the API base URL, and `$ADMIN` for an operator key. Nothing here prints
secrets or client data.

What this sequence rests on (all verified 24 Oct 2026 on throwaway infrastructure):
- **Either app can be deployed before or after the migrations.** The workforce app falls
  back to the pre-migration pool columns when the database lacks the new ones (it serves
  Label Studio pools only), and HEALTH treats a missing `task_store` as Label Studio. The
  new code on the pre-release schema passes the Label Studio end-to-end scenario
  (`scripts/native_e2e_stack.sh up --with-label-studio --schema before`).
- **The migrations are safe for the deployed old code.** Every addition has a default or
  is unused by it.
- The only ordering constraints are between migrations: workforce `021` before HEALTH
  `005` (005's functions read `pools.project_id`), and `006` after `005`.

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
scripts/native_e2e_stack.sh up --with-label-studio                # rehearsal again, with the transition test
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

**Label Studio is not part of this step.** Everything in Label Studio so far is internal test
work: its projects, tasks and annotations can be recreated, so a fresh Label Studio export is
not a release gate. The exports already taken are kept unchanged. A final export is still
taken before Label Studio is retired (step 9).

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
dashboard loads, an existing Label Studio pool serves a case, and `/project/results`
answers for an existing project.

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
- an existing Label Studio pool still serves and records a judgement.

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

1. `POST /project/projects` from a template, then
   `POST /project/admin/task-store {"store": "native"}` **before** any ingest.
2. Create its pool with `project_id` set
   ([native-task-store.md](native-task-store.md#turning-it-on)), then grant the internal
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
- any Label Studio call from the pool;
- any label not matching what the rehearsal produces for the same answers.

Keep the data, fix forward.

## 7. Watch [7 days]

The scheduled check stays green: no event pending over 10 minutes, nothing failed, no
delivery given up.

## 8. Transition away from Label Studio [approval per project]

- **New client projects go native** from now on (step 6's two calls). Making `native` the
  database default is a later one-line change, once you decide it.
- **Existing Label Studio projects** keep working until delivered, then retire with Label
  Studio.
- The old **rehearsal and demo** projects are internal test work and disposable. Recreate
  them natively, from the existing exports or from scratch, if they are still useful. Do not
  migrate them.

## 9. Retire Label Studio [approval]

Only when this returns no rows (read-only; run it against production):

```sql
-- Projects still on Label Studio and not delivered, or with an open Label Studio pool.
select s.id, s.stage from project_submissions s
 where s.task_store = 'label_studio' and s.ls_project_id is not null and s.stage <> 'delivered'
union
select p.project_id, 'open LS pool ' || p.id from pools p
 where p.project_id is null and p.ls_project_id > 0 and p.closed_at is null;
```

Then:
1. Take the final Label Studio export with `backup.sh` and archive it with its checksums.
2. Stop the VM. Don't delete it yet.
3. Run the scheduled check and the full native rehearsal against production read paths.
   Both must stay green with Label Studio stopped.
4. After a soak week: delete the VM and its disk; remove the
   `annotate.senebiclabs.com` DNS record; remove `LS_URL`, `LS_TOKEN`,
   `LS_WEBHOOK_SECRET` and `LS_CALLBACK_URL` (Render) and `LABEL_STUDIO_*` (Vercel).
5. Remove the Label Studio code (`/ls/*`, `labelstudio.py`, `lib/labelstudio.ts`, the
   compose files, the `Makefile` targets) in one reviewed change.

## Rolling back, by situation

| Situation | Do | Data |
|---|---|---|
| A migration failed | Fix and re-run (transactional or idempotent) | Untouched |
| A deploy regressed, no native pool open | Redeploy the previous revision | Untouched |
| A native pool is open | Close the pool first, then redeploy if needed. Old code does not know the native store and would treat the pool as a Label Studio pool | Native answers stay in `task_answers`; `/admin/rebuild` re-scores when fixed code returns |
| Data corrupted | Restore the step-2 dump into a new database, replay legitimate later writes by `created_at` | See [operations.md](operations.md#backups) |
