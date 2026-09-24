# Pilot runbook — delivering a client evaluation, start to finish

Written for the operator running a paid pilot (currently: you). It assumes the Smart
Reports shape — clinicians grade a model's lab-report interpretations — but the sequence is
the same for any evaluation.

Two projects run in a pilot like this:

| Project | Purpose | Who works it | Second reading |
|---|---|---|---|
| **Case authoring** | Clinicians write the test cases: the report, the expected interpretation, the correct urgency | 1 author per case | Yes — a second clinician approves |
| **Grading** | Clinicians judge the client's model output for each case | 3 per case | No — consensus + adjudication instead |

---

## 0. Gates — none of this starts until every line is true

- [ ] Company registered; pilot agreement signed by the company
- [ ] NDA signed, and the client's Transparency Report received
- [ ] 50% invoiced and **received**
- [ ] Clinical lead + reviewers contracted: confidentiality flowing down from the NDA,
      work assigned to the company, independence from the client declared, rate in writing
- [ ] `migrations/004_operators.sql` applied; each operator has their own key
- [ ] Supabase backups on; Label Studio snapshot verified
- [ ] You have personally worked one full loop (judge → adjudicate → deliver → read report)

Scope, in writing, before anything else: how many cases, what the deliverables are, the
date, and what a re-test includes. Everything below assumes that document exists.

---

## 1. Set up the projects

Create with the client's API key (`$KEY`), operate with your operator key (`$ADMIN`).

```bash
# 1a. Case authoring — clinicians WRITE the cases
curl -X POST "$API/projects" -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"name": "<Client> — case authoring", "template": "benchmark_creation"}'
# second_reading and review_required default ON for authoring. Confirm in the response
# project's config before continuing: without review_required the pool will not run the
# author → reviewer flow and delivery will block on items nobody can approve.

# 1b. Grading — clinicians JUDGE the client's outputs
curl -X POST "$API/projects" -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"name": "<Client> — <module> v1",
       "template": "clinical_safety_eval",
       "classes": ["Normal","Borderline","Abnormal — non-urgent","Abnormal — urgent","Critical"],
       "model_version": "v1"}'
```

Keep the **name short**: the Label Studio title is built from it and is capped at 50
characters. Long names are truncated, never rejected, but short names read better.

Then ingest the topics (authoring) or the cases (grading):

```bash
curl -X POST "$API/ingest" -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: <unique-per-batch>" \
  -d '{"project_id": "...", "items": [ ... ]}'
```

Grading items carry: `case_id`, `scenario` (the report), `prediction` (the model's
interpretation), `triage` (the model's urgency), `expected_triage`, `clinical_domain`,
`case_type`.

Wait until `GET /results?project_id=…` shows `total` equal to the number of items. That
means the cases reached Label Studio.

---

## 2. Create the pool clinicians work from

Pools are created by hand, in Supabase, one per project. **Closed by default.**

```sql
insert into pools (name, ls_project_id, calibration_items, eval_config,
                   maximum_annotations, open_access)
select '<Client> — <module>', s.ls_project_id, '[]'::jsonb, s.eval_config,
       coalesce((s.eval_config->>'reviewers_per_item')::int, 3), false
from project_submissions s where s.id = '<project_id>';

-- then, per clinician assigned to this pool:
insert into pool_eligibility (clinician_id, pool_id, eligible)
values ('<clinician_id>', '<pool_id>', true);
```

Checks:

- [ ] `open_access` is **false** — otherwise every new clinician is auto-granted the pool
- [ ] `eval_config` copied from the project, so the rubric and `review_required` match
- [ ] Only the clinicians meant to see this client's work are eligible

---

## 3. Calibration round — before any paid client cases

Ten cases, judged independently, then discussed with the clinical lead.

- [ ] Every disagreement talked through, and the outcome written into the rubric
- [ ] Rubric updated via `POST /admin/eval-config` — this also pushes the new form to
      clinicians. The response says "The clinicians' form was updated"; if it warns
      instead, retry before anyone works another case
- [ ] Record minutes per case and the flag rate

If the panel disagrees on more than about a third of cases, the rubric is the problem, not
the clinicians. Fix it here — it is far cheaper than fixing it at delivery.

---

## 4. The run

**Daily (10 minutes):**

```bash
curl "$API/admin/progress?project_id=…" -H "X-Admin-Key: $ADMIN"          # done / total
curl "$API/admin/adjudication/<project_id>" -H "X-Admin-Key: $ADMIN"      # splits waiting
```

- [ ] Progress moving? If not, message the group — a stalled pool is a stalled pilot
- [ ] Adjudication queue growing? Book the discussion call

**Twice a week: the discussion call.** Go through the adjudication queue with the lead.
Cases are referred to by `case_id` only; everyone opens the case in the workspace. Record
each resolution:

```bash
curl -X POST "$API/admin/adjudicate" -H "X-Admin-Key: $ADMIN" -H "Content-Type: application/json" \
  -d '{"project_id":"…","idx":<idx>,"final_label":{"verdict":"Incorrect","severity":"Critical"},
       "note":"Panel: potassium 6.9 is a critical value whatever the narrative says."}'
```

Then put the generalised version of that reasoning into the rubric.

**Weekly, to the client:** cases done of total, how many needed panel review, anything you
need from them. Three lines. Never send findings before delivery.

**If annotations stop arriving** (progress frozen while clinicians say they are working):

```bash
curl -X POST "$API/ls/pull" -H "X-Admin-Key: $ADMIN" -H "Content-Type: application/json" \
  -d '{"project_id":"…"}'
```

That pulls everything from Label Studio directly and settles any second readings waiting.

---

## 5. Deliver

- [ ] `done == total` in `/admin/progress`
- [ ] Adjudication queue empty
- [ ] For authoring projects: every item approved by a second clinician
- [ ] You have read the report yourself, and it matches what the panel found

```bash
curl -X POST "$API/admin/advance" -H "X-Admin-Key: $ADMIN" -H "Content-Type: application/json" \
  -d '{"submission_id":"…","stage":"delivered"}'
```

A `422` here is the platform refusing to ship unfinished work: read the message, it names
what is outstanding. Then:

```bash
curl "$API/results?project_id=…" -H "Authorization: Bearer $KEY"     # the client's exact view
```

Send the client the pass rate, the severity breakdown, the failure types, the per-specialty
split, and the full case list with reasoning. Invoice the remaining 50%.

---

## 6. Keep the failures, and gate the next release

```bash
curl -X POST "$API/failures/capture"   -H "Authorization: Bearer $KEY" -d '{"project_id":"…"}'
curl -X POST "$API/benchmark/promote"  -H "Authorization: Bearer $KEY" -d '{"min_severity":"High"}'
curl -X POST "$API/benchmark/run"      -H "Authorization: Bearer $KEY" -d '{"model_version":"v2"}'
# after the re-run is reviewed and delivered:
curl "$API/compare?baseline=<compare_with>&candidate=<new_project_id>" -H "Authorization: Bearer $KEY"
```

`verdict.recommendation` is **block**, **review** or **pass**, and `still_failing_serious`
counts known failures that remain. A "pass" with that above zero means nothing new broke —
not that the model is safe. Say so when you present it.

---

## 7. Numbers to record every pilot

These set the next quote, so keep them in one sheet:

| | Why |
|---|---|
| Minutes per case, authoring and grading | The price is built from this |
| Flag rate | Too high means the case set or rubric is unclear |
| Disagreement rate | Too high means the rubric is unclear; near zero means the cases are too easy |
| Clinician hours and amounts paid | Margin, and next pilot's cost base |
| Calendar days per phase | What you can promise next time |

---

## When something goes wrong

| Problem | What to do |
|---|---|
| Cases never appear for clinicians | `GET /results` re-triggers the sync. If still empty, check the project has a pool and the clinician is eligible |
| A clinician goes quiet | Reassign: the queue offers their unfinished cases to others automatically. Chase separately |
| The rubric needs changing mid-run | `POST /admin/eval-config`, confirm the form was updated, tell the panel what changed, and note it — cases judged before the change were judged under the old wording |
| Label Studio is unreachable | Work stops; nothing is lost. The VM has daily snapshots and a tested restore |
| A client asks for findings early | Don't. Findings before the panel has finished are half-formed and will be quoted back at you |
| The deadline is slipping | Tell the client in week two, not week four, and offer a reduced case count rather than a late delivery |
