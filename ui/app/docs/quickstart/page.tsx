import type { Metadata } from 'next'
import { BASE, C, Code } from '../_ui'

export const metadata: Metadata = {
  title: 'Quickstart',
  description: 'From an API key to a delivered report in three calls.',
  alternates: { canonical: 'https://senebiclabs.com/docs/quickstart' },
  openGraph: { url: 'https://senebiclabs.com/docs/quickstart' },
}

export default function Page() {
  return (
    <>
        <section>
          <span className="docs-kicker">Get started</span>
          <h1>From zero to results</h1>
          <p>
            The whole flow in three calls, and each one below runs as written. First,{' '}
            <a href="/developers" style={{ color: '#fff' }}>get a key</a>, then set it in your shell:
          </p>
          <Code>{`BASE="${BASE}"
API_KEY="your_api_key"`}</Code>

          <h3>1 · Create a project</h3>
          <p>Start from a template and there is no config to author:</p>
          <Code>{`curl -s -X POST "$BASE/projects" \\
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \\
  -d '{
    "name": "Triage model eval",
    "template": "model_evaluation",
    "classes": ["Routine", "Urgent", "Emergency"]
  }'`}</Code>
          <p>
            Returns a <C>project_id</C>. Every template is listed in{' '}
            <a href="/docs/create-a-project" style={{ color: '#fff' }}>Create a project</a>, along with the advanced
            path if you would rather author the config yourself.
          </p>

          <h3>2 · Push items</h3>
          <Code>{`curl -s -X POST "$BASE/ingest" \\
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \\
  -H "Idempotency-Key: batch-1" \\
  -d '{
    "project_id": "YOUR_PROJECT_ID",
    "items": [
      { "case_id": "case_001", "scenario": "patient message...", "prediction": "Routine" },
      { "case_id": "case_002", "scenario": "patient message...", "prediction": "Urgent" }
    ]
  }'`}</Code>

          <h3>3 · Poll for results</h3>
          <Code>{`curl -s "$BASE/results?project_id=YOUR_PROJECT_ID" -H "Authorization: Bearer $API_KEY"`}</Code>
          <p>
            <C>status</C> becomes <C>delivered</C> when the report and the reviewed items are ready.
            Clinicians work in hours and days rather than seconds, so poll on a timer of minutes. Better,
            register a <a href="/docs/webhooks" style={{ color: '#fff' }}>webhook</a> and we call you, signed,
            the moment it lands.
          </p>
        </section>
    </>
  )
}
