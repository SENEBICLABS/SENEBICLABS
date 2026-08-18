# Senebiclabs API

Programmatic access for clients who integrate by code instead of the dashboard.
Create a project, push items, poll for status and results, and optionally receive a
**signed** webhook when a clinician-reviewed batch is delivered.

> The canonical, always-current version of this reference is the web page at
> **https://senebiclabs.com/docs**. This file mirrors it.

You set the **purpose** of a project and it decides the deliverable you get back. Three
purposes:

- **`evaluate`** — each item carries a model output; you get a model-performance scorecard
  (accuracy, per-class metrics, critical misses).
- **`label`** — you get your data back labelled, plus a summary (class distribution,
  coverage, agreement).
- **`create`** — you get new data produced for you: gold answers, preference pairs, or
  ratings, plus a coverage/agreement summary.

Set it once with `purpose` in your config (defaults to `evaluate`).

**Base URL**

```
https://api.senebiclabs.com/api/v1/project
```

**Auth** — every request carries your API key as a bearer token:

```
Authorization: Bearer <YOUR_API_KEY>
```

**Get your key** at https://senebiclabs.com/developers — verify your email and create
one in seconds. Keys are shown once, tied to your account, and revocable there at any
time. Keep it secret.

Then create a project with `POST /projects` (§1) and you get a `project_id` to push
items to. One key can create and drive many projects.

---

## 1. Create a project — `POST /projects`

Create a project and get back a `project_id` to push items to.

### Start from a template (recommended)

Pick what you want to achieve and we build the project for you — no config to author.
List the outcomes with `GET /templates`:

| template | you get |
|---|---|
| `model_evaluation` | grade your model's outputs → accuracy + safety scorecard |
| `data_labeling` | your data back, labelled → labelled dataset + summary |
| `rlhf_preference` | pick the better of two responses → preference pairs for RLHF |
| `gold_answers` | write the ideal answer → gold dataset for fine-tuning |
| `case_review` | judge whether AI helped or hurt on full cases → audit dataset + impact distribution |
| `benchmark_creation` | author challenging test cases → an evaluation benchmark |
| `adversarial_prompts` | write probes that expose model gaps → a red-teaming test set |

Create from one, supplying your own `classes` (label set) where it applies:

```bash
curl -X POST "$BASE/projects" \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{
    "name": "Triage model eval",
    "template": "model_evaluation",
    "classes": ["Routine", "Urgent", "Emergency"],
    "webhook_url": "https://your-app.com/hooks/senebiclabs"
  }'
```

**Tune a template to your own rubric.** `GET /templates` also returns each template's full
`eval_config`. Take the closest one, edit it to fit your exact task (add rating axes, change
fields or context), and submit it as a custom `eval_config` (below) instead of `template`.
That way you start from a working, validated config rather than a blank page.

That is all most projects need. The rest of this section is the **advanced** path —
authoring a full config yourself.

### Custom config (advanced)

Define your own task from scratch:

```bash
curl -X POST "$BASE/projects" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Clinical response evaluation",
    "eval_config": {
      "title": "Clinical response review",
      "purpose": "evaluate",
      "schema": {
        "input": "text",
        "context": [
          { "key": "scenario",   "label": "Patient message" },
          { "key": "prediction", "label": "Model response" }
        ],
        "classes": ["Routine", "Urgent", "Emergency"],
        "case_id_field": "case_id",
        "fields": {
          "verdict":       { "type": "single", "options": ["Correct", "Incorrect", "Partial"], "required": true },
          "correct_label": { "type": "from_classes", "visible_when": "verdict!=Correct" },
          "critical_miss": { "type": "structured" },
          "notes":         { "type": "text" }
        }
      }
    },
    "webhook_url": "https://your-app.com/hooks/senebiclabs"
  }'
```

Returns:

```json
{
  "ok": true,
  "project_id": "fc64fb22-...",
  "webhook_secret": "a28e0736cb92..."
}
```

**Save the `webhook_secret`.** It is returned once, only when you register a
`webhook_url`, and is used to verify webhook authenticity (§4). Treat it like a
password.

This example is an **`evaluate`** project: each item carries a `prediction`, clinicians
return a `verdict` of `Correct` / `Incorrect` / `Partial`, and the report scores accuracy.
For **`label`** or **`create`**, set `purpose` accordingly, omit `prediction`, and set
`fields` to what the clinician should produce; results come back as content-and-label
pairs with a coverage/agreement summary instead of a scorecard.

### Config reference

- `purpose` — `"evaluate"` (grade a model output), `"label"` (categorise/annotate data),
  or `"create"` (produce gold answers, preferences, or ratings). Defaults to `"evaluate"`.
  It sets the reviewer workflow and the deliverable.
- `input` — `"text"` (shows the `context` fields), `"image"` (each item needs an
  `image` URL), or `"audio"` / `"video"` (each item needs an `audio` / `video` URL; a
  clinician plays it, streamed straight from your storage).
- `context` — text mode only: which data keys to show the clinician, in order.
- `classes` — the label set used by `from_classes` and `structured` fields.
- `case_id_field` — which item field ties a result back to your own record.
- `fields` — a map of what the clinician fills. Each has a `type`:
  - `single` — choose one of `options`
  - `from_classes` — choose one of the project `classes`
  - `structured` — yes/no plus which finding (from classes)
  - `scale` — a 1..`max` rating
  - `flag` — a single checkbox
  - `text` — free-text notes
- Optional on any field: `required: true`, `visible_when: "field!=value"`.

---

## 2. Push items — `POST /ingest`

Send a batch of items. Each item is a JSON object whose fields match your task config.

```bash
curl -X POST "$BASE/ingest" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: batch-2026-08-12-001" \
  -d '{
    "project_id": "YOUR_PROJECT_ID",
    "items": [
      { "case_id": "case_001", "scenario": "patient message...", "prediction": "Routine" },
      { "case_id": "case_002", "scenario": "patient message...", "prediction": "Urgent" }
    ]
  }'
```

- `items` — array of objects, fields must match the task config. An evaluation item
  carries the model output as `prediction`; a creation item just carries the raw data.

### Idempotency

Send an `Idempotency-Key` header with each batch. If a request times out and you
retry with the same key, we recognise it and skip the insert, so a retry never
creates duplicates. A repeated key returns:

```json
{ "ok": true, "message": "Batch already ingested (idempotent)." }
```

Use a fresh key per distinct batch. Without a key, each call appends its items, so
two identical calls would create duplicates.

Response on a new batch: `{ "ok": true, "message": "Ingested 2 items." }`

### Bulk (data in your storage)

For large volumes, don't push the data through the API at all. Leave it in your storage
(e.g. S3) and send a `manifest_url` instead of `items`. A manifest is a JSONL file where
each line is one item.

```bash
curl -s -X POST "$BASE/ingest" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{
    "project_id": "...",
    "source": { "manifest_url": "https://your-bucket.s3.../manifest.jsonl", "sample": 1000 }
  }'
```

The data itself never passes through the API and never leaves your storage — it is read
directly from your bucket, so any volume works. Poll `GET /results` as usual.

**Sample vs. everything.** `source.mode` picks what gets reviewed:

- `"sample"` (default) — review a representative random `sample` (default 1000). Best for
  **evaluating** a model's quality: a good sample gives valid metrics without labeling
  everything.
- `"all"` — review **every** item in the manifest. Best for **labeling a full dataset**.
  For very large sets we agree a volume and cadence up front.

```bash
  "source": { "manifest_url": "https://your-bucket.s3.../manifest.jsonl", "mode": "all" }
```

For an ongoing feed, use a **scoped read-only credential** to your bucket (so URLs don't
expire mid-review) and send a new manifest per cycle.

---

## 3. Poll status + results — `GET /results`

Clinician review is done by people, so results are not instant. Poll this endpoint;
`status` moves through `received → in_review → delivered`, and `total` / `done` show
progress along the way. Only `delivered` includes `report` and `items`.

```bash
curl "$BASE/results?project_id=YOUR_PROJECT_ID" \
  -H "Authorization: Bearer $API_KEY"
```

While in review:

```json
{ "ok": true, "project_id": "...", "status": "in_review", "total": 200, "done": 142 }
```

When delivered:

```json
{
  "ok": true,
  "project_id": "...",
  "status": "delivered",
  "total": 200,
  "done": 200,
  "report": {
    "accuracy": { "value": 0.8, "correct": 160, "assessable": 200 },
    "critical_misses": [ ... ],
    "per_class": { ... },
    "qa": { "mean_agreement": 0.86, "reviewers": 3, "disagreements": 12 }
  },
  "items": [
    { "idx": 0, "content": { "case_id": "case_001", ... },
      "label": { "verdict": "Correct", ... }, "labeled_at": "..." }
  ]
}
```

Each item is reviewed by multiple licensed clinicians, and the `qa` block reports their
mean agreement and how many items needed adjudication — so you can trust the numbers.

**Scoring contract:** to get the accuracy `report`, items must carry a `prediction` and
your fields must use these exact names: `verdict` (`Correct` / `Incorrect` / `Partial`),
`correct_label` (the corrected class for a wrong verdict), and `critical_miss` (a
`structured` field that populates the report's critical misses). A wrong verdict with no
`correct_label` is excluded, never guessed. `label` and `create` projects skip scoring and
return every reviewed item in `items` as a content-and-label pair.

---

## 4. Webhook (optional, signed) — we call you

If you registered a `webhook_url`, we `POST` it once when the batch is delivered. The
body is the same shape as the delivered `GET /results` response:

```
POST https://your-app.com/hooks/senebiclabs
Content-Type: application/json
X-Senebiclabs-Signature: sha256=<hex>

{
  "event": "results.delivered",
  "project_id": "...",
  "company": "Your Company",
  "report": { ... },
  "items": [ ... ]
}
```

### Verify the signature

Every webhook carries an `X-Senebiclabs-Signature` header: an HMAC-SHA256 of the exact
request body, keyed with your `webhook_secret`. Recompute it over the **raw request
bytes** (before any JSON parsing — re-serialising can change the bytes and break the
check) and compare in constant time before trusting the payload.

```python
import hmac, hashlib

def verify(raw_body: bytes, header: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")

# FastAPI example
@app.post("/hooks/senebiclabs")
async def hook(request: Request):
    raw = await request.body()
    sig = request.headers.get("X-Senebiclabs-Signature", "")
    if not verify(raw, sig, WEBHOOK_SECRET):
        raise HTTPException(status_code=401)
    payload = json.loads(raw)   # trusted from here
    ...
```

Return `2xx` to acknowledge. One call is made per delivery (no automatic retries), so
keep polling `GET /results` as the source of truth if delivery is critical.

---

## Notes

- **Errors**: `401` invalid/missing key · `403` project not on this key ·
  `422` invalid config or items missing a required field · `503` service unavailable.
- **Idempotency**: send an `Idempotency-Key` header per batch so retries are safe.
  Without one, each `/ingest` appends its items.
- **Content shape** is up to you as long as it matches the configured task. Evaluation
  items carry the model output as `prediction`; add `case_id` to tie results back to
  your records.
