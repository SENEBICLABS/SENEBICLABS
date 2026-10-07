import type { Metadata } from 'next'
import { C, Code } from '../_ui'

export const metadata: Metadata = {
  title: 'Authentication',
  description: 'Bearer API keys: how to get one, how to use it, how to revoke it.',
  alternates: { canonical: 'https://senebiclabs.com/docs/authentication' },
}

export default function Page() {
  return (
    <>
        <section>
          <span className="docs-eyebrow">Authentication</span>
          <h1>Bearer API key</h1>
          <p>Every request carries your API key as a bearer token:</p>
          <Code>{`Authorization: Bearer <YOUR_API_KEY>`}</Code>
          <p>
            <b>Get your key</b> at <a href="/developers" style={{ color: '#fff' }}>senebiclabs.com/developers</a>:
            verify your email and create one in seconds. Keys are shown once, tied to your account, and you
            can revoke any of them there at any time.
          </p>
          <p>
            Then <a href="/docs/create-a-project" style={{ color: '#fff' }}>create a project</a> with{' '}
            <C>POST /projects</C> and you get a <C>project_id</C> to push
            items to. One key can create and drive many projects.
          </p>
        </section>
    </>
  )
}
