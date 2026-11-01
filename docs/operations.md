# Operations: monitoring, alerts, backups

What watches the platform, what each alert means, and what to do about it. Nothing here
shows client data, clinician names or secrets: checks report ids and counts only.

## The scheduled check

`.github/workflows/ops.yml` runs `scripts/ops_check.py` every 10 minutes, one run at a time.
Each run:

1. checks the API answers `/api/v1/health` (allowing for a free-plan cold start);
2. checks the clinician app answers (an unauthenticated request must get `401`);
3. **runs the scoring drain** (`POST /project/internal/process-answers`): scores answers
   still waiting, re-sends failed client deliveries whose backoff has passed, and retires
   leases that have lapsed (`reservations_expired` in the reply). Retiring a lapsed lease
   changes nothing about who may read which case: serving already ignores one whose expiry
   has passed. It is the fallback, not the schedule: the sweep's own schedule lives in
   Postgres (below);
4. reads `GET /project/admin/ops-health` and fails on any check in alert.

## The lease sweep's own schedule

Retiring a lapsed lease does not depend on this workflow. `pg_cron` calls
`sweep_reservations()` every 5 minutes inside the database (migrations 010 and 011), which
needs no credential and cannot be throttled by anyone else. The drain above still calls
`expire_reservations()`, which is the fallback if that schedule ever stops.

**This workflow's schedule is not reliable enough to be the sweep's.** It is written as
every 10 minutes and has been measured firing between 2.8 and 7.1 hours apart, against a
15 minute warning threshold. The sweep was moved off it for that reason.

`reservations_sweep_stalled` is what notices the schedule stopping, and it is an **alert**:
it fails the scheduled run, emails the owner and makes `ops-health` answer `503`. A warning
would only have been printed in a run log. `select * from ops_sweep_state` answers the same
question directly.

A failed sweep does not get its own state. The failure rolls back, which is exactly what
preserves the last genuine success, so it surfaces here once that timestamp passes 20
minutes, and immediately in `cron.job_run_details`.

**A failed run is the alert.** GitHub emails the repository owner. Warnings are printed in
the run log without failing it.

To switch it on (needs approval), add three repository secrets:
- `OPS_API_BASE`;
- `OPS_APP_URL`;
- `OPS_ADMIN_KEY`, a **dedicated operator key** from `POST /project/admin/operators`, so it
  can be revoked on its own.

Until then the workflow skips itself, which is what it had been doing on every run since it
was written: `ops secrets not set; skipping`. Nothing had ever read `ops-health`. Setting
those three secrets is still the only thing that puts the **scoring drain** on a schedule
and sends a failure to somebody's inbox; the in-database monitor below does neither.

The same `ops-health` endpoint answers `503` when anything is in alert. Any uptime monitor
pointed at it with the admin key header raises the same alarm without parsing the body.

## The in-database monitor

Everything above notices faults. Until migrations 012 and 013 nothing noticed whether
*anything was reading them*: the workflow had been printing `ops secrets not set; skipping`
on every run since it was written, and because nothing recorded that, every alert in
`ops_health.py` was being raised to nobody and no query could tell you so.

`ops_monitor_tick()` closes that. `pg_cron` calls it every ten minutes inside Postgres, it
calls `GET /project/admin/ops-health` through `pg_net` with a dedicated operator key from
Supabase Vault, and it writes what came back into `ops_monitor_state`. One query answers the
question that had no answer:

```sql
select enabled, last_success_at, last_status_code, last_reported_status,
       last_failing_checks, last_error, consecutive_failures, runs
  from ops_monitor_state;
```

**It does not email anybody.** Postgres has nowhere to send mail. The record exists so that
something which can raise an alarm has something truthful to read, and the two halves cover
each other:

- if the **API** is down, this record says so from inside the database, which an external
  poller cannot distinguish from its own network being down;
- if this **schedule** dies, `ops-health` reports `ops_monitor_stalled` as an alert, so it
  answers `503` to the workflow or to any uptime monitor pointed at it with the key.

Neither half alone is enough and neither is asked to be. Point something at `ops-health`, or
set the workflow's three secrets, or both.

`pg_net` is asynchronous: `net.http_get` queues a request and a background worker sends it
after the transaction commits, so no single call can see its own reply. Each tick therefore
collects the *previous* tick's reply and then sends a new one, which is why every result is
one tick old and why the staleness bound is 35 minutes rather than 20.

**A 503 from the endpoint counts as a successful probe.** The endpoint answered, which is
what the heartbeat is about; the alert it carried belongs to the check that raised it. If a
503 counted as a probe failure, any alert anywhere would make the monitor alert too, and the
monitor's own alert would keep the 503 alive after the original fault was fixed.

**But the status code alone is never enough.** A heartbeat requires the reply to be JSON
carrying a `checks` array, whatever its status. A status code only proves that something at
the configured URL replied, and a marketing page, a parked domain or a proxy's sign-in page
answers 200 with HTML; recording that would give a monitor that is permanently green and has
never once spoken to this platform. 503 is no safer, because the route itself answers 503
with `{"detail": "Database unavailable."}` when it cannot reach the database, and a load
balancer answers 503 with its own page. An answer without an `ops-health` body is recorded
as a failure that says so and points at the `url` column first.

Nothing checks that the URL belongs to *this* deployment, and nothing can: a database cannot
know which project it is part of. The shape of the reply is the check.

### Switching it on

Three steps, in this order. Until all three are done, `ops_monitor_stalled` is reported as a
**warning** saying exactly which one is outstanding, which is deliberate: a warning does not
turn this endpoint into a 503 over an installation that is simply unfinished, and a pass
would report `ok` for a platform nothing is watching.

1. **Enable `pg_net`** once, as the project owner: Supabase dashboard, Database ->
   Extensions, search for `pg_net`. A migration does not do this, for the same reason 011
   does not enable `pg_cron`: the `postgres` role here is not a superuser.
2. **Mint a dedicated operator key and put it in Vault.** Dedicated so it can be revoked on
   its own:
   ```
   curl -s -X POST https://api.senebiclabs.com/api/v1/project/admin/operators \
     -H "X-Admin-Key: $ADMIN_API_KEY" -H 'Content-Type: application/json' \
     -d '{"name":"in-database monitor"}'
   ```
   Then store it **from `psql`, not from the SQL editor**. The dashboard keeps a history of
   what was run whether or not the query is saved, so a key pasted there is a key held in
   the dashboard for as long as that history lives. This keeps the value out of the shell
   history, out of every process's argument list and off the disk:
   ```bash
   set +o history                              # this shell stops recording
   umask 077
   read -rsp 'operator key: ' K; echo          # a builtin: no process, no argv, no echo
   printf '%s' "$K" > /dev/shm/opskey          # tmpfs, never written to disk
   unset K
   psql "$(cat ~/.config/senebiclabs/backup-db.url)" -q -v ON_ERROR_STOP=1 <<'SQL'
   \set k `cat /dev/shm/opskey`
   select vault.create_secret(:'k', 'ops_monitor_admin_key',
                              'Operator key used by ops_monitor_tick()') is not null as stored;
   SQL
   shred -u /dev/shm/opskey
   set -o history
   ```
   `read` is a shell builtin, so the key never appears in `ps`. `\set k \`cat ...\`` makes
   psql read the file, so the key is not in psql's own arguments either. `:'k'` quotes it as
   a SQL literal.

   **One exposure this cannot remove, so it is written down instead:** `vault.create_secret`
   takes the key as a literal and psql does not bind parameters, so the key is in the
   statement text sent to the server. If the project logs statements it lands in the
   Postgres log. Check first, and expect `none` or a duration long enough not to catch it:
   ```sql
   select name, setting from pg_settings
    where name in ('log_statement', 'log_min_duration_statement');
   ```
3. **Apply migration 013.** It refuses, with nothing applied, if `pg_cron` or `pg_net` is
   missing, if 012 has not been applied, if the Vault secret is absent, or if `net` has been
   added to the Data API's exposed schemas. It switches the monitor on last, so a refusal
   leaves it off.

   That exposed-schema check is **install-time only**. Nothing re-checks it: exposing `net`
   later is a dashboard setting no migration and no scheduled check here will notice, so it
   belongs on the list of things to confirm by hand below. It also depends on Supabase
   storing `pgrst.db_schemas` as a role setting; where PostgREST is configured from its own
   environment the check cannot establish an answer and says so rather than passing.

### The activation smoke test

Two things in this design can only be checked on the real project, because `pg_net` and
`supabase_vault` cannot be installed on a throwaway Postgres and the SQL tests stub their
interfaces. **Neither has been tested against Supabase.** Run these once after activating,
before trusting the monitor. Everything here is read-only except the ticks pg_cron runs
itself.

1. **Vault's real grants.** Asserted nowhere in this repository, and Supabase's
   documentation does not state it either way. If the service key can read the secret, the
   API's own key can read this monitor's operator key:
   ```sql
   select has_table_privilege('service_role', 'vault.decrypted_secrets', 'select') as svc,
          has_table_privilege('anon', 'vault.decrypted_secrets', 'select') as anon;
   -- both must be false
   ```
2. **pg_net's worker is really running.** 013 confirms the extension is installed, which is
   not the same thing. Wait two ticks, up to twenty minutes:
   ```sql
   select status, return_message, start_time from cron.job_run_details
    where jobid = (select jobid from cron.job where jobname = 'ops-monitor')
    order by start_time desc limit 3;
   ```
   Expect `nothing pending; sent <id>` then `answered 200: ok; sent <id>`. A repeated
   `failed: no reply recorded` means the worker is not draining the queue.
3. **It really reached `ops-health`**, and not something else answering on that URL:
   ```sql
   select last_success_at, last_status_code, last_reported_status, consecutive_failures
     from ops_monitor_state;
   ```
   `last_reported_status` must be `ok` or `warn` or `alert`. **Null means something answered
   but not `ops-health`**, and `last_error` will say so.
4. **The queue drains.** `select count(*) from net.http_request_queue` between ticks: expect
   0. A number that grows is the worker again.
5. **End to end**, with your own key: `GET /project/admin/ops-health` and confirm
   `ops_monitor_stalled` reads `ok` rather than the warning it showed before activation.

**What the reply is exposed to.** `net._http_response` holds the whole body of each reply,
and an `ops-health` body carries operational identifiers: project ids, task numbers and
event ids. No clinical content, no clinician names, no client URLs and no secrets. The tick
deletes the row as soon as it has read it, and a monitor switched off releases a reply it
will never collect, so the window is one tick rather than pg_net's six hour prune. It lives
in the same `net` schema under the same grants as the queue below.

**What the key is exposed to.** `pg_net` parks the request, headers included, in
`net.http_request_queue` until its worker sends it, which is seconds. Supabase grants
privileges on the `net` schema to `PUBLIC` as `supabase_admin`, and `postgres` cannot revoke
a grant it did not make, so those seconds cannot be closed off. What it means here: `anon`,
`authenticated` and `service_role` cannot open a direct connection and the Data API does not
expose `net`, so no API key reaches it; the roles that *can* connect already read every table
in the database, `operators` included, so the key tells them nothing new; and it is a
dedicated operator key, revocable on its own. Keep `net` out of Settings -> API -> Exposed
schemas, which 013 refuses to install without.

### Turning it off

Both statements, then the check that both took:

```sql
select cron.unschedule('ops-monitor');
update ops_monitor_state set enabled = false where name = 'ops-health';

-- expect: 0 | f
select (select count(*) from cron.job where jobname = 'ops-monitor') as jobs_left,
       (select enabled from ops_monitor_state where name = 'ops-health') as still_on;
```

**Doing only the second is the quiet failure.** The job stays scheduled and ticks
`skipped: disabled` for ever while `ops_monitor_stalled` drops from an alert to a warning, so
the endpoint stops answering 503 about a monitor that is still installed and no longer
watching anything. Nothing detects that state but the query above. `cron.unschedule` raises
if the job is already gone, so a rollback run twice errors on the first statement rather than
leaving the second undone.

A monitor switched off releases any request still in flight, so nothing is left pending and
no reply is left sitting in `net._http_response`.

No deploy, and nothing else in the platform reads this record, so nothing else changes:
`ops_monitor_stalled` returns to the warning it reported before 013, which does not make the
endpoint answer 503. To undo 012 as well, `drop function ops_monitor_tick()` and
`drop table ops_monitor_state`, after which the check reports the missing table as a warning.
The key is separate: revoke it with `DELETE /project/admin/operators/{id}` so it stops
working everywhere, and delete its row from `vault.secrets`.

## Measured capacity

`scripts/load_rehearsal.py` pushes a client-sized batch through the whole pipeline on the
throwaway stack and times each leg. It is not a pass/fail test: it reports where the
platform bends, so a turnaround can be promised from measurements rather than from hope.

```
scripts/native_e2e_stack.sh up
NATIVE_E2E_API=... NATIVE_E2E_APP=... NATIVE_E2E_DSN=... \
NATIVE_E2E_ADMIN_KEY=... NATIVE_E2E_JWT_SECRET=... \
python3 scripts/load_rehearsal.py --items 2000 --readers 8
```

Measured on the throwaway stack, with the clinician app in dev mode (a production build is
faster, so the serving figures are pessimistic):

| Leg | 2,000 items | 10,000 items |
|---|---|---|
| `POST /ingest` | 1.0s in 4 calls of 500 | 5.1s in 10 calls of 1,000 |
| release to servable | under a second | under a second |
| reserve + submit | median 1.4s, p95 2.1s per case | paced by people, not measured |
| scoring settles after the last answer | about a second | — |
| `GET /admin/report` | 0.8s | 1.2s |
| client's `GET /results`, delivered | 1.2s, 1.7 MiB | — |

What the runs establish, and what they do not:

- **Serving is correct under concurrency.** 2,000 cases through 8 clinicians at once: no
  case served twice, no errors, and the work split evenly (248 to 252 each).
- **Paging is right past 1,000 rows.** The report counted 2,000 of 2,000 and 10,000 of
  10,000. A truncated read here would quietly score a fraction of the batch and look fine,
  which is why `services/paging.py: fetch_all` exists.
- **A client may post the whole batch at once.** One 5,000-item body, 1.1 MiB, accepted in
  1.05s.
- **Throughput is clinicians, not the platform.** Every leg the platform owns is seconds;
  the only slow leg is people reading cases. Capacity planning is a roster question.
- **Not covered**: this runs against a local Postgres, not the hosted database on its
  plan's connection and CPU limits, and not against a free web plan's cold starts.

## What each check means

| Check | Alert or warning | What to do |
|---|---|---|
| API or clinician app down | Alert | Check Render and Vercel status and recent deploys. A cold start on a free plan shows as slow, not down |
| `scoring_events_waiting` | Alert: answers unscored over 10 minutes | Is the scheduled drain running? Run it by hand: `POST /project/internal/process-answers` |
| `scoring_events_failed` | Alert: scoring gave up after 5 tries | `GET /project/admin/answer-events/{project}` shows `last_error`. Fix the cause, then `POST /project/admin/answer-events/retry` |
| `scoring_events_abandoned` | Warning | A worker died mid-way. The next drain reclaims it after 5 minutes; nothing to do unless it repeats |
| `scoring_events_retrying` | Warning | Failed once, retrying. Watch it |
| `scoring_events` | Warning | `answer_events` could not be read, so no scoring check ran. A check that cannot run is reported as a warning, never as a pass |
| `second_readings_waiting` | Warning: authored work unread for a day | Nudge the pool's readers |
| `adjudication_waiting` | Warning: a split held for a day | Nudge the pool's seniors, or settle it via `/project/admin/adjudicate` |
| `reservations_lapsed_unswept` | Warning: leases past expiry still marked active for over 15 minutes | Serving is unaffected: an expired lease stops counting by itself. It says the tidy-up is not running. `select expire_reservations()`, then check the scheduled job |
| `reservations_overlong` | Alert | A lease has lived past the longest a renewal can carry it, which is five hours: 60 minutes per renewal and four renewals, because a renewal is refused once the lease has expired. `renew_reservation` cannot produce that, so something wrote to `item_reservations` around it. Find the writer |
| `reservations_starving_work` | Alert: a project's every open case held for over 90 minutes | Clinicians are being told there is no work while the work is there. Usually abandoned sessions. `select expire_reservations()` frees them; if it recurs, look for a client that reserves without submitting |
| `reservations_sweep_stalled` | Alert: the sweep has never succeeded, or last succeeded over 20 minutes ago | The sweep runs every 5 minutes inside Postgres (migration 011), so this is four missed ticks. Either the schedule stopped or every run since has failed, because a failed run preserves the previous timestamp rather than writing its own. Serving is unaffected, but `reservations_lapsed_unswept` follows. `select jobname, schedule, active from cron.job`, then `select status, return_message from cron.job_run_details order by start_time desc limit 5`. The scoring drain's own `expire_reservations()` call is the fallback while you fix it |
| `reservations_sweep_stalled` (warning) | Warning | `ops_sweep_state` could not be read, so nothing was checked. Before migration 010 is applied that is expected, and it is a warning rather than an alert so that deploying the code ahead of the migration cannot turn this endpoint into a 503 |
| `reservations` | Warning | The lease table could not be read, so none of the above was checked. If migration 009 is not applied yet, that is why, and cases are being served without being held |
| `ops_monitor_stalled` | Alert: the monitor is switched on and the endpoint has not answered it for over 35 minutes | Three missed ticks plus most of a fourth, allowing for every result being one tick old. Either the schedule stopped or every probe since has failed, so nothing inside the platform is watching it. `select jobname, schedule, active from cron.job where jobname = 'ops-monitor'`, then `select status, return_message from cron.job_run_details order by start_time desc limit 5`. `ops_monitor_probe` below says which of the two it is |
| `ops_monitor_stalled` (warning) | Warning | The monitor is not switched on, has no seeded row, or its table could not be read. Before migrations 012 and 013 that is expected. It is a warning rather than an alert so that deploying the code ahead of the migration cannot turn this endpoint into a 503, and not a pass because a platform nothing is watching is not healthy. Follow "Switching it on" above |
| `ops_monitor_probe` | Alert after 3 failures in a row, warning after 1 | What the probes got. A revoked key is the common one and says so in the note, which is an `operators` problem with the schedule running perfectly; the other causes are the API being down, a timeout, or `pg_net` not being installed. The note carries the recorded reason. A 503 from the endpoint is **not** a probe failure: the endpoint answered, and the alert it carried belongs to the check that raised it |
| `deliveries_failing` | Warning | The client's webhook is failing. It is retried on a growing schedule with the same event id; nothing to do yet |
| `deliveries_given_up` | Alert | The client refused it (4xx) or the retries ran out. Tell the client; they can poll `/results` or call `/webhook/redeliver` |
| `deliveries_awaiting_sign_off` | Warning: reviewed two days ago, not delivered | Deliver it: `/project/admin/advance` |
| `database_latency_ms` | Warning over 2 seconds | Check the Supabase dashboard (connections, CPU, the free plan's limits) |

**Not covered by this check**, so watch these on the providers' own dashboards:
- error rates and latency per route (Render and Vercel);
- database connection exhaustion and storage quota (Supabase);
- **whether `net` has been added to Settings -> API -> Exposed schemas.** Migration 013
  refuses to install while it is, but that is an install-time check and nothing re-checks
  it. While `net` is exposed, `anon` and `authenticated` can read
  `net.http_request_queue`, which holds the monitor's key in a request header until the
  worker sends it;
- the Supabase free plan's pause after 7 days without activity. `pg_cron` ticking every five
  minutes (migration 011) prevents that pause whether or not anything else is running, which
  the GitHub workflow could not be relied on for.

Two side effects of the in-database monitor worth knowing, because they are not what it is
for: probing every ten minutes keeps the API's free web plan from spinning down after
fifteen minutes of quiet, so the first clinician of the morning does not pay for a cold
start, and `last_failing_checks` gives the alert state a history rather than only a value at
the moment somebody asks.

## Backups

| What | How | Proves itself by |
|---|---|---|
| The database | `scripts/backup_verify.py --out <private folder>` (connection string from `~/.config/senebiclabs/backup-db.url`, mode 600) | Dumps from one snapshot, restores into a throwaway in-memory Postgres of the same version, and compares row counts, per-table content checksums and the schema inventory. Records the images the database refers to. Exit 1 on any difference. The password never appears in arguments, environment or output |
| Images (`client-images`) | `scripts/storage_backup.py --out <private folder> --expect <db report>` | Each copy is checked against the source's recorded size and must decode as an image; content-addressed keys are also checked against the bytes. Every image the database refers to must be present. Refuses to start if the copy would not fit on disk; reruns copy only new objects |
| Label Studio (until its VM is decommissioned; HEALTH no longer uses it) | `~/senebiclabs-backups/backup.sh` | Task and annotation counts per project |

The image check decodes client uploads, so it runs only with Pillow 13 or later, the first
release without known decoder vulnerabilities, which needs Python 3.11 or later. With an older
Pillow, or none, every object is reported as not checked and the run exits 1. Run it from a
Python 3.11 environment with `pip install "Pillow>=13" supabase`.

Run all three before every release and before every client project. The dumps hold client
and clinician data: keep them in a private folder (the scripts set owner-only permissions)
and under your retention policy. Never commit them, attach them to a ticket, or upload them
to CI.

**Restoring for real.** `pg_restore` the dump into a new Supabase project, or into a branch
of the existing one. Writes made after the dump are not in it; take them from the live
database by `created_at` and replay them deliberately. Prefer rolling back application code
over restoring data (see [release-runbook.md](release-runbook.md)).
