import type { Metadata } from 'next'
import { BASE, C, Code } from './_ui'

export const metadata: Metadata = {
  title: { absolute: 'Senebiclabs API reference' },
  description: 'Senebiclabs API overview: the three project purposes, the base URL, and where to start.',
  alternates: { canonical: 'https://senebiclabs.com/docs' },
  openGraph: { url: 'https://senebiclabs.com/docs' },
}

export default function Page() {
  return (
    <>
        <section>
          <span className="docs-kicker">Get started</span>
          <h1 className="docs-title">Clinician-grade data for medical AI</h1>
          <p className="docs-tagline">
            Evaluate, benchmark and label your medical models through the Senebiclabs API.
          </p>
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

          <div className="docs-cards">
            <a className="docs-card" href="/docs/quickstart">
              <span className="t">Quickstart</span>
              <span className="d">Three calls, from a key to a delivered report.</span>
            </a>
            <a className="docs-card" href="/docs/authentication">
              <span className="t">Authentication</span>
              <span className="d">Bearer keys, how to get one and how to revoke it.</span>
            </a>
            <a className="docs-card" href="/docs/create-a-project">
              <span className="t">Templates</span>
              <span className="d">Pick an outcome and we build the project for you.</span>
            </a>
            <a className="docs-card" href="/docs/webhooks">
              <span className="t">Webhooks</span>
              <span className="d">Signed delivery, so you do not have to poll.</span>
            </a>
          </div>

          <h3>Base URL</h3>
          <Code>{BASE}</Code>
        </section>
    </>
  )
}
