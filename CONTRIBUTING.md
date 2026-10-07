# Working on Senebiclabs

Everything you need to go from a clone to a passing test run. Read `README.md` first for
what the system does; this is how to work on it.

---

## The two repositories

| Repo | What it is | Runs on |
|---|---|---|
| **HEALTH** (this one) | The system of record. Project creation, ingestion, consensus, adjudication, scoring, delivery. FastAPI. | Cloud Run |
| **workforce** | The clinician-facing application. Task UI, auth, pools, second reading. Next.js. | Vercel |

They share one Supabase database and one Label Studio instance. Neither calls the other's
internals; they meet at the database and at a small number of HTTP endpoints.

**The rule that keeps them apart:** the clinician experience belongs to `workforce`, and the
record of what was decided belongs here. If you are adding a screen a clinician uses, it goes
in the other repo.

---

## Setup

You need **Python 3.10** and **Node 20+**.

```bash
git clone <this repo> && cd HEALTH

python3.10 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
```

### Run the tests first

Do this before anything else. The suite is hermetic: no database, no Label Studio, no
network, no credentials.

```bash
PYTHONPATH=. pytest -q tests --ignore=tests/e2e
```

176 tests, about 8 seconds. If that passes, your environment is correct and **you can write
and test most changes without any credentials at all.** `tests/e2e` is excluded because it
drives a real browser against a running server.

### Run the API

```bash
uvicorn app.main:app --reload --port 8000
# http://localhost:8000/docs  (interactive OpenAPI)
```

The API starts without credentials, but any route touching the database returns 503 until
`.env` is filled in. See the table below.

### Run the site

```bash
cd ui && npm install && npm run dev
# http://localhost:3000
```

---

## Environment

| Key | Needed for | Where it comes from |
|---|---|---|
| `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` | Everything that reads or writes the record | Supabase project settings |
| `LS_URL`, `LS_TOKEN` | Pushing tasks to clinicians, pulling their answers | Label Studio account page |
| `LS_WEBHOOK_SECRET` | Verifying the callback Label Studio makes to us | Shared secret, set on both sides |
| `ADMIN_API_KEY` | Admin and operator endpoints | Chosen by us; operator keys are issued from it |
| `RESEND_API_KEY`, `FROM_EMAIL`, `ADMIN_EMAIL` | Enquiry and delivery email | Resend dashboard |

Ask for credentials rather than creating your own against production.

---

## Where things live

```
app/
  api/v1/
    project.py      Project intake, ingestion, results, admin, operators. The big one.
    ls.py           Label Studio webhook and pull. How clinician answers get in.
    system.py       Health check.
  services/
    report.py       Scoring. Accuracy, per-class metrics, critical misses, assurance.
    labelstudio.py  Form generation and the Label Studio API.
    templates.py    The 17 outcome templates clients pick from.
    second_reading.py  Bridge to the workforce platform. It owns the loop; we only ask.
    operators.py    Per-operator admin keys and the audit trail.
    paging.py       fetch_all. PostgREST truncates at 1000 rows; always page.
ui/             Next.js: public site, /docs reference, client portal
docs/           api.md and the operational runbooks
tests/          24 files. Start with test_report.py to understand scoring.
scripts/        data quality and one-off ops
migrations/     SQL, applied by hand against Supabase
```

---

## Conventions

**Commits** describe the intent in a sentence, in the imperative, no prefix:

> `Build a confusion matrix only when both sides are categories`
> `Say what is guaranteed, not how the work is staffed`

Not `fix: matrix bug`. Commit as yourself; no AI co-author lines.

**Changing the API means changing three things together:** the code, `docs/api.md`, and
`ui/app/docs/`. They are allowed to be wrong, but never to disagree.

**Tests come with behaviour.** Every service in `app/services` has a test file; add to it.
The suite is hermetic by design, so keep it that way: use `tests/_fakedb.py` rather than a
real database.

---

## Deploying

**The site** deploys itself: push to `main` and Vercel builds it.

**The API** is deployed by hand, from a local machine, not Cloud Shell:

```bash
gcloud run deploy senebiclabs-api --source . --region us-central1 --timeout=3600
```

Check `https://api.senebiclabs.com/api/v1/health` afterwards.

---

## Things that will catch you out

**PostgREST truncates every query at 1000 rows.** Use `services/paging.py: fetch_all`. This
has bitten us more than once and it fails silently, with a short answer rather than an error.

**Bulk writes need `default_to_null=False`**, or a batch of rows with differing keys fills the
gaps with NULL instead of defaults.

**Config is stored as JSON, which does not preserve key order.** That is what `field_order`
exists for. Without it a clinician can be asked for a rationale before the verdict it explains.

**Label Studio is stopped between client projects** to save money, so
`annotate.senebiclabs.com` being down is usually expected rather than broken. Ask before
assuming an outage.

**The Label Studio UI returns 404 on purpose.** Caddy serves `/api/*` only. Clinicians work
through the workforce platform; nobody uses the Label Studio interface.

---

## Asking

Open the question early rather than guessing at intent. The parts of this system that look
over-engineered — consensus, adjudication, provenance, isolation — are the product, and
simplifying them usually means removing the thing a client is paying for.
