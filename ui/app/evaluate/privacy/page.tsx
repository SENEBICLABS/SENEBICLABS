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
  { h: 'What we collect', body: [
    'Account information you give us: your name, work email, and company.',
    'Evaluation data: the model prompts and outputs you submit to us for clinical review, and any rubric or instructions you provide.',
  ]},
  { h: 'How we use it', body: [
    'We use your evaluation data solely to perform the review you request and to deliver your results.',
    'We do not use your data to train our own models, and we do not repurpose it for anything beyond your project.',
  ]},
  { h: 'Confidentiality and security', body: [
    'Your data is treated as confidential. Access is restricted to the clinicians assigned to your project and our operators, all under confidentiality obligations.',
    'Data is stored on access-controlled, encrypted infrastructure and is never sold or shared with third parties for their own use.',
  ]},
  { h: 'Sensitive and health data', body: [
    'You are responsible for de-identifying data before sending it to us. Please remove patient identifiers from any prompts or outputs.',
    'We handle all submitted data as confidential regardless, but we do not seek to receive personally identifiable health information.',
  ]},
  { h: 'Retention and deletion', body: [
    'We retain your evaluation data only as long as needed to complete and deliver your project.',
    'You can request deletion of your data at any time, and we will remove it from our systems.',
  ]},
  { h: 'Sub-processors', body: [
    'We rely on trusted infrastructure providers for storage and on our network of contracted, credentialed clinicians for the review work. All are bound by confidentiality terms.',
  ]},
  { h: 'Your rights and contact', body: [
    'You can request access to, correction of, or deletion of your data at any time. For any privacy question, contact us at support@senebiclabs.com.',
  ]},
]

export default function EvalPrivacyPage() {
  return (
    <>
      <NavBar minimal />
      <section style={{ padding: 'clamp(140px, 16vw, 200px) 0 clamp(48px, 7vw, 80px)' }}>
        <div className="wrap" style={{ maxWidth: 720 }}>
          <span className="iso-label" style={{ display: 'inline-block', marginBottom: 24 }}>Privacy Policy</span>
          <h1 style={{ fontFamily: DISPLAY, fontWeight: 100, fontSize: 'clamp(34px, 4.5vw, 52px)', letterSpacing: '0.03em', lineHeight: 1.1, marginBottom: 18 }}>
            How we handle your data.
          </h1>
          <p style={{ ...p, color: 'var(--slate)' }}>Last updated: June 2026</p>
          <p style={p}>
            Senebiclabs provides clinician evaluation of AI outputs for medical and scientific AI teams.
            This policy explains what data you share with us and how we treat it.
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
