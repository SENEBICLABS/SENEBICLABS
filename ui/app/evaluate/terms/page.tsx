import NavBar from '../../components/NavBar'
import EvalFooter from '../../components/EvalFooter'

const DISPLAY = '"SF Pro Display", -apple-system, BlinkMacSystemFont, system-ui, sans-serif'
const BODY = '"DM Sans", system-ui, sans-serif'

const h2: React.CSSProperties = {
  fontFamily: DISPLAY, fontWeight: 300, fontSize: 'clamp(20px, 2.2vw, 26px)',
  letterSpacing: '0.01em', margin: '40px 0 14px',
}
const p: React.CSSProperties = {
  fontFamily: BODY, fontWeight: 300, fontSize: 16, color: 'var(--ink)', lineHeight: 1.8, marginBottom: 14,
}

const SECTIONS: { h: string; body: string[] }[] = [
  { h: 'The service', body: [
    'Senebiclabs provides evaluation of AI model outputs by credentialed clinicians, delivered as scored data, flags, and written rationale.',
  ]},
  { h: 'Your responsibilities', body: [
    'You agree to provide accurate data and to de-identify any sensitive or patient information before submitting it.',
    'You confirm you have the right to share the data you send us for evaluation.',
  ]},
  { h: 'Ownership', body: [
    'You retain ownership of the data you submit and of the evaluation results we deliver to you.',
    'We do not claim rights over your data or reuse it beyond delivering your project.',
  ]},
  { h: 'Nature of the evaluation', body: [
    'Our evaluations are expert clinical judgement provided to inform your work. They are not a regulatory approval, a clinical endorsement, or a substitute for your own diligence.',
    'You remain responsible for your product and for any decisions you make based on our results.',
  ]},
  { h: 'Confidentiality', body: [
    'We keep your data and project confidential. We expect the same in return for any non-public information we share with you.',
  ]},
  { h: 'Liability', body: [
    'The service is provided in good faith and to a professional standard. To the extent permitted by law, our liability is limited to the fees paid for the project in question.',
  ]},
  { h: 'Contact', body: [
    'Questions about these terms can be sent to support@senebiclabs.com.',
  ]},
]

export default function EvalTermsPage() {
  return (
    <>
      <NavBar minimal />
      <section style={{ padding: 'clamp(140px, 16vw, 200px) 0 clamp(48px, 7vw, 80px)' }}>
        <div className="wrap" style={{ maxWidth: 720 }}>
          <span className="iso-label" style={{ display: 'inline-block', marginBottom: 24 }}>Terms of Service</span>
          <h1 style={{ fontFamily: DISPLAY, fontWeight: 100, fontSize: 'clamp(34px, 4.5vw, 52px)', letterSpacing: '0.03em', lineHeight: 1.1, marginBottom: 18 }}>
            Working with Senebiclabs.
          </h1>
          <p style={{ ...p, color: 'var(--slate)' }}>Last updated: June 2026</p>
          <p style={p}>
            These terms cover your use of Senebiclabs&rsquo; clinician evaluation service.
            By starting a pilot or project with us, you agree to them.
          </p>
          {SECTIONS.map(s => (
            <div key={s.h}>
              <h2 style={h2}>{s.h}</h2>
              {s.body.map((b, i) => <p key={i} style={p}>{b}</p>)}
            </div>
          ))}
        </div>
      </section>
      <EvalFooter />
    </>
  )
}
