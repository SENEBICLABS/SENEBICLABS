import type { Metadata } from 'next'
import { Instrument_Sans } from 'next/font/google'
import './docs.css'
import DocsSidebar from './DocsSidebar'
import DocsPrevNext from './DocsPrevNext'
import DocsPageActions from './DocsPageActions'
import DocsEnhance from './DocsEnhance'

// Pages name only themselves; this adds the suffix once, instead of every page
// carrying it and the root layout adding a second one on top.
export const metadata: Metadata = {
  title: {
    default: 'Senebiclabs API reference',
    template: '%s · Senebiclabs API',
  },
}

// The reference reads as a reference, not as the marketing site. Instrument Sans
// is the face Mintlify-style API docs use, scoped to this route so the rest of
// senebiclabs.com keeps Geist and DM Sans.
const instrument = Instrument_Sans({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700'],
  variable: '--docs-sans',
  display: 'swap',
})

export default function DocsLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className={instrument.variable}>
      <header className="docs-topbar">
        <div className="docs-topbar-in">
          <span className="docs-brand">
            Senebiclabs
            <span className="docs-brand-sub">API reference</span>
          </span>
          <button type="button" className="docs-searchbtn" data-open-search>
            <span>Search the reference</span>
            <kbd>⌘K</kbd>
          </button>
          <a href="/" className="docs-toplink">Website</a>
          <a href="/developers" className="docs-cta">Get an API key →</a>
        </div>
      </header>

      <div className="docs-shell">
        <aside className="docs-side">
          <DocsSidebar />
        </aside>

        <main className="docs-main">
          <DocsPageActions />
          {children}
          <DocsPrevNext />
          <div className="docs-foot">
            <span className="docs-eyebrow">Questions? senebiclabs@gmail.com</span>
          </div>
        </main>
      </div>

      <div id="docs-search" role="dialog" aria-modal="true" aria-label="Search the reference">
        <div className="docs-search-panel">
          <input
            id="docs-search-input"
            type="text"
            placeholder="Search the reference…"
            autoComplete="off"
            spellCheck={false}
          />
          <div id="docs-search-results" />
          <div className="docs-search-foot">
            <span>↑↓ navigate</span>
            <span>↵ open</span>
            <span>esc close</span>
          </div>
        </div>
      </div>

      <DocsEnhance />
    </div>
  )
}
