import type { Metadata } from 'next'
import { C, Code } from '../_ui'

export const metadata: Metadata = {
  title: 'Poll status and results',
  description: 'GET /results: status while in review, and the report and reviewed items on delivery.',
  alternates: { canonical: 'https://senebiclabs.com/docs/poll-results' },
  openGraph: { url: 'https://senebiclabs.com/docs/poll-results' },
}

export default function Page() {
  return (
    <>
        <section>
          <span className="docs-kicker">Endpoints</span>
          <h1>
            <span className="m m-get">GET</span>
            <span className="ep">/results</span>
            <span className="tag">Poll status and results</span>
          </h1>
          <p>
            Clinician review is done by people, so results are not instant. Poll this endpoint.
            <C>status</C> moves through{' '}
            <C>received, in_review, delivered</C>, and <C>total</C> / <C>done</C> show progress.{' '}
            <C>received</C> means no clinician has started yet; it becomes <C>in_review</C> as soon
            as any item is being reviewed. Only <C>delivered</C> includes the report and items.
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
    "accuracy": { "value": 0.8, "correct": 160, "assessable": 200, "basis": "verdict+class" },
    "critical_misses": [ ... ],
    "per_class": { ... },
    "qa": { "mean_agreement": 0.86, "reviewed_items": 200, "disagreements": 12 }
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
          <p>
            The <C>assurance</C> block states who stands behind the result: licensed clinicians,
            independent of you and of the system under evaluation, with Senebiclabs accountable for
            the findings. Their identities are never disclosed.
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
            <p>
              <b>Free-text evaluations:</b> when the model&rsquo;s output is prose (an agent&rsquo;s
              answer, a RAG response) and the task has no <C>correct_label</C> field, as in{' '}
              <C>grounding_eval</C>, <C>reasoning_eval</C> and <C>triage_eval</C>, there is no class
              to correct to. <C>accuracy.value</C> is then the pass rate, the share of cases
              clinicians judged <C>Correct</C>, and every failure counts. <C>accuracy.basis</C> says
              which applies: <C>verdict</C> for free text, <C>verdict+class</C> when a corrected class
              is required. Free-text reports have no confusion matrix.
            </p>
          </div>
        </section>
    </>
  )
}
