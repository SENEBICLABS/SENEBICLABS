'use client'

import { useState } from 'react'
import Logo from './Logo'

type Page = 'home' | 'patients' | 'about' | 'analyse' | 'experts' | 'contribute' | 'aiteams'

const LINKS: { label: string; href: string; page: Page }[] = [
  { label: 'Patients',    href: '/patients',   page: 'patients'   },
  { label: 'Specialists', href: '/experts',    page: 'experts'    },
  { label: 'For AI teams', href: '/evaluate', page: 'aiteams'   },
  { label: 'Contribute',  href: '/contribute', page: 'contribute' },
  { label: 'About',       href: '/about',      page: 'about'      },
]

// `minimal` = the eval-business funnel: no links into the science side,
// brand stays within /evaluate, single "Book a demo" CTA.
export default function NavBar({ active, onLight, minimal }: { active?: Page; onLight?: boolean; minimal?: boolean }) {
  const [open, setOpen] = useState(false)

  const links = minimal ? [] : LINKS
  const brandHref = '/'
  const ctaHref = minimal ? '/submit' : '/experts'
  const ctaLabel = minimal ? 'Book a demo →' : 'Join as specialist →'

  return (
    <nav className={`top scrolled${open ? ' nav-open' : ''}${onLight ? ' nav-on-light' : ''}`}>
      <div className="wrap row">

        <a className="brand" href={brandHref} onClick={() => setOpen(false)}>
          <span className="mark"><Logo size={20} /></span>
          Senebiclabs
        </a>

        {/* Desktop links  */}
        {!minimal && (
          <div className="nav-links">
            {links.map(l => (
              <a key={l.page} href={l.href} style={active === l.page ? { color: 'var(--ink)' } : {}}>
                {l.label}
              </a>
            ))}
          </div>
        )}

        {/* Right side: CTA, plus hamburger only when there are links  */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, gridColumn: 3 }}>
          <a href={ctaHref} className={minimal ? 'nav-join-cta' : 'nav-join-cta nav-cta-desktop'}>{ctaLabel}</a>
          {!minimal && (
            <button
              className="nav-hamburger"
              onClick={() => setOpen(o => !o)}
              aria-label={open ? 'Close menu' : 'Open menu'}
            >
              {open ? (
                <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
                  <path d="M2 2l14 14M16 2L2 16" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/>
                </svg>
              ) : (
                <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
                  <path d="M2 4h14M2 9h14M2 14h14" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/>
                </svg>
              )}
            </button>
          )}
        </div>

      </div>

      {/* Mobile menu (science nav only)  */}
      {!minimal && open && (
        <div className="nav-mobile-menu">
          {links.map(l => (
            <a
              key={l.page}
              href={l.href}
              className={active === l.page ? 'nav-mobile-active' : ''}
              onClick={() => setOpen(false)}
            >
              {l.label}
            </a>
          ))}
          <a href={ctaHref} className="nav-join-cta" onClick={() => setOpen(false)}>
            {ctaLabel}
          </a>
        </div>
      )}
    </nav>
  )
}
