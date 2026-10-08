import { FOOTER } from '@/content/footer'

export default function FooterSection() {
  return (
    <footer>
      <div className="wrap">
        <div className="grid">
          <div>
            <a className="brand" href="/">Senebiclabs</a>
            <p style={{ marginTop: '18px', maxWidth: '300px', lineHeight: '1.6', color: 'var(--slate)', fontSize: '13.5px' }}>
              {FOOTER.tagline}
            </p>
          </div>
          <div>
            <h5>Platform</h5>
            <ul>
              {FOOTER.nav.platform.map((item) => (
                <li key={item.label}><a href={item.href}>{item.label}</a></li>
              ))}
            </ul>
          </div>
          <div>
            <h5>Company</h5>
            <ul>
              {FOOTER.nav.company.map((item) => (
                <li key={item.label}><a href={item.href}>{item.label}</a></li>
              ))}
            </ul>
          </div>
          <div>
            <h5>Legal</h5>
            <ul>
              {FOOTER.nav.legal.map((item) => (
                <li key={item.label}><a href={item.href}>{item.label}</a></li>
              ))}
            </ul>
          </div>
        </div>
        <div className="legal">
          <span>{FOOTER.legal}</span>
          <span style={{ fontFamily: 'var(--font-mono, monospace)', fontSize: 11, letterSpacing: '0.08em' }}>
            For research use only · Not a medical device · Does not constitute medical advice
          </span>
          <span>{FOOTER.legal2}</span>
        </div>
      </div>
    </footer>
  )
}
