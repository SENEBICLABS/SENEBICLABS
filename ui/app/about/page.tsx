import type { Metadata } from 'next'
import EvalNav from '../components/EvalNav'
import EvalFooter from '../components/EvalFooter'

export const metadata: Metadata = {
  title: 'About · Senebiclabs',
  description:
    'Senebiclabs is the data layer under medical AI. Licensed clinicians review the data medical models are trained and tested on, with a record of who decided what.',
  alternates: { canonical: '/about' },
  openGraph: {
    title: 'About · Senebiclabs',
    description: 'Who we are and why we build the clinician layer under medical AI.',
    url: 'https://senebiclabs.com/about',
  },
}

const SANS = "'Geist', system-ui, -apple-system, sans-serif"
const READ = "'DM Sans', system-ui, sans-serif"

const T_DISPLAY: React.CSSProperties = { fontFamily: SANS, fontWeight: 600, fontSize: 'clamp(40px, 6.4vw, 88px)', letterSpacing: '-0.04em', lineHeight: 1.0 }
const T_H2: React.CSSProperties = { fontFamily: SANS, fontWeight: 600, fontSize: 'clamp(28px, 3.8vw, 48px)', letterSpacing: '-0.03em', lineHeight: 1.08 }
const T_H3: React.CSSProperties = { fontFamily: SANS, fontWeight: 600, fontSize: 'clamp(19px, 1.7vw, 22px)', letterSpacing: '-0.02em', lineHeight: 1.28 }
const T_LEAD: React.CSSProperties = { fontFamily: READ, fontWeight: 400, fontSize: 'clamp(17px, 1.6vw, 20px)', lineHeight: 1.7, color: 'rgba(255,255,255,0.74)' }
const T_BODY: React.CSSProperties = { fontFamily: READ, fontWeight: 400, fontSize: 15.5, lineHeight: 1.7, color: 'rgba(255,255,255,0.62)' }

const PAD_SECTION = 'clamp(80px, 11vw, 140px) 0'
const GAP_GRID = 'clamp(48px, 6vw, 72px)'
const PAD_CELL = 'clamp(38px, 4vw, 52px) clamp(28px, 3vw, 38px)'
const WIDE: React.CSSProperties = { maxWidth: 1440, margin: '0 auto', padding: '0 clamp(24px, 4vw, 60px)' }
const SECTION: React.CSSProperties = { padding: PAD_SECTION, borderTop: '1px solid var(--hairline)' }
const HEAD: React.CSSProperties = { textAlign: 'center', maxWidth: 820, margin: '0 auto' }
const CELL: React.CSSProperties = { border: '1px solid var(--hairline)', background: 'var(--navy)', padding: PAD_CELL, display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center' }

function Head({ tag, title, sub }: { tag: string; title: string; sub?: string }) {
  return (
    <div style={HEAD}>
      <span className="iso-label" style={{ display: 'inline-block', marginBottom: 24 }}>{tag}</span>
      <h2 style={{ ...T_H2, margin: 0, textWrap: 'balance' }}>{title}</h2>
      {sub && <p style={{ ...T_LEAD, margin: '24px auto 0', maxWidth: 660 }}>{sub}</p>}
    </div>
  )
}

function Prose({ children }: { children: React.ReactNode }) {
  return <div style={{ maxWidth: 720, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 22 }}>{children}</div>
}

export default function AboutPage() {
  return (
    <>
      <EvalNav />

      {/* HERO  */}
      <section style={{ padding: 'clamp(112px, 13vw, 168px) 0 clamp(56px, 7vw, 88px)' }}>
        <div style={{ ...WIDE, textAlign: 'center' }}>
          <span className="iso-label" style={{ display: 'inline-block', marginBottom: 30 }}>About Senebiclabs</span>
          <h1 style={{ ...T_DISPLAY, maxWidth: 1080, margin: '0 auto', textWrap: 'balance' }}>
            Clinician-reviewed data for medical AI.
          </h1>
          <p style={{ ...T_LEAD, fontSize: 'clamp(18px, 1.9vw, 23px)', maxWidth: 780, margin: '34px auto 0' }}>
            Senebiclabs is the data layer under medical AI. We have licensed clinicians review the
            data your models learn from and get tested against, so it was checked by people who
            actually understand medicine.
          </p>
        </div>
      </section>

      {/* WHY WE EXIST  */}
      <section style={SECTION}>
        <div style={WIDE}>
          <Head tag="Why we exist" title="Most medical AI is built on data no clinician has checked." />
          <div style={{ marginTop: GAP_GRID }}>
            <Prose>
              <p style={{ ...T_LEAD, textAlign: 'center' }}>
                A medical model is only as good as the data behind it. Most of that data gets
                labeled by crowd workers with no medical training, or graded by another model.
              </p>
              <p style={{ ...T_BODY, fontSize: 17, textAlign: 'center' }}>
                That is fine for a demo. It becomes a problem the moment a hospital, a regulator, or
                an investor asks how you know the model is right. We started Senebiclabs to close
                that gap. Real clinicians check the data, every decision is recorded, and you get
                back something you can actually defend.
              </p>
            </Prose>
          </div>
        </div>
      </section>

      {/* WHAT WE DO  */}
      <section style={SECTION}>
        <div style={WIDE}>
          <Head tag="What we do" title="The work"
                sub="Whatever stage your model is at, we do the clinical data work behind it." />
          <div className="blocks-3col" style={{ marginTop: GAP_GRID }}>
            {[
              { n: '01', t: 'Evaluate', d: "We grade your model's answers against a clinician's read, and give you a report that shows every mistake, including the ones that matter clinically." },
              { n: '02', t: 'Label', d: 'We turn raw medical data into labeled ground truth. Imaging, clinical text, and classification, reviewed one case at a time.' },
              { n: '03', t: 'Create', d: 'We write the gold answers, preference data, and test sets you fine-tune and check your model against.' },
            ].map((c, i) => (
              <div key={c.n} style={{ ...CELL, marginLeft: i % 3 === 0 ? 0 : -1 }}>
                <span className="iso-label" style={{ marginBottom: 24 }}>{c.n}</span>
                <h3 style={{ ...T_H3, margin: '0 0 10px' }}>{c.t}</h3>
                <p style={{ ...T_BODY, margin: 0, maxWidth: 340 }}>{c.d}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* HOW WE KEEP IT TRUSTWORTHY  */}
      <section style={SECTION}>
        <div style={WIDE}>
          <Head tag="The standard" title="How we keep it trustworthy" />
          <div className="cards-2col" style={{ marginTop: GAP_GRID, gap: 1, background: 'var(--hairline)' }}>
            {[
              { t: 'Clinicians, not crowds', d: 'Every case is reviewed by a licensed clinician. On the work that matters, several people review the same case and we show you how often they agreed.' },
              { t: 'Accountable, and private', d: 'Every case is reviewed by a credentialed clinician, and we keep our own record of what was decided and when. To you, reviewers stay anonymous; what you can show a safety board is the process: qualified specialists reviewed your data, with a full audit trail behind it.' },
              { t: 'Your data stays yours', d: 'We remove identifying details before anyone sees a case, and we keep each client walled off from the rest. The system enforces it, not a line in a contract.' },
              { t: 'We do not oversell the numbers', d: "We tell you how many cases a figure is based on, where reviewers disagreed, and what we couldn't assess. If a result is thin, we say so." },
            ].map((p) => (
              <div key={p.t} style={{ background: 'var(--navy)', padding: PAD_CELL, display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center' }}>
                <h3 style={{ ...T_H3, margin: '0 0 12px' }}>{p.t}</h3>
                <p style={{ ...T_BODY, margin: 0, maxWidth: 360 }}>{p.d}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* WHERE WE'RE GOING  */}
      <section style={SECTION}>
        <div style={WIDE}>
          <Head
            tag="Where we're going"
            title="One data layer for medical AI"
            sub="We start with the work only clinicians can do: judging, checking, and writing medical data. Over time we want to cover the whole data stack a medical model needs, across imaging, pathology, clinical text, genomics, and medical language models."
          />
        </div>
      </section>

      {/* CLOSE  */}
      <section style={SECTION}>
        <div style={{ ...WIDE, textAlign: 'center' }}>
          <h2 style={{ ...T_H2, maxWidth: 720, margin: '0 auto', textWrap: 'balance' }}>
            Data your medical AI can stand on.
          </h2>
          <p style={{ ...T_LEAD, maxWidth: 540, margin: '24px auto 0' }}>
            Start small, see if it holds up, then scale. Book a demo, or read the docs.
          </p>
          <div style={{ display: 'flex', gap: 18, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'center', marginTop: 44 }}>
            <a href="/submit" className="nav-join-cta">Book a demo →</a>
            <a href="/docs" className="iso-cta" style={{ fontSize: 12 }}>Read the docs →</a>
          </div>
        </div>
      </section>

      <EvalFooter />
    </>
  )
}
