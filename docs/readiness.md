# Readiness: can we take a client?

**Verdict, 4 November 2026: yes for a text based evaluation. Not yet for imaging.**

This page is the current answer to that one question. It replaces reading
`docs/native-task-store-production-readiness.md`, which was a gate review written on
24 October and has since gone partly stale: Label Studio is now fully retired, and two
defects it raised are fixed. That gate review and its companion
`docs/label-studio-replacement-audit.md` are deliberately **not committed**; they exist only
in the working tree on the operator's machine, which is why they are named here rather than
linked.

Every row below carries the evidence it rests on and the command that re-checks it. When a
claim here cannot be re-verified by running something, it says so. Re-run
[the verification block](#re-verifying-this-page) before quoting this page to anyone.

---

## 1. Verified live

Checked 4 November 2026 against production, not against the documents.

| What | State | Re-verify |
|---|---|---|
| API | Serving `8384fe0`, which is exactly `origin/main` | `curl -s https://api.senebiclabs.com/api/v1/health` |
| Clinician app | 200 | `curl -o /dev/null -w '%{http_code}' https://app.senebiclabs.com/` |
| Site and reference | 200 | `curl -o /dev/null -w '%{http_code}' https://senebiclabs.com/docs` |
| Scheduled ops check | Green on every run, every 10 minutes, for days | `gh run list --workflow ops --limit 20` |
| Hermetic suite | 517 passed, 4 skipped, about 13 seconds | `pytest -q tests --ignore=tests/e2e` |
| Database backup | `db-20261024T151600Z.dump`, verified: 21 tables and 296 rows matched on restore | `scripts/backup_verify.py` |

**The ops check is worth more than it looks.** It now exits non-zero when it is not
configured, instead of skipping, so a green run proves it genuinely read `ops-health`. The
earlier version returned success when the secrets were unset, which is why the Actions tab
showed a watched platform for weeks while nothing had ever been read. A green `ops` run is
now real evidence.

### Label Studio is gone, not just unused

| Layer | State |
|---|---|
| Schema | `migrations/008_remove_label_studio.sql` applied |
| Application code | Nothing in `app/` calls it; no `/ls/*`, no `labelstudio.py`, no `LS_*` settings read |
| DNS | `annotate.senebiclabs.com` is `NXDOMAIN` |
| Still present | `docker/labelstudio/` (local development compose only), and stale `LS_URL`, `LS_TOKEN`, `LS_WEBHOOK_SECRET` entries in the local `.env` |

### Two defects from the October audit are fixed

| Audit finding | State now |
|---|---|
| The `number` field type was silently dropped, so `first_failed_step` never reached a result | Handled: `wf:lib/eval-config.ts:460` |
| Context blocks rendered as plain text, so an image task showed its signed URL as a string | Rendered: `wf:app/workspace/page.tsx:555` |

---

## 2. What we can sell today

**Text based evaluation: yes.** Grading a model's written output against a clinician
rubric, consensus with adjudication, second reading on authoring, scored delivery. This is
the path the platform has exercised end to end.

**Imaging: not yet.** The viewer code exists and is covered by a rehearsal, but the
workforce app re-signing a private bucket image for a clinician has never run against real
Supabase Storage. In every test Storage is faked. Until that is exercised once, an imaging
scope is a promise without evidence behind it.

Use the text template for the first pilot, as
[release-runbook.md](release-runbook.md) section 6 already instructs.

---

## 3. Open before a client's real data lands

Technical, and none of it blocks signing a text based pilot.

- [ ] **Full disaster recovery has never been rehearsed.** The backup is verified, which is
      not the same claim: restoring into a fresh project with both apps serving from it has
      not been tried. A verified backup proves the dump restores and matches. It does not
      prove you can be running again afterwards.
- [ ] **Portal image upload and read against real Storage.** Never exercised.
- [ ] **The clinician's image view against real Storage (W3).** Never exercised. This is
      what gates imaging work.
- [ ] **Retention decision on the 31 orphaned `client-images` objects.** They are real
      client uploads with no decision attached. They are copied and re-verified, 12,655,011
      bytes, all readable. Blocks nothing technically.
- [ ] **The production pilot and its 7 day soak.** The functional criteria are rehearsed
      (section 6 below), but a pilot on production with real internal clinicians, followed by
      7 days of green scheduled checks, has not been run.

Two limitations worth stating plainly, because they are permanent rather than pending
properties of the design:
the database and the images are not one atomic snapshot (Storage and Postgres cannot be
captured together), and a restore proves the `public` schema only, since Supabase recreates
its own managed schemas.

---

## 4. Gates that are not software

From [pilot-runbook.md](pilot-runbook.md) section 0. None of these are platform work, and
on current evidence they are the real constraint.

- [ ] Company registered, and the pilot agreement signed by the company
- [ ] NDA signed, and the client's Transparency Report received
- [ ] 50% invoiced **and received**
- [ ] Clinical lead and reviewers contracted: confidentiality flowing down from the NDA,
      work assigned to the company, independence from the client declared, rate in writing
- [ ] Each operator holding their own key, not the root key
- [ ] Supabase backups on, and a backup verified
- [ ] You have personally worked one full loop: judge, adjudicate, deliver, read the report

Scope in writing before any of it: how many cases, what the deliverables are, the date, and
what a re-test includes.

---

## 5. Infrastructure to close out

| Item | State | Action |
|---|---|---|
| Azure | DNS for the VM is gone, so nothing serves from it | **Before 15 November:** confirm in the portal that the VM, its disk and its snapshots are actually deleted. A stopped VM still bills for its disk, and the credit ends that day |
| GCP | Project active, billing disabled, Cloud Run service still listed, 5 alert policies enabled for retired resources | Delete the policies, then decide whether to delete the project |
| Secrets | `LS_*` still in the local `.env`, and possibly still set on Render and Vercel | Remove from both platforms, then rotate anything that was shared |
| Local containers | Two Label Studio containers idling on the development machine | `docker rm -f labelstudio-label-studio-1 labelstudio-ls-db-1` |
| Backup scripts | `~/senebiclabs-backups/ls-vm.sh` and `pilot-preflight.sh` target the retired GCP VM; `backup.sh` has a Label Studio step | Update or retire |

---

## 6. Test coverage, and where it stops

The acceptance criteria in [release-runbook.md](release-runbook.md) section 6 are covered as
follows. "Rehearsed" means it ran against the throwaway stack: both real applications, real
Postgres behind PostgREST as Supabase serves it, driven over HTTP.

### Rehearsed 4 November 2026

The whole acceptance loop, run on the throwaway stack. Nothing in production was touched.

| Suite | Result |
|---|---|
| `tests/e2e/test_native_cutover.py` | 9 passed, 48s |
| `tests/e2e/test_reservation_integration.py` | 22 passed, 124s |
| `tests/sql` | 318 passed, 68s |
| `tests/e2e/test_reservation_browser.py` (Chromium) | 7 passed, 55s |
| `tests/e2e/test_workforce_auth_gate.py`, `tests/e2e/test_submit_form.py` | 5 passed, 1 skipped, 18s |

The skip is deliberate and should stay skipped: it creates a real lead and sends real email,
and is opt-in behind `RUN_SUBMIT_TEST=1`.

**Bring the stack up with `HEARTBEAT_INTERVAL_MS=3000` before the browser reservation
suite.** Without it the heartbeat runs at its production interval, no renewal happens inside
the window the tests watch, and three of them fail in a way that reads exactly like a broken
lease. It is a harness setting, not a defect. The suite's own docstring says so, and it is
repeated here because the failure is convincing enough to send you looking in the wrong
place.

Also beware of wrapping a suite in a shell subshell that ends on `echo`: the exit status
becomes the echo's, and failures pass silently. Read the log, not the status.

| Criterion | Covered by |
|---|---|
| Each clinician served, and submits | `tests/e2e/test_native_cutover.py` |
| A deliberate split held and settled by the senior | `tests/e2e/test_native_cutover.py` |
| Second reading approves | `tests/e2e/test_native_cutover.py` |
| A re-upload makes a new version and keeps the old answer | `tests/e2e/test_native_cutover.py` |
| An image shown through the gate and never as a URL | `tests/e2e/test_native_cutover.py` (faked Storage) |
| Scoring survives the API being unreachable, and catches up | `tests/e2e/test_native_cutover.py` |
| A failed client delivery retried once, same event id | `tests/e2e/test_native_cutover.py` |
| Invalid answers refused with nothing stored | `tests/e2e/test_native_cutover.py` |
| The reviewer ceiling holds | `tests/e2e/test_reservation_integration.py`, `tests/sql/`, `tests/test_native_tasks.py` |
| No clinician identity in `/results` | `tests/test_operators.py:142` (hermetic) |

**Where CI stops.** `.github/workflows/tests.yml` runs the hermetic suite only, on both
3.10 and 3.11, and deliberately ignores `tests/e2e` because those drive a real browser and a
real server. Its path filters cover `app/**`, `tests/**`, `scripts/data_quality.py`,
`supabase_schema.sql` and `requirements-ci.txt`. They do **not** cover `ui/**`, so:

- the `/docs` browser suite (`tests/e2e/test_docs_reference.py`, 46 tests) never runs in CI;
- `npm run test:docs` (26 highlighter tests) never runs in CI;
- a change to `ui/` alone triggers no workflow at all.

`tests/test_docs_palette.py` is hermetic, so the palette guard does run on every push.

---

## Re-verifying this page

Public, read only, no credentials:

```bash
curl -s https://api.senebiclabs.com/api/v1/health            # commit should equal origin/main
curl -o /dev/null -w '%{http_code}\n' https://app.senebiclabs.com/
curl -o /dev/null -w '%{http_code}\n' https://senebiclabs.com/docs
gh run list --workflow ops --limit 20                        # every run green
pytest -q tests --ignore=tests/e2e                           # hermetic suite
```

The full rehearsal, which needs Docker, psql and Node 22 or newer, and touches nothing in
production:

```bash
HEARTBEAT_INTERVAL_MS=3000 scripts/native_e2e_stack.sh up

export NATIVE_E2E_API=http://127.0.0.1:8765 NATIVE_E2E_APP=http://localhost:3765 \
  NATIVE_E2E_DSN=postgresql://postgres:test@127.0.0.1:55432/senebi_e2e \
  NATIVE_E2E_ADMIN_KEY=e2e-admin-key \
  NATIVE_E2E_JWT_SECRET=e2e-jwt-secret-long-enough-for-the-min-0123456789 \
  NATIVE_E2E_STACK=scripts/native_e2e_stack.sh PYTHONPATH=.

pytest tests/e2e/test_native_cutover.py -p no:playwright
pytest tests/e2e/test_reservation_integration.py -p no:playwright
pytest tests/e2e/test_reservation_browser.py --browser chromium
SQLTEST_DSN=postgresql://postgres:test@127.0.0.1:55432/postgres pytest tests/sql

scripts/native_e2e_stack.sh down
```

Never point that suite at production. It creates clinicians, projects and sessions.

---

## When to update this page

On any of: a production deploy that changes behaviour, a migration, an item in section 3 or
5 closing, a change to what we can sell, or a new gap found. Put the date in the verdict line
and keep the evidence columns honest. A readiness page nobody trusts is worse than none,
because it gets quoted anyway.
