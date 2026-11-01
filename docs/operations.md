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
   the dashboard for as long as that history lives.

   **Check the logging settings first**, because one of them decides what happens if the
   call fails, and that is the dangerous case rather than the successful one:

   ```sql
   select name, setting from pg_settings
    where name in ('log_statement', 'log_min_duration_statement', 'log_min_error_statement');
   ```

   On production as of 2026-11-01: `log_statement = ddl`, `log_min_duration_statement = -1`,
   `log_min_error_statement = error`. A successful `select vault.create_secret(...)` is
   neither DDL nor slow, so it is **not** logged. **A failing one is**, in full, including
   the key, because `log_min_error_statement = error` logs the text of any statement that
   raises. Checking only the first two settings would have missed that entirely.

   **Save this as a file and run it with `bash`.** Three reasons, and none of them is
   style:

   - it depends on `read` and `printf` being **bash builtins**. They are in bash, so no
     process is spawned and the key reaches no argument list. Under a shell where either is
     an external command, the key would be in that process's arguments and visible in `ps`.
     The script refuses to run if `BASH_VERSION` is unset;
   - running it as a script rather than pasting it is what makes the `trap` fire, which is
     what shreds the key file when the call fails or you interrupt it. Pasted into an
     interactive shell, an `EXIT` trap does not run until you log out;
   - it needs the repo root as its working directory, for `.venv`.

   If you copy it out of the raw Markdown rather than the rendered page, remove the leading
   indentation: a heredoc terminator has to be at the start of its line.

   ```bash
   #!/usr/bin/env bash
   # Requires bash, and the repo root as the working directory (for .venv).
   #   bash store-monitor-key.sh
   [ -n "${BASH_VERSION:-}" ] || { echo "run this with bash, not sh" >&2; exit 1; }
   set -uo pipefail
   umask 077
   KEYFILE=/dev/shm/opskey          # must match the path in the \set line below
   PASSFILE=/dev/shm/opspass

   # Whatever happens next, including Ctrl-C and a failed psql, both files go. The EXIT
   # trap deliberately does not call `exit` itself: doing so would return shred's status
   # and mask psql's, which is the one thing this script has to report accurately.
   cleanup() { shred -u "$KEYFILE" "$PASSFILE" 2>/dev/null || true; }
   trap cleanup EXIT
   trap 'cleanup; exit 130' INT TERM

   # Split the stored connection string once, the same way backup_verify.py does: the
   # non-secret parts into the environment, the password into a mode-600 password file.
   # Neither the password nor the full URI ever reaches a command line. This runs before
   # the key is asked for, so a configuration problem cannot leave a key lying in a file.
   eval "$(.venv/bin/python - "$PASSFILE" <<'PY'
   import sys
   from pathlib import Path
   from psycopg.conninfo import conninfo_to_dict
   p = conninfo_to_dict(Path("~/.config/senebiclabs/backup-db.url").expanduser().read_text().strip())
   esc = lambda v: str(v).replace("\\", "\\\\").replace(":", "\\:")
   f = Path(sys.argv[1])
   f.write_text(":".join(esc(p.get(k, "*")) for k in ("host", "port", "dbname", "user"))
                + ":" + esc(p["password"]) + "\n")
   f.chmod(0o600)
   print("export " + " ".join(f"{k}={v}" for k, v in (
       ("PGHOST", p["host"]), ("PGPORT", p.get("port", "5432")),
       ("PGDATABASE", p.get("dbname", "postgres")), ("PGUSER", p["user"]),
       ("PGSSLMODE", p.get("sslmode", "require")), ("PGPASSFILE", sys.argv[1])) if v))
   PY
   )"
   # An empty eval means the python above failed, and PG* is unset. Stop before asking for
   # a key rather than after.
   [ -n "${PGPASSFILE:-}" ] || { echo "could not read the connection settings" >&2; exit 1; }

   read -rsp 'operator key: ' K; echo     # builtin: no process, no argv, no echo
   printf '%s' "$K" > "$KEYFILE"          # builtin, tmpfs: never on disk, never in argv
   unset K

   # The heredoc is quoted, so the shell expands nothing in it: the backtick is psql's own
   # and runs there. That is why the path is written out rather than "$KEYFILE".
   psql -q -v ON_ERROR_STOP=1 <<'SQL'
   \set k `cat /dev/shm/opskey`
   select vault.create_secret(:'k', 'ops_monitor_admin_key',
                              'Operator key used by ops_monitor_tick()') is not null as stored;
   SQL
   rc=$?
   if [ "$rc" -ne 0 ]; then
     echo "FAILED (psql exit $rc). Treat this key as logged: revoke it and mint a new one." >&2
     echo "Do NOT run this again with the same key." >&2
   fi
   exit "$rc"
   ```

   **If it fails, the key is burnt. Do not retry with the same one.** The statement that
   raised has already been written to the Postgres log with the key in it, so retrying
   stores a credential you know to be logged. Instead: revoke it
   (`DELETE /project/admin/operators/{id}`), find out why the call failed, mint a new key,
   and run this again. The most likely cause is that the name is already taken, which
   `select name from vault.secrets` answers without decrypting anything.

   **The residual risk this does not remove, stated plainly.** `vault.create_secret` takes
   the key as a SQL literal and psql does not bind parameters, so the key is in the
   statement text sent to the server. That text is exposed in three ways this procedure
   cannot close:

   - **if it errors, it is logged**, as above;
   - **while it runs**, the full text is in `pg_stat_activity.query`, readable by any role
     that can see other sessions' queries, which on this project includes `postgres`
     through `pg_monitor`. The window is milliseconds, but it is not zero;
   - **the settings above are today's settings**, verified once. Anyone setting
     `log_statement = 'all'`, or a short `log_min_duration_statement`, makes the successful
     path log the key too, and nothing here would notice.

   This procedure keeps the key out of shell history, out of every process's argument list
   and off the disk. It does not and cannot keep it out of the statement text. The reason to
   use it anyway is that the SQL editor's exposure is worse and permanent.
3. **Apply migration 013.** It refuses, with nothing applied, if `pg_cron` or `pg_net` is
   missing, if 012 has not been applied, if the Vault secret is absent, or if it **can see**
   that `net` or `vault` has been added to the Data API's exposed schemas. It switches the
   monitor on last, so a refusal leaves it off.

   **Read that last condition carefully, because it is weaker than it sounds.** The check
   depends on Supabase storing `pgrst.db_schemas` as a role setting, and on the production
   project it does not: there is no such setting in any scope (checked 2026-11-01), so the
   check establishes nothing and 013 installs anyway, printing a notice that says so. A
   successful installation is therefore **not** evidence that those schemas are unexposed.
   Step 1 of the smoke test below is what actually answers it, by hand, and the activation
   record is where that answer is written down.

   It is also **install-time only** even when it does work. Nothing re-checks it: exposing
   either schema later is a dashboard setting no migration and no scheduled check here will
   notice, which is why it is on the by-hand watch list further down.

### The activation smoke test

Two things in this design can only be checked on the real project, because `pg_net` and
`supabase_vault` cannot be installed on a throwaway Postgres and the SQL tests stub their
interfaces. **Neither has been tested against Supabase.** Run these once after activating,
before trusting the monitor. Everything here is read-only except the ticks pg_cron runs
itself.

1. **Neither `vault` nor `net` is exposed through the Data API.** Do not re-check the Vault
   grant: that is already answered and cannot be changed. `service_role` **can** read
   `vault.decrypted_secrets` (checked 2026-11-01), by a direct Supabase grant `postgres`
   cannot revoke. Check the thing that keeps it harmless instead.

   Read every applicable row, not one of them, because a role has a cluster-wide row and a
   per-database row and either may carry the setting:

   ```sql
   select r.rolname as role,
          case when s.setdatabase = 0 then '(cluster-wide)' else d.datname end as scope,
          e as setting
     from pg_db_role_setting s
     join pg_roles r on r.oid = s.setrole
     left join pg_database d on d.oid = s.setdatabase
    cross join lateral unnest(s.setconfig) as e
    where e like 'pgrst.db_schemas=%'
    order by r.rolname, scope;
   ```

   **Three outcomes, and only one of them is a pass:**

   | What came back | Verdict | What to do |
   |---|---|---|
   | One or more rows, and neither `vault` nor `net` appears in any of them as an exact comma-separated token | **PASS** | Proceed. Compare tokens exactly: `network_logs` and `vaults` are different schemas and not hits |
   | **No rows** | **UNKNOWN — not a pass** | PostgREST is configured from the platform, not from a role setting, so the database cannot answer and 013's guard verified nothing. **Confirm by hand in Supabase Dashboard → Settings → API → Exposed schemas**, then record it in the checklist below. This is the state of production as of 2026-11-01 |
   | `vault` or `net` appears in any row | **STOP** | Do not activate. Remove it from Settings → API → Exposed schemas first, then re-run this query. 013 refuses to install while it can see either one, but see the row above: it cannot always see |

   An empty result is the easiest thing here to misread as a clean bill of health. It is the
   opposite: it means nothing was checked.

   Re-run this whenever the Data API's exposed schemas change. 013's guard is an
   install-time check and nothing re-checks it.
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

### The activation record

Fill this in when you activate, in this file, in the commit that activates it. Step 1 is the
only one here a query cannot settle on its own, so it is the only one that needs a name
against it: an unattributed "confirmed" is not a record of anything.

| | Checked | By | Date | Result |
|---|---|---|---|---|
| Exposed schemas, from the database | | | | PASS / UNKNOWN / STOP |
| Exposed schemas, **by hand** in Dashboard → Settings → API | | | | required when the row above is UNKNOWN |
| `pg_net` installed and its worker answering | | | | |
| Secret created, and the call did **not** error | | | | an error logs the key: see step 2 of "Switching it on" |
| `last_reported_status` is `ok`, not null | | | | null means something answered that was not ops-health |
| `ops-health` reports `ops_monitor_stalled` as `ok` | | | | |

The dashboard row exists because the database cannot answer that question on this project
and 013 installs anyway. Whoever signs that row is the only check that happened.

**What the reply is exposed to.** `net._http_response` holds the whole body of each reply,
and an `ops-health` body carries operational identifiers: project ids, task numbers and
event ids. No clinical content, no clinician names, no client URLs and no secrets. The tick
deletes the row as soon as it has read it, and a monitor switched off releases a reply it
will never collect, so the window is one tick rather than pg_net's six hour prune. It lives
in the same `net` schema under the same grants as the queue below.

**The Vault grant, which is not ours to remove.** Checked read-only against production on
2026-11-01:

```
has_table_privilege('service_role','vault.decrypted_secrets','select')  ->  true
has_table_privilege('anon',        'vault.decrypted_secrets','select')  ->  false
```

Supabase grants `service_role` **SELECT and DELETE** on that view when it creates the
extension. It is a direct grant from `supabase_admin`, not something inherited through role
membership, and `postgres` cannot revoke a grant it did not make: the statement raises
nothing and removes nothing, so it looks as though it worked. Supabase documents this and
says that if a role must not be able to read a credential at all, the credential should not
be in Vault.

**The monitor's key stays in Vault anyway**, and this is why that is the right call rather
than a resigned one:

- `service_role` is `NOLOGIN`. The service key is a JWT against the Data API, which only
  reaches exposed schemas, and `vault` is not one. **A holder of the service key cannot read
  this secret.** The privilege is standing, not reachable.
- the alternative, a private schema no API role can reach, puts a **live operator key in
  plaintext into every `pg_dump`**. `operators` stores only a SHA-256 hash, so the dumps in
  `~/senebiclabs-backups` contain no usable credential today. Vault's root key is held
  outside the database by Supabase, so a captured dump of `vault.secrets` yields ciphertext.
  Moving the key would trade a privilege nothing can currently exercise for a plaintext
  credential that anyone holding a backup file can.

So the control is not the grant, it is **keeping `vault` out of the Data API**. Migration 013
refuses to install while either `vault` or `net` is exposed, and names whichever it found.

**That is an installation-time check and not monitoring.** It runs once, when 013 is applied.
Nothing re-checks it: exposing either schema afterwards is a dashboard setting that no
migration, no scheduled job and no check in `ops-health` will notice. It is on the by-hand
watch list below, and it is the one item there that would turn a standing privilege into a
live credential leak.

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
- **whether `net` or `vault` has been added to Settings -> API -> Exposed schemas.** This
  is the one item on this list that no code here will ever notice. Migration 013 refuses to
  install while it can *see* either one, but on this project it cannot see at all: there is
  no `pgrst.db_schemas` role setting in any scope, so the check reports UNKNOWN and installs
  anyway. Even where it works it runs once, at install time.

  The two schemas fail differently, and `net` is the broader of the two:
  - `net` exposed: `anon` and `authenticated` can read `net.http_request_queue`, which
    holds the monitor's key in a request header until the worker sends it. pg_net's grants
    are to `PUBLIC` and `postgres` cannot revoke them;
  - `vault` exposed: `anon` and `authenticated` have no `USAGE` on `vault` (checked
    2026-11-01), so this one reaches `service_role` only. That is still the service key,
    which holds a direct SELECT and DELETE grant on `vault.decrypted_secrets` that
    `postgres` cannot revoke, so it could read and delete every secret in the Vault.

  Use the query and the three-outcome table in "The activation smoke test" above, which
  handles the empty result properly. Re-check it whenever the exposed schemas change.
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
