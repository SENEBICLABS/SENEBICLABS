import type { Metadata } from 'next'
import { C } from '../_ui'

export const metadata: Metadata = {
  title: 'Errors and notes',
  description: 'Status codes, idempotency, and the shape of an item.',
  alternates: { canonical: 'https://senebiclabs.com/docs/errors' },
  openGraph: { url: 'https://senebiclabs.com/docs/errors' },
}

export default function Page() {
  return (
    <>
        <section>
          <span className="docs-kicker">Reference</span>
          <h1>Errors and notes</h1>
          <ul>
            <li><b>Errors:</b> <C>401</C> invalid or missing key, <C>403</C> project not on this key, <C>422</C> invalid config or items missing a required field, <C>503</C> service unavailable.</li>
            <li><b>Idempotency:</b> send an <C>Idempotency-Key</C> header per batch so retries are safe. Without one, each <C>/ingest</C> appends its items.</li>
            <li><b>Content shape</b> is up to you as long as it matches the configured task. For an evaluation, the item carries the input the model responded to (<C>scenario</C>) and the model&rsquo;s output as <C>prediction</C>, which the scoring contract requires. Add <C>case_id</C> to tie results back to your own records.</li>
          </ul>
          <div className="docs-foot">
            <span className="docs-eyebrow">Questions? senebiclabs@gmail.com</span>
          </div>
        </section>
    </>
  )
}
