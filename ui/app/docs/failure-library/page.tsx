import type { Metadata } from 'next'
import { C, Code } from '../_ui'

export const metadata: Metadata = {
  title: 'Failure library',
  description: 'Record failures, query patterns, promote them to a benchmark and re-run the suite.',
  alternates: { canonical: 'https://senebiclabs.com/docs/failure-library' },
  openGraph: { url: 'https://senebiclabs.com/docs/failure-library' },
}

export default function Page() {
  return (
    <>
        <section>
          <span className="docs-kicker">Endpoints</span>
          <h1>
            <span className="ep">Failure library</span>
            <span className="tag">Memory across versions</span>
          </h1>
          <p>
            The endpoints so far evaluate one version. These give the evaluation a memory: what
            your model always gets wrong, whether a fix held, and a permanent suite every
            future release is tested against. Tag a run with{' '}
            <C>model_version</C> on <C>POST /projects</C>.
          </p>
          <h3>Record a finished evaluation</h3>
          <Code>{`curl -X POST "$BASE/failures/capture" \\
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \\
  -d '{ "project_id": "...", "model_version": "v2.3" }'

{ "ok": true, "recorded": 12, "fixed": 4, "regressed": 1 }`}</Code>
          <p>
            Failures are upserted by <C>your</C> case id, so a case that keeps failing
            accumulates <C>occurrences</C> rather than duplicating. Cases this run passed that
            the library holds open are closed as <C>fixed</C>, tagged with the version that
            fixed them. A case that was fixed and fails again becomes <C>regressed</C>, never
            plain <C>open</C> — a fix that did not hold is the most important thing the library
            knows.
          </p>
          <h3>Query and aggregate</h3>
          <Code>{`GET $BASE/failures?severity=Critical&clinical_domain=Respiratory&status=open
GET $BASE/failures/patterns

{ "patterns": {
  "by_status": { "open": 61, "fixed": 22, "regressed": 4 },
  "by_error_category": { "Missed red flag": 19, "Triage failure": 14 },
  "serious_by_domain": { "Respiratory": 12, "Cardiac": 9 },
  "recurring": [ { "case_key": "PMX-47", "occurrences": 3, "status": "regressed" } ]
}}`}</Code>
          <h3>Promote, then re-run</h3>
          <Code>{`POST $BASE/benchmark/promote  { "min_severity": "High" }
POST $BASE/benchmark/run      { "model_version": "v2.4" }

{ "ok": true, "project_id": "...", "cases": 34, "compare_with": "<id>,<id>",
  "runs": [ { "project_id": "...", "cases": 34, "compare_with": "<id>,<id>" } ] }`}</Code>
          <p>
            <C>benchmark/run</C> seeds a new evaluation with every benchmark case, replaying each
            stored input verbatim — a regression test is only a test if the input does not drift
            between runs. Cases are graded by the same task config that found them. Cases from
            several projects graded the same way go into one run, and <C>compare_with</C> lists all
            of those projects, ready to pass to <C>/compare</C>. If your benchmark mixes tasks graded
            differently (a triage eval and a grounding eval, say), you get one run per task in{' '}
            <C>runs</C>, each with its own <C>compare_with</C>, and the top-level <C>project_id</C> is
            omitted.
          </p>
          <h3>The release gate, end to end</h3>
          <Code>{`POST /benchmark/run      { "model_version": "v2.4" }   -> project_id, compare_with
GET  /results?project_id=...                           -> poll until delivered
GET  /compare?baseline=<compare_with>&candidate=<project_id>
                                                       -> verdict: block | review | pass
                                   (with several runs: one /compare per entry in \`runs\`)
POST /failures/capture   { "project_id": "...", "model_version": "v2.4" }`}</Code>
          <p>
            Four calls. The last folds the new results back into the library, so the next release
            is tested against everything learned so far.
          </p>
        </section>
    </>
  )
}
