# Serving a text / LLM client — manual pilot playbook (NO CODE)

**When a text or medical-LLM client shows up, you can deliver a pilot today, by hand, through
Label Studio's web UI — no developer, no code, no automation build.** This is the scrappy,
legitimate way to serve and get paid. Automate it later, only when text demand is real and a
paying client's needs can shape the build.

Imaging clients go through the automated pipeline (`ingest_xray.py` → `/ls/sync` → portal).
Text/LLM clients, for now, go through **Label Studio directly** — that's this doc.

---

## What Label Studio already gives you (no build needed)
LS natively handles text and has built-in templates for every task a text/LLM client asks for:
- **Text classification** (categorise a note)
- **LLM output scoring** (rate a model's answer 1–5 for accuracy/safety)
- **A/B preference** (RLHF — which of two answers is better)
- **Named-entity / span extraction**, **PHI redaction**
- **Response writing** (a clinician writes the gold answer)

Your codebase also has polished versions of these configs in
`app/services/labelstudio.py` — you can copy the XML from there into LS if you want the
Senebiclabs-styled version. But LS's own templates work fine.

---

## The manual pilot — step by step

**1. Scope it (a call).**
Ask: what's the task (classification? scoring? A/B ranking? extraction?), how many items for
the pilot (aim ~100), and get a small sample. Agree a price and a turnaround.

**2. Get their data as a file.**
A CSV or JSON, one row per item, with the text columns the task needs:
- Classification / extraction → a `text` column.
- LLM scoring → `prompt` and `output` columns.
- A/B preference → `prompt`, `output_a`, `output_b`.
- Include **their own id column** (e.g. `study_id`) so results come back keyed to their ids.
- **De-identified only** — ask them to strip patient identifiers before sending. No PHI.

**3. Open Label Studio** (your LS VM URL) and log in as admin.

**4. Create a new project.** Give it the client's name.

**5. Import their data.** In the project: **Import** → upload the CSV/JSON. Each row becomes a
task. (LS reads your columns as `$text`, `$prompt`, `$output`, etc.)

**6. Set the labeling interface.** In **Settings → Labeling Interface**, either pick a built-in
template (e.g. "Text Classification") or paste the matching config XML from
`app/services/labelstudio.py`. Edit the label options to match the client's categories.

**7. Add your clinicians.** **Organization → People** → invite each reviewer by email; they set
their own password. Then add them to the project. They log in from **their own laptops** — LS
is multi-user, so several can review at once and it won't hand the same item to two people.

**8. Clinicians review** in the LS UI.

**9. Export the results.** Project → **Export** → CSV or JSON. That file *is* the deliverable:
every item with the clinician's label/score/ranking, keyed to the client's id.

**10. Deliver.**
- Send the client the export (their labeled / scored dataset).
- Add a short written summary. For **classification** you can compute accuracy in a spreadsheet
  (compare their model's prediction vs the clinician label → % agreement, per category). For
  **scoring / RLHF** just report the scores/preferences and averages — no accuracy report needed.
- **Honesty rails (always):** say "licensed clinicians," never "board-certified"; frame any
  number as "measured on the reviewed sample," never the model's true accuracy; never invent
  stats.

---

## Now built (this section used to say "can't do by hand")
The text path is automated too now, managed or self-serve, same pipeline:
- **Self-serve ingest** — clients upload a CSV/JSON in the portal, or push by API
  (`POST /ingest`). Each row becomes a `project_item`. See `docs/api.md`.
- **Text scoring** — `report.py` scores verdict-style text eval (triage, safety review),
  not just imaging. Pure RLHF/preference still delivers the labeled data, not an accuracy score.
- **Delivery** — the portal shows the report + download for delivered projects; API clients
  poll `GET /results` or receive a webhook on delivery.
- **Config, assign, report, API key** — all in the `/admin` dashboard, no scripts needed.

So a text client no longer needs a manual, LS-only pilot.

---

## The one caution
Run `ingest_xray.py` and the automated pipeline **only from your local repo on the
`eval-production-hardening` branch** (it has all the fixes). Your Cloud Shell clones are stale
(`main`) and will reintroduce old bugs. For manual text pilots you're in the LS web UI, so this
doesn't apply — but it matters the moment you touch the imaging automation.

---

---

## PRACTICE FIRST (do this once, before any real client)
Run a full dry run with **fake data** so the first real client isn't the first time you touch
this. Make a 5-row CSV, create a throwaway LS project, import it, paste a config below, label a
couple yourself, export. 20 minutes now saves you panic later. Then delete the throwaway project.

---

## Copy-paste Label Studio configs (paste into Settings → Labeling Interface)

**A) Score an LLM's answers** — CSV columns: `prompt,output,study_id`
```xml
<View>
  <Header value="Score the model's response for accuracy and safety."/>
  <Text name="prompt" value="$prompt"/>
  <Header value="Model response"/>
  <Text name="output" value="$output"/>
  <Rating name="score" toName="output" maxRating="5" icon="star" required="true"/>
  <Choices name="flag" toName="output" choice="single">
    <Choice value="Flag: unsafe or clinically incorrect"/>
  </Choices>
  <TextArea name="rationale" toName="output" placeholder="One line: why this score" rows="3"/>
</View>
```

**B) A vs B preference (RLHF)** — CSV columns: `prompt,output_a,output_b,study_id`
```xml
<View>
  <Text name="prompt" value="$prompt"/>
  <Header value="Response A"/>
  <Text name="output_a" value="$output_a"/>
  <Header value="Response B"/>
  <Text name="output_b" value="$output_b"/>
  <Choices name="preference" toName="prompt" choice="single" required="true">
    <Choice value="A is better"/>
    <Choice value="B is better"/>
    <Choice value="Tie"/>
  </Choices>
  <TextArea name="rationale" toName="prompt" placeholder="Why" rows="3"/>
</View>
```

**C) Classify / categorise text** — CSV columns: `text,study_id` (edit the categories)
```xml
<View>
  <Text name="text" value="$text"/>
  <Choices name="label" toName="text" choice="single" required="true">
    <Choice value="Category A"/>
    <Choice value="Category B"/>
    <Choice value="Category C"/>
  </Choices>
  <TextArea name="rationale" toName="text" placeholder="Optional note" rows="2"/>
</View>
```

**The one rule that trips people up:** in the config, `$columnname` must match your CSV column
header exactly. If your CSV column is `note`, the config uses `value="$note"`. That's the #1
thing to get right on import.

---

## Bottom line
- **Imaging client** → automated pipeline (proven).
- **Text / LLM client** → this manual LS playbook (works today, no code).
- Either way, **you can say yes and deliver.** Get the client, run the pilot, get paid, then
  automate what the revenue justifies.
