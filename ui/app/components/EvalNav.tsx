import Logo from './Logo'

// Conversion nav for the /evaluate sell page. Brand-consistent with the site nav
// (same .top pill, hairline, fonts, monochrome) but stripped for selling: one exit
// home via the logo, two in-page anchors to the proof, one persistent CTA. No site
// directory links (they are exits away from converting), no hamburger.
export default function EvalNav() {
  return (
    <nav className="top scrolled">
      <div className="wrap row">

        {/* The one clean exit: logo -> home  */}
        <a className="brand" href="/">
          <span className="mark"><Logo size={20} /></span>
          Senebiclabs
        </a>

        {/* In-page anchors to the proof (desktop only; .nav-links hides under 768px).
            All four are on-page section jumps, About is this service's #about, not the
            main-site company About. No directory links, no dropdowns.  */}
        <div className="nav-links">
          <a href="#what-you-get">What we do</a>
          <a href="#how">How it works</a>
          <a href="#modalities">Modalities</a>
          <a href="#why-us">Why us</a>
        </div>

        {/* Persistent CTA, always visible, including mobile. API link sits beside it so
            developer visitors (a qualified audience) can reach the docs from any viewport. */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, gridColumn: 3 }}>
          <a
            href="/docs"
            style={{
              fontFamily: "'Geist Mono', monospace",
              fontSize: 11,
              letterSpacing: '0.12em',
              textTransform: 'uppercase',
              color: 'var(--ink)',
              textDecoration: 'none',
              opacity: 0.72,
            }}
          >
            API
          </a>
          <a href="/submit" className="nav-join-cta">Book a demo →</a>
        </div>

      </div>
    </nav>
  )
}
