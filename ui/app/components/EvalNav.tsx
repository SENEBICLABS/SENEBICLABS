'use client'

import { useState } from 'react'
import Logo from './Logo'

// Nav for the data-business (eval) side. Reachable from ANY page: the homepage-section
// links are absolute (/#...) so they work off the homepage, the pages (Docs, About) are
// always one click away, and there is a mobile menu. No links into the science side.
const LINKS: { label: string; href: string }[] = [
  { label: 'What we do', href: '/#what-you-get' },
  { label: 'How it works', href: '/#how' },
  { label: 'Docs', href: '/docs' },
  { label: 'About', href: '/about' },
]

export default function EvalNav() {
  const [open, setOpen] = useState(false)

  return (
    <nav className={`top scrolled${open ? ' nav-open' : ''}`}>
      <div className="wrap row">

        <a className="brand" href="/" onClick={() => setOpen(false)}>
          <span className="mark"><Logo size={20} /></span>
          Senebiclabs
        </a>

        {/* Desktop links  */}
        <div className="nav-links">
          {LINKS.map((l) => (
            <a key={l.href} href={l.href}>{l.label}</a>
          ))}
        </div>

        {/* CTA (desktop) + hamburger (mobile)  */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, gridColumn: 3 }}>
          <a href="/submit" className="nav-join-cta nav-cta-desktop">Book a demo →</a>
          <button
            className="nav-hamburger"
            onClick={() => setOpen((o) => !o)}
            aria-label={open ? 'Close menu' : 'Open menu'}
          >
            {open ? (
              <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
                <path d="M2 2l14 14M16 2L2 16" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
              </svg>
            ) : (
              <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
                <path d="M2 4h14M2 9h14M2 14h14" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
              </svg>
            )}
          </button>
        </div>

      </div>

      {/* Mobile menu  */}
      {open && (
        <div className="nav-mobile-menu">
          {LINKS.map((l) => (
            <a key={l.href} href={l.href} onClick={() => setOpen(false)}>{l.label}</a>
          ))}
          <a href="/submit" className="nav-join-cta" onClick={() => setOpen(false)}>Book a demo →</a>
        </div>
      )}
    </nav>
  )
}
