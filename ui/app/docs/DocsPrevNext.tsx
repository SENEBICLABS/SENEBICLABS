'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { PAGES, href } from './_nav'

// Somewhere to go at the bottom of a page. Without it, a split reference makes a
// reader climb back to the sidebar after every page.
export default function DocsPrevNext() {
  const path = usePathname()
  const i = PAGES.findIndex(p => href(p.slug) === path)
  if (i < 0) return null
  const prev = PAGES[i - 1]
  const next = PAGES[i + 1]
  if (!prev && !next) return null

  return (
    <nav className="docs-pager" aria-label="Previous and next">
      {prev ? (
        <Link href={href(prev.slug)} className="pg pg-prev">
          <span className="pg-k">Previous</span>
          <span className="pg-t">{prev.label}</span>
        </Link>
      ) : <span />}
      {next && (
        <Link href={href(next.slug)} className="pg pg-next">
          <span className="pg-k">Next</span>
          <span className="pg-t">{next.label}</span>
        </Link>
      )}
    </nav>
  )
}
