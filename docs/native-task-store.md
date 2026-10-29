# The task store

How Senebiclabs serves tasks and stores answers. It replaced Label Studio, which has been
removed from HEALTH. The audit behind the decision is an internal record and is not
published here.

## What it is

Every project's items are its tasks. Clinicians' answers are stored in our own database,
and HEALTH scores them from there.

| Piece | Where | What it does |
|---|---|---|
| `project_items.task_no` | HEALTH migration 005 | Every item's task number, from 1,000,000,000 up. Lower numbers in historical rows are Label Studio task ids |
| `task_answers` | migration 005 | The answer of record, as a result array, which HEALTH's parser, consensus, adjudication, second reading, scoring and delivery read |
| `answer_events` | migration 005 | An outbox written in the same transaction as each answer. HEALTH drains it and scores the task |
| `pools.project_id` | workforce migration 021 | Links a pool to its HEALTH project. A pool reads that project's `eval_config` and `reviewers_per_item` directly, so there is no second copy to drift |
| `native_submit_judgement`, `native_approve_review` | migration 005 | Each write is one transaction: answer, work record and event, or nothing |
| `services/tasks.py` | HEALTH | Release, the drain, and reading a task in the shape scoring expects |
| `item_reservations`, `reserve_next_item` | migration 009 | Holds a case for the clinician it was served to, for a bounded time, so two clinicians are never sent the same one |
| `lib/taskstore.ts`, `lib/gate.ts` | workforce | Serving and submitting |

Answers are attributed to the clinician who gave them. That attribution stays internal:
`_client_item` strips it from everything a client receives.

## Starting a project

Every project is created on the task store (`project_submissions.task_store = 'native'`).
Create its pool with `project_id` set. The pool needs no config of its own:

```sql
insert into pools (name, ls_project_id, calibration_items, project_id, open_access)
values ('<Client> — <module>', 0, '[]'::jsonb, '<project_id>', false);
```

`ls_project_id` is required by the table but unused. Grant eligibility as for any pool.
Ingest as usual: items are released to clinicians automatically (`/sync-pending`), or by
hand with `POST /project/admin/release`.

## Label Studio

There is no other store. Every project is created with `task_store = 'native'`, and
migration 008 makes that the only value the column allows (it stays while the workforce app
reads it). The internal Label Studio test work is deleted before 008 by
`scripts/sql/delete_label_studio_test_data.sql`; see
[release-runbook.md](release-runbook.md) §9.

## Running it

| Need | Route (admin key) |
|---|---|
| Release pending items | `POST /project/admin/release {project_id}` |
| See answers waiting, processing or failed | `GET /project/admin/answer-events/{project_id}` |
| Retry failed events | `POST /project/admin/answer-events/retry {project_id}` |
| Re-score every task from its stored answers | `POST /project/admin/rebuild {project_id}` |
| Held cases for the clinician platform | `GET /project/admin/held/project/{project_id}` |

Scoring is triggered three ways: the clinician platform nudges HEALTH after every answer,
every `/results` poll drains anything left, and a rebuild or retry drains again. A lost
nudge delays scoring and loses nothing, because the events wait in the database.
An event that fails is retried up to five times, then parked as `failed` until an operator
retries it. A worker that dies mid-way has its claim released after five minutes.

## Serving a case

A case is held from the moment it is served, not from the moment an answer arrives
(migration `009`). `reserve_next_item` records a reservation and returns a lease; the
clinician reads with no lock held anywhere.

Before this, the next case was whichever open one had the lowest number, and nothing was
written down. Clinicians who asked at the same moment were given the same case, and all
but one had the submission refused after they had already read it and formed a judgement.
The refusal was always correct. The wasted reading was the cost, and we pay for that time.

| Rule | Where it is enforced |
|---|---|
| A case is held by at most the number of reviews the project commissioned | `item_slots_taken` against `native_ceiling`, re-checked under a row lock |
| One clinician holds at most one lease on a case | a unique index, so a retried request cannot make a second |
| Asking again returns the same case | `reserve_next_item` serves an existing lease before drawing a new one |
| A lease that lapses frees the case | by predicate on `expires_at`, so no sweep has to run for this to be true |
| A re-uploaded case gives its lease back | a trigger on `project_items`, covering every path that supersedes |
| A lease is never clinical evidence | only non-withdrawn `task_answers` are scored or delivered |

Leases start at 60 minutes and renew up to four times, so an abandoned tab holds a case
for five hours at most. `expire_reservations()` tidies lapsed rows; it is housekeeping, and
correctness does not depend on it running.

Partly reviewed cases are served first. Spreading clinicians over fresh cases would avoid
collisions just as well, but it fills a project with half-reviewed work and nothing a
client can be sent; filling a case to its ceiling is what produces a settled finding.

A clinician who is not eligible, or whose pool is closed, is served nothing and is told
nothing more than that.

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

## Rehearsing end to end

`scripts/native_e2e_stack.sh up` starts a throwaway copy of the whole system:
- Postgres with both repos' schemas;
- PostgREST at `/rest/v1`, as Supabase serves it;
- the HEALTH API on :8765;
- the workforce app on :3765.

`WORKFORCE_REPO=<path>` points it at a different checkout of the clinician platform, which
is how a change there is rehearsed before it is applied.

`up` prints the command for `tests/e2e/test_native_cutover.py`, which drives a client,
clinicians, a senior and an operator over HTTP:
- ingest and serving;
- agreement, a split settled by a senior, and the reviewer ceiling;
- second reading;
- `number` and highlight answers;
- the image route's permissions;
- delivery with identities stripped.

`tests/e2e/test_reservation_integration.py` drives the same stack to prove that every
route which hands out a case holds it first: simultaneous asks, the configured number of
independent readings, a reload, a lapsed and a renewed lease, a flag, a re-upload, a HEALTH
outage, and the whole way through to a delivered result. It is the test that tells you
whether the clinician platform is wired to reservations: without that wiring most of it
fails, which is the point.

`scripts/native_e2e_stack.sh down` removes all of it. Nothing in the stack reads
production settings.

Caching to expect: the platform reuses HEALTH's held-case list for up to 20 seconds, so a
newly held case can take that long to reach a senior.
