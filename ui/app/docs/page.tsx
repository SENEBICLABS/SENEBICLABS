import type { Metadata } from 'next'
import './docs.css'
import DocsEnhance from './DocsEnhance'

export const metadata: Metadata = {
  title: 'API · Senebiclabs',
  description:
    'Senebiclabs API: create a project, push items, poll status and results, and receive a signed webhook when a clinician-reviewed batch is delivered.',
  robots: { index: true, follow: true },
  alternates: { canonical: 'https://senebiclabs.com/docs' },
}

const BASE = 'https://api.senebiclabs.com/api/v1/project'

function Code({ children }: { children: string }) {
  return (
    <pre className="docs-pre">
      <code>{children}</code>
    </pre>
  )
}

function C({ children }: { children: string }) {
  return <code className="ic">{children}</code>
}

export default function DocsPage() {
  return (
    <div className="docs-shell">
      {/* Sidebar */}
      <aside className="docs-side">
        <a href="/" className="docs-brand">Senebiclabs</a>
        <div className="docs-brand-sub">API reference</div>
        <a href="/developers" className="docs-cta">Get an API key →</a>
        <nav className="docs-nav">
          <a href="#overview">Overview</a>
          <a href="#quickstart">Quickstart</a>
          <a href="#auth">Authentication</a>
          <div className="grp">Endpoints</div>
          <a href="#create">Create a project</a>
          <a href="#ingest">Push items</a>
          <a href="#results">Poll results</a>
          <a href="#webhooks">Webhooks</a>
          <div className="grp">Reference</div>
          <a href="#config">Task config</a>
          <a href="#errors">Errors and notes</a>
        </nav>
      </aside>

      {/* Content */}
      <main className="docs-main">
        <section id="overview">
          <h1>API</h1>
          <p className="docs-lead">
            Programmatic access for clients who integrate by code instead of the dashboard.
            Create a project, push a batch of items, poll for status and results, and
            optionally receive a signed webhook when a clinician-reviewed batch is delivered.
          </p>

          <p>You set the <b>purpose</b> of a project and it decides the deliverable you get back. Three purposes, set with <C>purpose</C> in your config (defaults to <C>evaluate</C>):</p>
          <ul>
            <li><b><C>evaluate</C>:</b> each item carries a model output; you get a model-performance scorecard — accuracy, per-class metrics, critical misses.</li>
            <li><b><C>label</C>:</b> you get your data back labelled, plus a summary — class distribution, coverage, agreement.</li>
            <li><b><C>create</C>:</b> you get new data produced for you — gold answers, preference pairs, or ratings, plus a coverage/agreement summary.</li>
          </ul>

          <h3>Base URL</h3>
          <Code>{BASE}</Code>
        </section>

        {/* Quickstart */}
        <section id="quickstart" className="docs-sec">
          <span className="docs-eyebrow">Quickstart</span>
          <h2>From zero to results</h2>
          <p>
            The whole flow in four calls. First, <a href="/developers" style={{ color: '#fff' }}>get a key</a>,
            then set it in your shell:
          </p>
          <Code>{`BASE="${BASE}"
KEY="your_api_key"`}</Code>

          <h3>1 · Create a project</h3>
          <Code>{`curl -s -X POST "$BASE/projects" \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{ "name": "My eval", "eval_config": { ... } }'`}</Code>
          <p>Returns a <C>project_id</C>. Full task config in <a href="#create" style={{ color: '#fff' }}>Create a project</a>.</p>

          <h3>2 · Push items</h3>
          <Code>{`curl -s -X POST "$BASE/ingest" \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -H "Idempotency-Key: batch-1" \\
  -d '{ "project_id": "...", "items": [ ... ] }'`}</Code>

          <h3>3 · Poll for results</h3>
          <Code>{`curl -s "$BASE/results?project_id=..." -H "Authorization: Bearer $KEY"`}</Code>
          <p>
            Clinicians review, then <C>status</C> becomes <C>delivered</C> with the report and reviewed
            items. Prefer a push? Register a <a href="#webhooks" style={{ color: '#fff' }}>webhook</a> and we
            call you, signed.
          </p>
        </section>

        {/* Auth */}
        <section id="auth" className="docs-sec">
          <span className="docs-eyebrow">Authentication</span>
          <h2>Bearer API key</h2>
          <p>Every request carries your API key as a bearer token:</p>
          <Code>{`Authorization: Bearer <YOUR_API_KEY>`}</Code>
          <p>
            <b>Get your key</b> at <a href="/developers" style={{ color: '#fff' }}>senebiclabs.com/developers</a>:
            verify your email and create one in seconds. Keys are shown once, tied to your account, and you
            can revoke any of them there at any time.
          </p>
          <p>
            Then create a project with <C>POST /projects</C> (below) and you get a <C>project_id</C> to push
            items to. One key can create and drive many projects.
          </p>
        </section>

        {/* Create */}
        <section id="create" className="docs-sec">
          <span className="docs-eyebrow">Endpoint</span>
          <h2>
            <span className="m m-post">POST</span>
            <span className="ep">/projects</span>
            <span className="tag">Create a project</span>
          </h2>
          <p>
            Create a project and get back a <C>project_id</C> to push items to.
          </p>

          <h3>Start from a template (recommended)</h3>
          <p>
            Pick what you want to achieve and we build the project for you — no config to author.
            List the outcomes with <C>GET /templates</C>:
          </p>
          <ul>
            <li><C>model_evaluation</C> — grade your model&rsquo;s outputs → accuracy + safety scorecard</li>
            <li><C>data_labeling</C> — your data back, labelled → labelled dataset + summary</li>
            <li><C>rlhf_preference</C> — pick the better of two responses → preference pairs for RLHF</li>
            <li><C>gold_answers</C> — write the ideal answer → gold dataset for fine-tuning</li>
            <li><C>case_review</C> — judge whether AI helped or hurt on full cases → audit dataset + impact distribution</li>
            <li><C>benchmark_creation</C> — author challenging test cases → an evaluation benchmark</li>
            <li><C>adversarial_prompts</C> — write probes that expose model gaps → a red-teaming test set</li>
            <li><C>fact_checking</C> — highlight errors in an answer, rewrite it, cite a source → accuracy + a corrections dataset</li>
            <li><C>dialogue_creation</C> — author realistic patient-clinician dialogues → synthetic training data</li>
            <li><C>response_ranking</C> — rank two answers on accuracy/empathy/clarity/safety → preference pairs with per-axis scores</li>
          </ul>
          <p>Create from one, supplying your own <C>classes</C> (label set) where it applies:</p>
          <Code>{`curl -X POST "$BASE/projects" \\
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \\
  -d '{
    "name": "Triage model eval",
    "template": "model_evaluation",
    "classes": ["Routine", "Urgent", "Emergency"],
    "webhook_url": "https://your-app.com/hooks/senebiclabs"
  }'`}</Code>
          <p>That is all most projects need. The rest of this section is the <b>advanced</b> path — authoring a full config yourself.</p>
          <p>
            <b>Tune a template to your own rubric.</b> <C>GET /templates</C> also returns each template&rsquo;s
            full <C>eval_config</C>. Take the closest one, edit it to fit your exact task (add rating axes,
            change fields or context), and submit it as a custom <C>eval_config</C> below instead of{' '}
            <C>template</C> — so you start from a working, validated config, not a blank page.
          </p>

          <h3>Custom config (advanced)</h3>
          <p>Define your own task from scratch:</p>
          <Code>{`curl -X POST "$BASE/projects" \\
  -H "Authorization: Bearer $API_KEY" \\
  -H "Content-Type: application/json" \\
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
  }'`}</Code>
          <h3>Response</h3>
          <Code>{`{
  "ok": true,
  "project_id": "fc64fb22-...",
  "webhook_secret": "a28e0736cb92..."
}`}</Code>
          <p>
            <b>Save the <C>webhook_secret</C>.</b> It is returned once, only when you register
            a <C>webhook_url</C>, and is used to verify webhook authenticity (see{' '}
            <a href="#webhooks" style={{ color: '#fff' }}>Webhooks</a>). Treat it like a password.
          </p>
          <p>
            This example is an <b>evaluation</b> project: each item carries a <C>prediction</C>,
            clinicians return a <C>verdict</C> of <C>Correct</C>, <C>Incorrect</C>, or <C>Partial</C>,
            and the report scores accuracy. For a <b>creation</b> project, omit <C>prediction</C> and set
            <C>fields</C> to the labels you want produced; the results come back as content-and-label
            pairs with no scorecard.
          </p>
        </section>

        {/* Ingest */}
        <section id="ingest" className="docs-sec">
          <span className="docs-eyebrow">Endpoint</span>
          <h2>
            <span className="m m-post">POST</span>
            <span className="ep">/ingest</span>
            <span className="tag">Push items</span>
          </h2>
          <p>
            Send a batch of items (for example, conversations). Each item is a JSON object
            whose fields match your task config. We tell you the exact fields at setup.
          </p>
          <Code>{`curl -X POST "$BASE/ingest" \\
  -H "Authorization: Bearer $API_KEY" \\
  -H "Content-Type: application/json" \\
  -H "Idempotency-Key: batch-2026-08-12-001" \\
  -d '{
    "project_id": "YOUR_PROJECT_ID",
    "items": [
      { "case_id": "case_001", "scenario": "patient message...", "prediction": "Routine" },
      { "case_id": "case_002", "scenario": "patient message...", "prediction": "Urgent" }
    ]
  }'`}</Code>
          <h3>Idempotency</h3>
          <p>
            Send an <C>Idempotency-Key</C> header with each batch. If a request times out and
            you retry with the same key, we recognise it and skip the insert, so a retry never
            creates duplicates. A repeated key returns:
          </p>
          <Code>{`{ "ok": true, "message": "Batch already ingested (idempotent)." }`}</Code>
          <p>
            Use a fresh key per distinct batch. Without a key, each call appends its items, so
            two identical calls would create duplicates.
          </p>
          <h3>Response</h3>
          <Code>{`{ "ok": true, "message": "Ingested 2 items." }`}</Code>
          <h3>Bulk (data in your storage)</h3>
          <p>
            For large volumes, don&rsquo;t push the data through the API at all. Leave it in your
            storage (e.g. S3) and send a <C>manifest_url</C> instead of <C>items</C>. A manifest is a
            JSONL file where each line is one item.
          </p>
          <Code>{`curl -s -X POST "$BASE/ingest" \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{
    "project_id": "...",
    "source": { "manifest_url": "https://your-bucket.s3.../manifest.jsonl", "sample": 1000 }
  }'`}</Code>
          <p>
            The data itself never passes through the API and never leaves your storage — it is read
            directly from your bucket, so any volume works.
            Poll <a href="#results" style={{ color: '#fff' }}>results</a> as usual.
          </p>
          <p>
            <C>source.mode</C> picks what gets reviewed. <C>&quot;sample&quot;</C> (default) reviews a
            representative random sample (default 1000) — best for <b>evaluating</b> a model&rsquo;s
            quality without labeling everything. <C>&quot;all&quot;</C> reviews <b>every</b> item — best
            for <b>labeling a full dataset</b>; for very large sets we agree a volume and cadence up front.
          </p>
          <Code>{`  "source": { "manifest_url": "https://your-bucket.s3.../manifest.jsonl", "mode": "all" }`}</Code>
        </section>

        {/* Results */}
        <section id="results" className="docs-sec">
          <span className="docs-eyebrow">Endpoint</span>
          <h2>
            <span className="m m-get">GET</span>
            <span className="ep">/results</span>
            <span className="tag">Poll status and results</span>
          </h2>
          <p>
            Clinician review is done by people, so results are not instant. Poll this endpoint.
            <C>status</C> moves through{' '}
            <C>received, in_review, delivered</C>, and <C>total</C> / <C>done</C> show progress. Only{' '}
            <C>delivered</C> includes the report and items.
          </p>
          <Code>{`curl "$BASE/results?project_id=YOUR_PROJECT_ID" \\
  -H "Authorization: Bearer $API_KEY"`}</Code>
          <h3>While in review</h3>
          <Code>{`{ "ok": true, "project_id": "...", "status": "in_review", "total": 200, "done": 142 }`}</Code>
          <h3>When delivered</h3>
          <Code>{`{
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
}`}</Code>
          <p>
            Each item is reviewed by multiple licensed clinicians, and the <C>qa</C> block reports
            their mean agreement and how many items needed adjudication — so you can trust the numbers.
          </p>
          <div className="docs-callout">
            <p>
              <b>Scoring contract:</b> to get the accuracy <C>report</C>, items must carry a
              <C>prediction</C> and your fields must use these exact names: <C>verdict</C> (<C>Correct</C> /{' '}
              <C>Incorrect</C> / <C>Partial</C>), <C>correct_label</C> (the corrected class for a wrong
              verdict), and <C>critical_miss</C> (a <C>structured</C> field that populates the report&rsquo;s
              critical misses). A wrong verdict with no <C>correct_label</C> is excluded, never guessed.{' '}
              <C>label</C> and <C>create</C> projects skip scoring and return every reviewed item in{' '}
              <C>items</C> as a content-and-label pair.
            </p>
          </div>
        </section>

        {/* Webhooks */}
        <section id="webhooks" className="docs-sec">
          <span className="docs-eyebrow">Delivery</span>
          <h2>Webhooks <span className="tag">optional, signed</span></h2>
          <p>
            If you registered a <C>webhook_url</C>, we POST it once when the batch is delivered,
            so you do not have to poll. The body is the same shape as the delivered{' '}
            <C>GET /results</C> response:
          </p>
          <Code>{`POST https://your-app.com/hooks/senebiclabs
Content-Type: application/json
X-Senebiclabs-Signature: sha256=<hex>

{
  "event": "results.delivered",
  "project_id": "...",
  "company": "Your Company",
  "report": { ... },
  "items": [ ... ]
}`}</Code>

          <h3>Verify the signature</h3>
          <p>
            Every webhook carries an <C>X-Senebiclabs-Signature</C> header. It is an
            HMAC-SHA256 of the exact request body, keyed with your <C>webhook_secret</C>.
            Recompute it and compare in constant time before you trust the payload. This proves
            the request came from us and was not altered in transit.
          </p>
          <div className="docs-callout">
            <p>
              Compute over the <b>raw request bytes</b>, before any JSON parsing. Parsing and
              re-serialising can change the bytes and break the check.
            </p>
          </div>
          <Code>{`import hmac, hashlib

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
    ...`}</Code>
          <p>
            Return <C>2xx</C> to acknowledge. One call is made per delivery (no automatic
            retries), so keep polling <C>GET /results</C> as the source of truth if delivery is
            critical.
          </p>
        </section>

        {/* Config reference */}
        <section id="config" className="docs-sec">
          <span className="docs-eyebrow">Reference</span>
          <h2>Task config</h2>
          <p>
            The <C>eval_config</C> defines what clinicians see and fill in. Key fields:
          </p>
          <ul>
            <li><C>purpose</C>: <C>evaluate</C> (grade a model output), <C>label</C> (categorise / annotate data), or <C>create</C> (produce gold answers, preferences, or ratings). Defaults to <C>evaluate</C>; it sets the reviewer workflow and the deliverable.</li>
            <li><C>instructions</C>: your rubric, shown to clinicians at the top of every task &mdash; what to evaluate, the standard, what counts as an error, edge cases. Optional, but it&rsquo;s the single biggest lever on answer quality and reviewer agreement. Use line breaks to separate points. (Templates ship with a starter rubric you can tune.)</li>
            <li><C>adjudicate</C>: <C>true</C> holds any item where reviewers disagree for a senior reviewer to resolve, instead of shipping the majority vote. Recommended for judgment work; the judgment templates set it for you. Optional (default <C>false</C>).</li>
            <li><C>auto_deliver</C>: by default a finished batch is held for a human sign-off before it&rsquo;s released to you (status stays <C>in_review</C> until then). Set <C>true</C> for hands-off delivery the moment every item is done. Optional (default <C>false</C>).</li>
            <li><C>input</C>: <C>text</C> (shows the <C>context</C> fields), <C>image</C> (each item needs an <C>image</C> URL), or <C>audio</C> / <C>video</C> (each item needs an <C>audio</C> / <C>video</C> URL; a clinician plays it, streamed straight from your storage).</li>
            <li><C>context</C>: for text tasks, which data keys to show the clinician, in order.</li>
            <li><C>classes</C>: the label set used by <C>from_classes</C> and <C>structured</C> fields.</li>
            <li><C>case_id_field</C>: which item field ties a result back to your own record.</li>
          </ul>
          <p><C>fields</C> is a map of what the clinician fills. Each has a <C>type</C>:</p>
          <ul>
            <li><C>single</C>: choose one of <C>options</C>.</li>
            <li><C>from_classes</C>: choose one of the project <C>classes</C>.</li>
            <li><C>structured</C>: yes or no, plus which finding (from classes).</li>
            <li><C>scale</C>: a rating from 1 to <C>max</C>.</li>
            <li><C>flag</C>: a single checkbox.</li>
            <li><C>text</C>: free-text notes (<C>rows</C> sets the box height for long-form).</li>
            <li><C>spans</C>: highlight text in the model output and tag each span with one of <C>options</C> (text input only).</li>
          </ul>
          <p>
            Any field can add <C>required: true</C>, <C>{'visible_when: "field!=value"'}</C>, and <C>{'hint: "..."'}</C> (a one-line note shown under the field&rsquo;s label to guide the clinician).
          </p>
        </section>

        {/* Errors */}
        <section id="errors" className="docs-sec">
          <span className="docs-eyebrow">Reference</span>
          <h2>Errors and notes</h2>
          <ul>
            <li><b>Errors:</b> <C>401</C> invalid or missing key, <C>403</C> project not on this key, <C>422</C> invalid config or items missing a required field, <C>503</C> service unavailable.</li>
            <li><b>Idempotency:</b> send an <C>Idempotency-Key</C> header per batch so retries are safe. Without one, each <C>/ingest</C> appends its items.</li>
            <li><b>Content shape</b> is up to you as long as it matches the configured task. For text review, typically <C>prompt</C> plus <C>output</C>. Add <C>case_id</C> to tie results back to your records.</li>
          </ul>
          <div className="docs-foot">
            <span className="docs-eyebrow">Questions? senebiclabs@gmail.com</span>
          </div>
        </section>
      </main>
      <DocsEnhance />
    </div>
  )
}
