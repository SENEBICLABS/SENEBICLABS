# Eval Operations — onboarding a client (operator runbook)

How to take a new evaluation client (e.g. X-ray classification review) from first contact to
delivered, radiologist-reviewed results. **A new client onboards via config, not code.**

---

## The full lifecycle at a glance

The whole path, customer bookends included. The customer touches only steps 1 and 9;
everything between is operator-driven. **Steps 2–5 are deliberately manual for now** — do
them by hand for the first client before automating anything.

| # | Who | What happens | Where |
|---|-----|--------------|-------|
| 1 | **Customer** | Books a demo (homepage → `/submit` → Calendly), or an API client is set up | `POST /project/submit` → `project_submissions` (stage `submitted`) |
| 2 | Operator | Scope the deal, advance the stage, set the project's `eval_config` (label set + fields + conditionals) | `POST /project/admin/advance`, set `eval_config` |
| 3 | Operator | Ingest the client's images: private per-client storage, de-identified keys, signed URLs | `scripts/ingest_xray.py` → `project_items` |
| 4 | Operator | Create reviewers and **assign** them to the project | `POST /project/admin/clinicians` + `project_clinicians` |
| 5 | Operator | Generate the LS config and push tasks | `POST /ls/sync` → Label Studio |
| 6 | Reviewers | Annotate (X-ray: in Label Studio; other types: in-app `/work/*` queue) | LS UI / `/work/next` + `/work/label` |
| 7 | Operator | Pull annotations back; structured fields collapse; every write is audited | `POST /ls/pull` (or `/ls/webhook`) → `project_items.label` + `audit_events` |
| 8 | Operator | QA: progress, export, and the audit trail (who labeled/reviewed, when) | `/project/admin/progress`, `/admin/export`, `/admin/audit/{id}` |
| 9 | **Customer** | Once stage is `delivered`, sign in and download results | `/portal/request` → `/portal/projects` → `GET /project/portal/results` |

**One-line pipeline:**

```
/evaluate -> /submit -> project_submissions
   -> (operator: eval_config; ingest_xray.py -> private storage -> project_items)
   -> (operator: clinicians + project_clinicians assignment)
   -> /ls/sync -> Label Studio  <- reviewers annotate
   -> /ls/pull (+ collapse structured) -> project_items.label + audit_events
   -> operator QA via /admin/audit, /admin/export
   -> stage=delivered -> /portal/results -> customer downloads
```

Isolation and audit guarantees hold only against the **deployed** instance running this
code. Before ingesting any real client data: **deploy, then re-run the isolation gate tests
against the deployed instance**, not just locally (config can drift between environments).

---

## 0. One-time schema migration

Before the first client, apply `supabase_schema.sql` in the Supabase SQL editor. The
pipeline depends on three additions:

- `project_submissions.eval_config` (jsonb) — the per-project labeling schema.
- `project_clinicians` (table) — which clinicians are assigned to which project.
- `audit_events` (table) — the append-only audit trail.

The gate tests (`tests/test_isolation.py`, `tests/test_audit.py`) refuse to pass until these exist.

---

## 1. Customer submits the project

The customer lands on `/evaluate` and submits via `/submit` → `POST /project/submit`, which
creates a `project_submissions` row at stage `submitted` and emails a notification. This is
the only self-serve step; you drive everything from here.

---

## 2. Create the project + its eval_config

Each project carries an `eval_config` describing what the reviewer sees and answers. The
labeling UI is **generated from this schema** (`build_label_config`), so a new client or a
new question set is a config change, never a code change.

```jsonc
{
  "title": "Chest X-ray classification review",
  "schema": {
    "classes": ["Normal", "Pneumonia", "TB", "Effusion"],   // the client's label set
    "multi_label": false,
    "fields": {
      "verdict":       { "type": "single", "options": ["Correct", "Incorrect", "Partially correct"], "required": true },
      "correct_label": { "type": "from_classes", "visible_when": "verdict!=Correct" },
      "critical_miss": { "type": "structured", "visible_when": "verdict!=Correct" },
      "radiologist_confidence": { "type": "scale", "min": 1, "max": 5 },
      "cannot_assess": { "type": "flag", "label": "Cannot assess" },
      "rationale":     { "type": "text" }
    }
  }
}
```

Field types: `single`, `from_classes`, `structured`, `scale`, `flag`, `text`. An invalid
config fails loudly (HTTP 422 on sync) rather than producing a broken labeling UI.

Set it on the row: `project_submissions.eval_config = <the json above>`. If a project has
no `eval_config`, sync falls back to the built-in task-type config (existing projects keep
working).

---

## 3. Ingest the client's images

```bash
python scripts/ingest_xray.py \
  --company "Abhishek Radiology" \
  --email   client@acme.com \
  --images  ./their_xrays \
  --predictions preds.csv \
  --ttl-days 7
```

`preds.csv` has a header `filename,prediction`. Images upload to a **private** bucket under
a per-client prefix (derived from `--email`) with de-identified (hashed) keys, and are
served to Label Studio via time-limited signed URLs.

> **Do not ingest real client X-rays until the isolation gate is green** (see §5). Demo /
> public PNGs only until then.

---

## 4. Assign clinicians (required — creating the project does not grant access)

Since Slice #2, **project creation alone grants nobody access.** A clinician sees and labels
a project only if they are assigned to it in `project_clinicians`. The operator (admin key)
sees everything.

```sql
insert into project_clinicians (project_id, clinician_id)
values ('<project-uuid>', '<clinician-uuid>');
```

Get clinician IDs from the `clinicians` table (create them via `POST /admin/clinicians`).
An unassigned work code gets `403` from every work endpoint.

---

## 5. Send to Label Studio, reviewers annotate, then pull results

- **Send:** `/admin` → select the project → *Send to Label Studio* (`POST /ls/sync`). Creates
  the LS project from the generated config and pushes pending items as tasks.
- **Annotate:** reviewers label in Label Studio (X-ray) or the in-app `/work/*` queue (other
  task types). The brief + LS link come from `GET /project/work/brief`.
- **Pull:** `POST /ls/pull` (or the LS webhook) writes annotations back to `project_items`;
  structured fields (e.g. `critical_miss`) collapse into one object, and every write records
  an `audit_events` row (actor, source, value snapshot).

---

## 6. QA and deliver (step 9 is the customer's)

- **QA (operator):** review `/project/admin/progress`, the raw `/project/admin/export`, and
  the full audit trail at `GET /project/admin/audit/{project_id}` — who labeled/reviewed each
  item, with what value, through which channel, and when. Corrections re-run `/work/label`
  (the operator can label anything) and land as `review` events; nothing is overwritten silently.
- **Build the client deliverable (the report):** `python scripts/report.py --project-id <id>
  --out-dir ./out` turns the pulled verdicts into the model-performance report — headline
  accuracy, per-class precision/recall/F1, confusion matrix, the critical-miss list, named
  failure cases, and an explicit exclusion/caveat section. Writes `.json` (full), `.md`
  (hand to the client), and `_cases.csv` (every case). Same data is available at
  `GET /project/admin/report/{project_id}`. Run `/ls/pull` first so verdicts are in the DB.
  Note: ground truth = the model's prediction on Correct cases, the corrected label on
  wrong cases; cannot-assess/unlabeled/incomplete cases are excluded and stated, not guessed.
- **Deliver:** advance the project to stage `delivered` (`POST /project/admin/advance`). The
  customer then requests a magic link (`/portal/request`), signs in (`/portal/projects`), and
  downloads via `GET /project/portal/results` — which enforces email ownership AND
  `stage == delivered` before returning any labels.

---

## 7. Isolation gate (must be green before real client data)

```bash
PYTHONPATH=. python3 tests/test_isolation.py
```

- **Gate A (storage):** a client cannot read another client's object — private-bucket
  objects are not world-readable; only the owner's signed URL serves them.
- **Gate B (scoping):** a work code cannot reach an unassigned project by passing its id
  (`403`), and an assigned clinician can.

Both must pass. If either is hard to make pass, stop — do not weaken the test.

---

## Isolation notes & known limits

- **Signed-URL TTL:** default **7 days** (`storage.DEFAULT_SIGNED_URL_TTL`, override with
  `--ttl-days`). URLs **do not auto-refresh** — if a radiologist stays on a case past the
  TTL the image link expires; re-run *Send to Label Studio* to regenerate fresh URLs.
- **De-identification scope:** covers **object keys / filenames only** (hashed). It does
  **not** strip DICOM headers or burned-in pixel PHI — out of scope until we add DICOM
  ingest. Only send data that is already free of burned-in identifiers.
- **RLS (future hardening):** isolation is currently enforced at the **application layer**
  (`_labeler_can_access`) plus a **private storage bucket** with signed URLs — not full
  Postgres row-level security. Per-tenant DB RLS is planned hardening, not yet in place.
- **Audit failures are silent by design (future hardening):** audit writes are best-effort
  so a hiccup never blocks a labeling save (see `app/services/audit.py`). When the audit
  trail becomes a client deliverable, add monitoring/alerting on the logged audit-write
  failures so a silent audit outage surfaces before a client notices a gap.
- **Case-id passthrough — DONE (was the MUST-FIX).** Each verdict now carries the client's
  own case/study id, not just our row index. To turn it on for a client: include their id
  column in the ingest mapping's `extra_columns` (e.g. `"extra_columns": ["study_id"]`) so it
  rides into `project_items.content`; the report surfaces it automatically for common names
  (`case_id`, `study_id`, `study_uid`, `accession`), or set `eval_config.case_id_field` to
  name it explicitly. `report.py` then keys every case, critical miss, and CSV row by that id,
  with `idx` kept as the internal fallback. Row-order integrity still holds
  (`tests/test_ingest_order.py`); the passthrough is covered by
  `tests/test_report.py::test_case_id_passthrough`. A real batch can now be delivered keyed
  to the client's identifiers.

---

## Future: the automated version (sketch — DO NOT build yet)

Captured while the manual friction is fresh. **Build only after the first pilot (Abhishek's
batch, run by hand) confirms these are the real chafe points** — not before. Today's flow is
deliberately white-glove: the client hands over files and the operator ingests. The clean
version removes the operator from the middle, in this priority order:

1. **Results keyed by the client's own IDs — DONE.** Every delivered verdict carries the
   client's study ID, so results drop straight into their system with no row-order dependency.
   Foundation for everything below.
2. **Self-serve image upload.** A portal upload that accepts image *files* (today `/portal/items`
   takes JSON rows only) → straight into the private per-client bucket with the same
   de-identification and stable indexing the script does now. Removes the out-of-band transfer
   link and the operator running `ingest_xray.py`. Shape: presigned per-client upload URLs +
   a manifest CSV upload, validated by `ingest.load_manifest` before any task is created.
3. **Read from the client's own bucket.** Instead of him uploading, he grants read access to
   his S3/GCS and we pull — the Scale-style intake. Matters once there is more than one client
   and transfers get painful.
4. ~~**Results via API / webhook.**~~ **DONE.** Clients can now integrate entirely by code:
   `POST /projects` (self-serve task config), `POST /ingest`, `GET /results` (poll), and a
   webhook on delivery. Long-lived Bearer API keys, issued from `/admin`. See the "API clients"
   section below and `docs/api.md` (hosted at `senebiclabs.com/docs`).

Guiding principle: match the **rigor** of the big platforms (de-id, isolation, audit, ID-keyed
results — largely built) before their **plumbing** (buckets, APIs, self-serve consoles). The
plumbing is bought back later with revenue; the rigor is the product.

---

## API clients (managed and self-serve)

For clients who integrate by code instead of the dashboard (e.g. Heyrafiki). Full reference:
`docs/api.md` / `senebiclabs.com/docs`. Base: `/api/v1/project`, auth `Authorization: Bearer <key>`.

- **Issue a key** — `/admin` → a project's **API key ⚙** button, or `POST /admin/api-key`
  with `{email}` for an account key before any project exists. The key is tied to the account
  email; one key can create and drive many projects.
- **Two ways to start:**
  - *Managed* — you create + configure the project in `/admin`, hand them the `project_id`.
  - *Self-serve* — they `POST /projects` with their own `eval_config`, get a `project_id`.
- **Push / pull** — client `POST /ingest` (items), then `GET /results` (poll: returns
  `status` + counts, and `report` + `items` once `delivered`) or a **webhook** on delivery.
- **The middle is automated for API clients.** Ingest **auto-syncs** the new items to Label
  Studio (creates the project from config, registers the LS webhook, pushes only the new
  items). As clinicians annotate, the LS webhook **auto-pulls** each annotation (multi-reviewer
  aware: consensus + agreement, done only at N reviewers). When the last item completes, the
  project is marked **ready for delivery and held for your sign-off** — nothing ships until you
  advance it to `delivered` (see "Quality assurance & delivery sign-off"). Set
  `eval_config.auto_deliver: true` to restore hands-off shipping. The only manual setup is:
  create the project (or the client self-serves via `POST /projects`) and **assign clinicians
  once**. Dashboard-run projects still use the manual buttons (steps 4–7 below).
- **Webhook storage** — a per-project `webhook_url` lives in `eval_config._webhook_url` and is
  preserved across config edits; it fires when the project is advanced to `delivered`.

---

## Book-a-demo intake (was: "soften the /submit intake")

**DONE.** The funnel is now "Book a demo": every CTA (`/`, footer, nav) points at `/submit`,
which is a Scale-style lead form (first/last name, work email, company, job title, use case).
On submit it saves the lead to `project_submissions` and forwards to Calendly
(`calendly.com/senebiclabs/30min`, name + email prefilled). The old "Start a pilot" wording and
the required detailed-brief field are gone.

---

## Quality assurance & delivery sign-off

The QA layer that makes an "approved" deliverable mean something. All of it is opt-in through
`eval_config`, so existing projects are unchanged until you turn a flag on. No schema migration
is needed — statuses and flags live in existing columns.

**`eval_config` flags:**

| Flag | Default | Effect |
|---|---|---|
| `adjudicate` | `false` | When multi-reviewers disagree (no majority), the item is held as `needs_adjudication` instead of shipping the majority vote. |
| `auto_deliver` | `false` | When every item is done, ship automatically. Off = **hold for human sign-off** (the safe default). |

Turn both on for a real paying client. Leave them off for low-stakes internal runs.

**Adjudication (when `adjudicate: true`):**

- A split item never enters the report — it is excluded as `awaiting-adjudication`, so an
  unresolved disagreement can never become a scored ground truth.
- `GET /project/admin/adjudication/{project_id}` — the queue: each held item with **every
  reviewer's own answer**, so you can see the split.
- `POST /project/admin/adjudicate` `{project_id, idx, final_label, note}` — a senior reviewer
  resolves it; the final answer replaces the split, the item is marked `done`, the reviewers'
  originals are preserved for audit, and the sign-off gate is re-checked.

**Delivery sign-off (the default):**

- When all items are done, the project is marked ready (`eval_config._ready_for_delivery`
  timestamp) and **nothing is shipped**. `GET /results` shows the client `in_review`.
- You deliver explicitly: `POST /project/admin/advance` `{submission_id, stage: "delivered"}`
  — which reality-checks that every item is done, then fires the client webhook.

**Free-text fields — vote the categories, never merge the prose:**

- Categorical fields (`single`, `from_classes`, `structured`, `scale`) are consensus-voted.
  **Free-text fields (`type: text`) are NOT** — two clinicians can write the same correction in
  different words, so a majority-vote would silently keep one and hide the rest. Instead, a
  multi-reviewed text field is surfaced as **every distinct version** (a list) in the item's
  label and the deliverable; a single/agreed version stays a plain string.
- This means: the **verdict/score is still a real consensus** (categorical), and the written
  correction/rationale is delivered **transparently, all versions shown** — never a hidden
  arbitrary pick.
- The *right* QA for written work is a **review pass, not a vote**: one clinician authors, a
  second approves or edits, the approved version ships (the author→reviewer / "write→approve"
  flow — a workforce serving feature). Don't multi-author free text expecting a merge; there
  isn't one.

**Per-reviewer quality (always available):**

- `GET /project/admin/reviewers/{project_id}` — each reviewer's **consensus agreement** and
  **gold accuracy**, with anyone under `REVIEWER_AGREEMENT_FLOOR` (0.7) flagged `below_floor`.
  Reviewers are keyed by their real identity: the workforce app writes every Label Studio
  annotation under one service user, so attribution is joined from `task_completions`
  (who-reviewed-what, the system of record) via each item's stored `_ls_task_id`. Projects
  reviewed before this join was in place fall back to the Label Studio-derived attribution.
- **Gold items:** seed known-answer items into the stream with `content._gold_expected`
  (a `{field: expected_value}` map). They are scored per reviewer automatically. Serving gold
  invisibly into the clinician stream is the workforce platform's job.

**Guideline lock (batch consistency):**

- Once a project has **any reviewed item** (a clinician has submitted), its **grading config is
  frozen** — `purpose`, `instructions` (the rubric), and `schema` (`fields`, `classes`,
  `context`, `input`, `case_id_field`, `media_key`). `POST /project/admin/eval-config` returns
  **409** if you try to change any of those, so a mid-batch edit can't silently split the data
  into items judged by different rules.
- **Still editable after serving starts:** operational settings — reviewer count, `adjudicate`,
  `auto_deliver` — and internal `_`-markers. Those don't change how an item is judged.
- **To change the rubric or layout mid-project:** start a **new batch/project**. Or, for a
  genuine fix (a rubric typo), override deliberately with `{"force": true}` on the eval-config
  call — it's allowed and logged, but it means later items were judged under a changed standard,
  so use it knowingly.
