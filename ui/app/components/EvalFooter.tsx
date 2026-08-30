// Structured footer for the eval-business funnel, same layout as the site footer,
// no links to the science side.
const NAV = {
  product: [
    { label: 'What we do', href: '/#what-you-get' },
    { label: 'Modalities', href: '/#modalities' },
    { label: 'API docs', href: '/docs' },
    { label: 'Get an API key', href: '/developers' },
    { label: 'Book a demo', href: '/submit' },
  ],
  company: [
    { label: 'About', href: '/about' },
    { label: 'Research', href: '/research' },
    { label: 'Contact', href: 'mailto:senebiclabs@gmail.com' },
  ],
  legal: [
    { label: 'Privacy', href: '/evaluate/privacy' },
    { label: 'Terms', href: '/evaluate/terms' },
  ],
}

export default function EvalFooter() {
  return (
    <footer>
      <div className="wrap">
        <div className="grid">
          <div>
            <a className="brand" href="/">Senebiclabs</a>
            <p style={{ marginTop: '18px', maxWidth: '320px', lineHeight: '1.6', color: 'var(--slate)', fontSize: '13.5px' }}>
              The clinician-grade data layer for medical AI: labeling, evaluation, and RLHF.
            </p>
          </div>
          <div>
            <h5>Product</h5>
            <ul>
              {NAV.product.map((item) => (
                <li key={item.label}><a href={item.href}>{item.label}</a></li>
              ))}
            </ul>
          </div>
          <div>
            <h5>Company</h5>
            <ul>
              {NAV.company.map((item) => (
                <li key={item.label}><a href={item.href}>{item.label}</a></li>
              ))}
            </ul>
          </div>
          <div>
            <h5>Legal</h5>
            <ul>
              {NAV.legal.map((item) => (
                <li key={item.label}><a href={item.href}>{item.label}</a></li>
              ))}
            </ul>
          </div>
        </div>
        <div className="legal">
          <span>© 2026 Senebiclabs Inc. · All rights reserved</span>
          <span>Clinician-reviewed · De-identified · Isolated per client</span>
        </div>
      </div>
    </footer>
  )
}
