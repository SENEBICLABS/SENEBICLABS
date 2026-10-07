'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { PAGES, href } from './_nav'

// The sidebar now navigates between pages, so "where am I" is the route rather
// than whatever section happens to be in view.
export default function DocsSidebar() {
  const path = usePathname()
  return (
    <nav className="docs-nav">
      {PAGES.map(p => (
        <span key={p.slug || 'overview'} style={{ display: 'contents' }}>
          {p.group && <span className="grp">{p.group}</span>}
          <Link href={href(p.slug)} className={path === href(p.slug) ? 'active' : undefined}>
            {p.label}
          </Link>
        </span>
      ))}
    </nav>
  )
}
