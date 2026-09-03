# Senebiclabs — Clinician-Grade Data for Medical AI

**The data layer behind medical AI. Certified clinicians evaluate, correct, and create the
data that medical models are trained and measured against, with the consensus, adjudication,
and provenance that make it trustworthy enough to build on.**

**Live:** [senebiclabs.com](https://senebiclabs.com)

---

## The problem

A medical model is only as good as its data. In high-stakes domains that data cannot be
crowd-labeled, and it cannot be trusted to another model, it takes licensed clinicians,
which almost no one supplies at scale. Senebiclabs is the infrastructure that does.

---

## What we produce

- **Evaluation** — expert judgment on what a model gets right and where it fails: an
  accuracy scorecard plus corrected, gold-standard labels.
- **Creation** — data that doesn't exist yet: gold answers, preferences (RLHF), and
  benchmarks, authored by clinicians.
- **Labeling** — expert-reviewed, categorised datasets.

Every deliverable is multi-reviewed by default, disagreements are adjudicated, per-reviewer
quality is tracked, and every judgment is attributable.

---

## How it works

1. **Create a project** — the client defines the task (an `eval_config`: input, fields,
   classes, rubric) via API, or from a ready template.
2. **Ingest data** — inline, or a manifest pointing at the client's own storage. The bytes
   stay in the client's bucket; only references flow in. Bulk-safe via a bounded rolling
   window into review.
3. **Expert review** — certified clinicians review each item; N-way consensus forms the
   answer, and disagreements are held for senior adjudication.
4. **Deliver** — a QA'd result (scorecard + corrected data + per-reviewer quality) by API
   poll or a signed webhook. Each client's data is isolated; reviewers are anonymous to the
   client.

---

## Architecture

Two repositories over one shared database:

- **HEALTH** (this repo) — the system of record: project creation, ingestion, consensus,
  adjudication, aggregation, and delivery. FastAPI on Cloud Run.
- **workforce** — the clinician-serving application (task UI, auth, pools). Next.js on Vercel.
- **Label Studio** — the shared annotation store.
- **Supabase (Postgres)** — the shared system of record.

---

## Principles

1. **Human expert judgment, not automated eval.** In high-stakes medicine, a model judging a
   model is not trustworthy. Licensed clinicians are the authority.
2. **Consensus, not one opinion.** Multiple experts per item; disagreements are adjudicated,
   never silently averaged.
3. **Confidential by construction.** Each client's data is isolated; reviewers never see other
   clients' work and stay anonymous to the client.
4. **Provenance.** Every judgment is attributable and auditable. The deliverable is evidence,
   not opinion.

---

## Tech stack

| Component | Technology |
|---|---|
| API | FastAPI + uvicorn, Pydantic v2 |
| Database | Supabase (Postgres) |
| Annotation store | Label Studio |
| Frontend | Next.js 14 |
| Data quality | Great Expectations (offline / CI) |
| Deploy | Cloud Run + Vercel |

---

## Repository layout

```
app/            FastAPI backend
  api/v1/       API routes — project intake, ingestion, consensus, delivery
  services/     scoring/report, Label Studio integration, task templates
ui/             Next.js frontend — public site + client portal
scripts/        data-quality validators + ops tooling
tests/          unit + numeric (numpy oracle) + browser (Playwright)
```

---

## Running locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
# API docs: http://localhost:8000/docs
```

Frontend:

```bash
cd ui && npm install && npm run dev
# http://localhost:3000
```

---

## Disclaimer

Senebiclabs provides expert-reviewed evaluation and labeling data for medical-AI development.
It is not a diagnostic or clinical-care system.

---

> **Note:** This repository also contains earlier respiratory-intelligence research — a
> healthy-cell reference that characterises how a sample deviates from healthy. That is a
> separate research thread; the clinician data platform above is the primary system.

---

*Senebiclabs — clinician-grade data for medical AI.*
