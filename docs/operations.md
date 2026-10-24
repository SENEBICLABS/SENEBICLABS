# Operations: monitoring, alerts, backups

What watches the platform, what each alert means, and what to do about it. Nothing here
shows client data, clinician names or secrets: checks report ids and counts only.

## The scheduled check

`.github/workflows/ops.yml` runs `scripts/ops_check.py` every 10 minutes, one run at a time.
Each run:

1. checks the API answers `/api/v1/health` (allowing for a free-plan cold start);
2. checks the clinician app answers (an unauthenticated request must get `401`);
3. **runs the scoring drain** (`POST /project/internal/process-answers`): scores answers
   still waiting and re-sends failed client deliveries whose backoff has passed;
4. reads `GET /project/admin/ops-health` and fails on any check in alert.

**A failed run is the alert.** GitHub emails the repository owner. Warnings are printed in
the run log without failing it.

To switch it on (needs approval), add three repository secrets:
- `OPS_API_BASE`;
- `OPS_APP_URL`;
- `OPS_ADMIN_KEY`, a **dedicated operator key** from `POST /project/admin/operators`, so it
  can be revoked on its own.

Until then the workflow skips itself.

The same `ops-health` endpoint answers `503` when anything is in alert. Any uptime monitor
pointed at it with the admin key header raises the same alarm without parsing the body.

## What each check means

| Check | Alert or warning | What to do |
|---|---|---|
| API or clinician app down | Alert | Check Render and Vercel status and recent deploys. A cold start on a free plan shows as slow, not down |
| `scoring_events_waiting` | Alert: answers unscored over 10 minutes | Is the scheduled drain running? Run it by hand: `POST /project/internal/process-answers` |
| `scoring_events_failed` | Alert: scoring gave up after 5 tries | `GET /project/admin/answer-events/{project}` shows `last_error`. Fix the cause, then `POST /project/admin/answer-events/retry` |
| `scoring_events_abandoned` | Warning | A worker died mid-way. The next drain reclaims it after 5 minutes; nothing to do unless it repeats |
| `scoring_events_retrying` | Warning | Failed once, retrying. Watch it |
| `second_readings_waiting` | Warning: authored work unread for a day | Nudge the pool's readers |
| `adjudication_waiting` | Warning: a split held for a day | Nudge the pool's seniors, or settle it via `/project/admin/adjudicate` |
| `deliveries_failing` | Warning | The client's webhook is failing. It is retried on a growing schedule with the same event id; nothing to do yet |
| `deliveries_given_up` | Alert | The client refused it (4xx) or the retries ran out. Tell the client; they can poll `/results` or call `/webhook/redeliver` |
| `deliveries_awaiting_sign_off` | Warning: reviewed two days ago, not delivered | Deliver it: `/project/admin/advance` |
| `database_latency_ms` | Warning over 2 seconds | Check the Supabase dashboard (connections, CPU, the free plan's limits) |

**Not covered by this check**, so watch these on the providers' own dashboards:
- error rates and latency per route (Render and Vercel);
- database connection exhaustion and storage quota (Supabase);
- the Supabase free plan's pause after 7 days without activity. The scheduled check's
  activity also prevents that pause.

## Backups

| What | How | Proves itself by |
|---|---|---|
| The database | `scripts/backup_verify.py --out <private folder>` (connection string from `~/.config/senebiclabs/backup-db.url`, mode 600) | Dumps from one snapshot, restores into a throwaway in-memory Postgres of the same version, and compares row counts, per-table content checksums and the schema inventory. Records the images the database refers to. Exit 1 on any difference. The password never appears in arguments, environment or output |
| Images (`client-images`) | `scripts/storage_backup.py --out <private folder> --expect <db report>` | Each copy is checked against the source's recorded size and must decode as an image; content-addressed keys are also checked against the bytes. Every image the database refers to must be present. Refuses to start if the copy would not fit on disk; reruns copy only new objects |
| Label Studio (while it exists) | `~/senebiclabs-backups/backup.sh` | Task and annotation counts per project |

Run all three before every release and before every client project. The dumps hold client
and clinician data: keep them in a private folder (the scripts set owner-only permissions)
and under your retention policy. Never commit them, attach them to a ticket, or upload them
to CI.

**Restoring for real.** `pg_restore` the dump into a new Supabase project, or into a branch
of the existing one. Writes made after the dump are not in it; take them from the live
database by `created_at` and replay them deliberately. Prefer rolling back application code
over restoring data (see [release-runbook.md](release-runbook.md)).
