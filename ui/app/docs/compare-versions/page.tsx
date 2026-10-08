import type { Metadata } from 'next'
import { C, Code } from '../_ui'

export const metadata: Metadata = {
  title: 'Compare versions',
  description: 'GET /compare: regression between two runs of your system.',
  alternates: { canonical: 'https://senebiclabs.com/docs/compare-versions' },
  openGraph: { url: 'https://senebiclabs.com/docs/compare-versions' },
}

export default function Page() {
  return (
    <>
        <section>
          <span className="docs-kicker">Endpoints</span>
          <h1>
            <span className="m m-get">GET</span>
            <span className="ep">/compare</span>
            <span className="tag">Regression between two versions</span>
          </h1>
          <p>
            Evaluate a new model version against the same cases, then diff the two runs. Cases
            are matched on <C>your</C> case id, so the candidate run only needs to carry the same
            ids as the baseline.
          </p>
          <Code>{`curl "$BASE/compare?baseline=PROJECT_A&candidate=PROJECT_B" \\
  -H "Authorization: Bearer $API_KEY"`}</Code>
          <p>
            If a run was split across several projects (one per specialty, say), pass them all,
            comma-separated: <C>{`baseline=<id>,<id>&candidate=<id>`}</C>. Each side is pooled. A case
            id must be unique within a side; one that appears in two projects on the same side is
            refused with a <C>422</C> naming it, because pooling would silently keep only one of them.
          </p>
          <Code>{`{ "ok": true, "comparison": {
  "matched": 4,
  "pass_rate": { "baseline": 0.5, "candidate": 0.75, "delta": 0.25 },
  "fixed":     { "count": 2, "cases": [...] },
  "regressed": { "count": 1, "cases": [{ "case_id": "PMX-2", "severity": "Critical" }] },
  "clinical_deltas": { "missed_emergency": { "baseline": 1, "candidate": 0, "delta": -1 } },
  "verdict": { "recommendation": "block", "serious_regressions": 1,
               "still_failing_serious": 0,
               "reason": "1 case(s) that passed before now fail at Critical/High severity" }
}}`}</Code>
          <p>
            <C>fixed</C> and <C>regressed</C> are never netted off against each other. In the
            example above every headline metric improved and the verdict is still <C>block</C>,
            because one case that used to pass now fails critically — which is the entire reason
            to keep a regression suite. <C>recommendation</C> is <C>block</C> (a serious
            regression), <C>review</C> (a regression), or <C>pass</C>. It judges what the release{' '}
            <b>changed</b>. Known failures that are still failing do not move it, but they are never
            hidden: <C>still_failing_serious</C> counts those still failing at Critical/High severity,
            and <C>reason</C> names them. A <C>pass</C> with <C>still_failing_serious</C> above zero
            means &ldquo;nothing new broke&rdquo;, not &ldquo;safe to ship&rdquo;.
          </p>
          <p>
            Cases present in only one run are listed in <C>only_in_baseline</C> /{' '}
            <C>only_in_candidate</C> and excluded from the comparison, so a benchmark that quietly
            drops a case cannot manufacture a clean scorecard.
          </p>
        </section>
    </>
  )
}
