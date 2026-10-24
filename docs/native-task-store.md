# The native task store

How Senebiclabs serves tasks and stores answers without Label Studio, how to turn it on for
a project, and how to undo that. The audit behind the decision is an internal record
and is not published here.

## What it is

A project is on one of two stores, recorded in `project_submissions.task_store`:

- `label_studio` is today's path, and the default. Nothing about it changed.
- `native` means the project never touches Label Studio. Its items are the tasks, clinicians'
  answers are stored in our own database, and HEALTH scores them from there.

| Piece | Where | What it does |
|---|---|---|
| `project_items.task_no` | HEALTH migration 005 | Every item's task number, from 1,000,000,000 up. Label Studio ids sit below that, which is how the clinician platform tells the stores apart |
| `task_answers` | migration 005 | The answer of record, in the same result format Label Studio held, so HEALTH's parser, consensus, adjudication, second reading, scoring and delivery read it unchanged |
| `answer_events` | migration 005 | An outbox written in the same transaction as each answer. HEALTH drains it and scores the task |
| `ls_task_map` | migration 005 | Every Label Studio task id an item has had, per instance. History is joined through this, never by a bare number |
| `pools.project_id` | workforce migration 021 | Links a pool to its HEALTH project. A native pool reads that project's `eval_config` and `reviewers_per_item` directly, so there is no second copy to drift |
| `native_submit_judgement`, `native_approve_review` | migration 005 | Each write is one transaction: answer, work record and event, or nothing |
| `services/tasks.py` | HEALTH | Release, the drain, and reading a task in the shape scoring expects |
| `lib/taskstore.ts`, `lib/gate.ts` | workforce | Serving and submitting on the native store |

What a clinician sees does not change. Answers are now attributed to the clinician who gave
them, where Label Studio could only name its service account. That attribution stays
internal: `_client_item` strips it from everything a client receives.

## Turning it on

**Deploy order matters.** The workforce app reads `pools.project_id` on every pool query.

1. Take a backup (`~/senebiclabs-backups/backup.sh`).
2. Apply the workforce repo's `migrations/021_pool_project.sql`, then HEALTH's
   `migrations/005_native_task_store.sql`, in the Supabase SQL editor. Both are additive
   and safe to re-run. 005 gives every existing item a task number and leaves every
   project on `label_studio`.
3. Deploy HEALTH, then the workforce app.

For a **new** project, before any item has been released:

```bash
curl -X POST "$API/project/admin/task-store" -H "X-Admin-Key: $ADMIN" \
  -H "Content-Type: application/json" -d '{"project_id": "<id>", "store": "native"}'
```

Then create its pool with `project_id` set. The pool needs no config of its own:

```sql
insert into pools (name, ls_project_id, calibration_items, project_id, open_access)
values ('<Client> — <module>', 0, '[]'::jsonb, '<project_id>', false);
```

`ls_project_id` is required by the table but unused by a native pool. Grant eligibility as
for any pool. Ingest as usual: items are released to clinicians automatically
(`/sync-pending`), or by hand with `POST /project/admin/release`.

A project that already has tasks in Label Studio cannot be switched this way. Move it with
`scripts/reconcile_ls_identity.py` (below).

## Running it

| Need | Route (admin key) |
|---|---|
| Release pending items | `POST /project/admin/release {project_id}` |
| See answers waiting, processing or failed | `GET /project/admin/answer-events/{project_id}` |
| Retry failed events | `POST /project/admin/answer-events/retry {project_id}` |
| Re-score every task from its stored answers (replaces `/ls/pull`) | `POST /project/admin/rebuild {project_id}` |
| Held cases for the clinician platform | `GET /project/admin/held/project/{project_id}` |

Scoring is triggered three ways: the clinician platform nudges HEALTH after every answer,
every `/results` poll drains anything left, and a rebuild or retry drains again. A lost
nudge delays scoring and loses nothing, because the events wait in the database.
An event that fails is retried up to five times, then parked as `failed` until an operator
retries it. A worker that dies mid-way has its claim released after five minutes.

`/ls/sync` and `/ls/pull` refuse a native project with a 409. Nothing falls back to Label
Studio.

## Undoing it

- **A new project, before any answer:** `POST /project/admin/task-store {"store": "label_studio"}`.
  This is refused once an answer exists, because moving back would strand it.
- **A migrated project:** run the `rollback.sql` the reconciliation produced. It is one
  transaction and restores every remapped row.

## Re-uploading an item

Uploading content again at an index never deletes anything (migration `006`):

| The upload is | What happens |
|---|---|
| The same content again | Nothing is written. The image is compared by the hash of its bytes, not by its signed URL |
| A change, before anyone has started on the item | The item is updated in place |
| A change, after any work on it | The old item is kept, `superseded`, with its answers, label and history, and a new version replaces it in review |

Superseded versions are never counted, scored again or delivered. They stay in
`/project/admin/export`, where `status` shows them.

## Running it day to day

Monitoring, alerts and backups are in [operations.md](operations.md). The release sequence
is in [release-runbook.md](release-runbook.md), and the schema rules are in
[schema.md](schema.md).

## Moving a project that is already on Label Studio

**Current decision:** the existing Label Studio projects (rehearsal and demo) are
disposable test data. Recreate them natively if they are still useful; do not migrate them.
The tool below remains for a future case with real history, once the two problems at the
end of this section are fixed.

`scripts/reconcile_ls_identity.py` works offline, from backups only:

```bash
python scripts/reconcile_ls_identity.py \
  --ls gcp-2026=~/senebiclabs-backups/2026-10-05-2104 \
  --ls azure-2026=<latest backup> \
  --supabase <latest backup> --out <private folder>
```

It writes `report.md`, `forward.sql` and `rollback.sql`, and applies nothing. A project is
included only when:
- every reference resolves to exactly one of its own items, through `_item_id`;
- it has exactly one pool;
- Label Studio holds no task in its project that is not one of its items;
- every stored label re-scores identically from the answers it would hold.

Answers are migrated in the order they were given. Order is part of scoring: a one-to-one
tie on a structured finding goes to the first answer, and free-text versions are listed
first to last.

Run against the October backups, every non-adjudicated label re-scores exactly (9 of 12; the
other 3 are a senior's final decision, which the check skips). The rehearsal project is
blocked because three pools share its Label Studio project.

Before this plan is used on production it must be made idempotent and guarded against data
newer than its backup. A second run currently duplicates answers that have no clinician.

## Testing

```bash
make test                                  # hermetic HEALTH suite
docker run -d --rm --name senebi-sqltest -e POSTGRES_PASSWORD=test \
  -p 127.0.0.1:55432:5432 --tmpfs /var/lib/postgresql/data postgres:15
SQLTEST_DSN=postgresql://postgres:test@127.0.0.1:55432/postgres PYTHONPATH=. pytest tests/sql
```

`tests/sql` builds a disposable database from both repos' schema files and proves the
store's guarantees: atomic writes, the reviewer ceiling under concurrent submits, one
worker per task, crash recovery, and the full journey of every template's answers from the
workforce translator to the client deliverable. It needs the workforce repo checked out
beside this one, or `WORKFORCE_REPO` set.

## Rehearsing a cutover

`scripts/native_e2e_stack.sh up` starts a throwaway copy of the whole system:
- Postgres with both repos' schemas;
- PostgREST at `/rest/v1`, as Supabase serves it;
- the HEALTH API on :8765;
- the workforce app on :3765.

Label Studio is pointed at a closed port, so the native path cannot quietly depend on it.
`up` prints the command for `tests/e2e/test_native_cutover.py`, which drives a client,
clinicians, a senior and an operator over HTTP:
- ingest and serving;
- agreement, a split settled by a senior, and the reviewer ceiling;
- second reading;
- `number` and highlight answers;
- the image route's permissions;
- delivery with identities stripped.

`scripts/native_e2e_stack.sh down` removes all of it. Nothing in the stack reads
production settings.

Caching to expect: the platform reuses HEALTH's held-case list for up to 20 seconds, so a
newly held case can take that long to reach a senior.
