import type { Metadata } from 'next'
import { C, Code } from '../_ui'

export const metadata: Metadata = {
  title: 'Push items · Senebiclabs API',
  description: 'POST /ingest: send a batch inline, or stream a manifest from your own storage.',
  alternates: { canonical: 'https://senebiclabs.com/docs/push-items' },
}

export default function Page() {
  return (
    <>
        <section>
          <span className="docs-eyebrow">Endpoint</span>
          <h1>
            <span className="m m-post">POST</span>
            <span className="ep">/ingest</span>
            <span className="tag">Push items</span>
          </h1>
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
            Poll <a href="/docs/poll-results" style={{ color: '#fff' }}>results</a> as usual.
          </p>
          <p>
            <C>source.mode</C> picks what gets reviewed. <C>&quot;sample&quot;</C> (default) reviews a
            representative random sample (default 1000) — best for <b>evaluating</b> a model&rsquo;s
            quality without labeling everything. <C>&quot;all&quot;</C> reviews <b>every</b> item — best
            for <b>labeling a full dataset</b>; for very large sets we agree a volume and cadence up front.
          </p>
          <Code>{`  "source": { "manifest_url": "https://your-bucket.s3.../manifest.jsonl", "mode": "all" }`}</Code>
        </section>
    </>
  )
}
