import type { Metadata } from 'next'
import EvalNav from './components/EvalNav'
import EvalFooter from './components/EvalFooter'

export const metadata: Metadata = {
  title: 'Clinician-grade data for medical AI · Senebiclabs',
  description:
    'Senebiclabs is the data infrastructure under medical AI. Licensed clinicians label, evaluate, and create the data models are trained, aligned, and tested on, delivered by API, isolated per client, and traceable to a name.',
  alternates: { canonical: '/' },
  openGraph: {
    title: 'Clinician-grade data for medical AI · Senebiclabs',
    description: 'The clinician layer underneath medical AI: label, evaluate, and create the data your models depend on.',
    url: 'https://senebiclabs.com',
  },
}

// Geist (brand) for headings, DM Sans to read, Geist Mono for labels.
// Confident bold weights (as Scale / Heyrafiki use), centered throughout.
const SANS = "'Geist', system-ui, -apple-system, sans-serif"
const READ = "'DM Sans', system-ui, sans-serif"

const T_DISPLAY: React.CSSProperties = { fontFamily: SANS, fontWeight: 600, fontSize: 'clamp(46px, 7.6vw, 108px)', letterSpacing: '-0.04em', lineHeight: 0.97 }
const T_H2: React.CSSProperties = { fontFamily: SANS, fontWeight: 600, fontSize: 'clamp(28px, 3.8vw, 48px)', letterSpacing: '-0.03em', lineHeight: 1.08 }
const T_H3: React.CSSProperties = { fontFamily: SANS, fontWeight: 600, fontSize: 'clamp(19px, 1.7vw, 22px)', letterSpacing: '-0.02em', lineHeight: 1.28 }
const T_LEAD: React.CSSProperties = { fontFamily: READ, fontWeight: 400, fontSize: 'clamp(17px, 1.6vw, 20px)', lineHeight: 1.6, color: 'rgba(255,255,255,0.74)' }
const T_BODY: React.CSSProperties = { fontFamily: READ, fontWeight: 400, fontSize: 15.5, lineHeight: 1.66, color: 'rgba(255,255,255,0.62)' }

const PAD_SECTION = 'clamp(92px, 12vw, 152px) 0'
const GAP_GRID = 'clamp(48px, 6vw, 72px)'
const PAD_CELL = 'clamp(38px, 4vw, 52px) clamp(28px, 3vw, 38px)'

// Wider than the shared .wrap (1100) so the homepage uses the page, not a narrow column.
const WIDE: React.CSSProperties = { maxWidth: 1440, margin: '0 auto', padding: '0 clamp(24px, 4vw, 60px)' }
const SECTION: React.CSSProperties = { padding: PAD_SECTION, borderTop: '1px solid var(--hairline)' }
const HEAD: React.CSSProperties = { textAlign: 'center', maxWidth: 760, margin: '0 auto' }
const CELL: React.CSSProperties = { border: '1px solid var(--hairline)', background: 'var(--navy)', padding: PAD_CELL, display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center' }

function Head({ tag, title, sub }: { tag: string; title: string; sub?: string }) {
  return (
    <div style={HEAD}>
      <span className="iso-label" style={{ display: 'inline-block', marginBottom: 24 }}>{tag}</span>
      <h2 style={{ ...T_H2, margin: 0, textWrap: 'balance' }}>{title}</h2>
      {sub && <p style={{ ...T_LEAD, margin: '24px auto 0', maxWidth: 600 }}>{sub}</p>}
    </div>
  )
}

function Cell({ tag, title, body }: { tag?: string; title: string; body: string }) {
  return (
    <>
      {tag && <span className="iso-label" style={{ marginBottom: 24 }}>{tag}</span>}
      <h3 style={{ ...T_H3, margin: '0 0 10px' }}>{title}</h3>
      <p style={{ ...T_BODY, margin: 0, maxWidth: 340 }}>{body}</p>
    </>
  )
}

function Ctas() {
  return (
    <div style={{ display: 'flex', gap: 18, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'center' }}>
      <a href="/submit" className="nav-join-cta">Book a demo →</a>
      <a href="/docs" className="iso-cta" style={{ fontSize: 12 }}>Read the docs →</a>
    </div>
  )
}

export default function HomePage() {
  return (
    <>
      <EvalNav />

      {/* HERO  */}
      <section style={{ padding: 'clamp(112px, 13vw, 172px) 0 clamp(56px, 8vw, 96px)' }}>
        <div style={{ ...WIDE, textAlign: 'center' }}>
          <span className="iso-label" style={{ display: 'inline-block', marginBottom: 30 }}>Data infrastructure for medical AI</span>
          <h1 style={{ ...T_DISPLAY, maxWidth: 1320, margin: '0 auto', textWrap: 'balance' }}>
            Clinician-grade data for medical AI.
          </h1>
          <p style={{ ...T_LEAD, fontSize: 'clamp(18px, 1.9vw, 23px)', maxWidth: 780, margin: '34px auto 0' }}>
            The layer between raw medicine and a model you can trust. Licensed clinicians
            label, evaluate, and create the data your models are trained, aligned, and tested on.
          </p>
          <div style={{ marginTop: 46 }}>
            <Ctas />
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '12px 26px', justifyContent: 'center', marginTop: 56 }}>
            {['Licensed clinicians', 'De-identified', 'Isolated per client', 'Consensus reviewed', 'API-native'].map((t, i) => (
              <span key={t} className="iso-label" style={{ fontSize: 11.5, letterSpacing: '0.1em', color: i === 0 ? 'var(--ink)' : 'var(--slate)' }}>{t}</span>
            ))}
          </div>
        </div>
      </section>

      {/* PROBLEM  */}
      <section style={SECTION}>
        <div style={{ ...WIDE, textAlign: 'center' }}>
          <p style={{ ...T_H2, fontSize: 'clamp(24px, 3.1vw, 40px)', maxWidth: 900, margin: '0 auto', textWrap: 'balance' }}>
            Medical AI is only as good as its data. Crowd labels and a model grading itself
            will not survive a hospital, a regulator, or an investor asking how you know it
            is right.
          </p>
        </div>
      </section>

      {/* WHAT WE DO  */}
      <section id="what-you-get" style={{ ...SECTION, scrollMarginTop: 90 }}>
        <div style={WIDE}>
          <Head tag="What we do" title="Label. Evaluate. Create."
                sub="The data work behind a medical model, at every stage of its life, done by licensed clinicians." />
          <div className="blocks-3col" style={{ marginTop: GAP_GRID }}>
            {[
              { n: '01', t: 'Label', d: 'Raw medical data into ground truth: imaging labels, clinical extraction, and classification.' },
              { n: '02', t: 'Evaluate', d: 'Model outputs graded against a clinician read, every critical miss surfaced, in a report you can defend.' },
              { n: '03', t: 'Create', d: 'Clinician-written gold answers and preference data to fine-tune and align on.' },
            ].map((c, i) => (
              <div key={c.n} style={{ ...CELL, marginLeft: i % 3 === 0 ? 0 : -1 }}>
                <Cell tag={c.n} title={c.t} body={c.d} />
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* HOW IT WORKS  */}
      <section id="how" style={{ ...SECTION, scrollMarginTop: 90 }}>
        <div style={WIDE}>
          <Head tag="How it works" title="Built API-first."
                sub="Send data, licensed clinicians review it, you get it back. One pipeline, an API around it." />
          <div className="blocks-3col" style={{ marginTop: GAP_GRID }}>
            {[
              { n: 'Step 01', t: 'Send your data', d: 'Push items through the API or the dashboard, to label or to grade.' },
              { n: 'Step 02', t: 'Clinicians review', d: 'Licensed specialists work each case, several reviewers per item, combined into a consensus.' },
              { n: 'Step 03', t: 'Get it back', d: 'Labeled data or scored reports, delivered by API and signed webhook, keyed to your case IDs.' },
            ].map((s, i) => (
              <div key={s.n} style={{ ...CELL, marginLeft: i % 3 === 0 ? 0 : -1 }}>
                <Cell tag={s.n} title={s.t} body={s.d} />
              </div>
            ))}
          </div>
          <p style={{ textAlign: 'center', marginTop: 30 }}>
            <a href="/docs" className="iso-cta" style={{ fontSize: 12 }}>Read the API reference →</a>
          </p>
        </div>
      </section>

      {/* WHY US  */}
      <section id="why-us" style={{ ...SECTION, scrollMarginTop: 90 }}>
        <div style={WIDE}>
          <Head tag="Why us" title="Defensible by construction."
                sub="How medical data is handled separates a result you can stand behind from a liability." />
          <div className="cards-2col" style={{ marginTop: GAP_GRID, gap: 1, background: 'var(--hairline)' }}>
            {[
              { k: 'Credible', t: 'Licensed clinicians', d: 'Medical specialists review your data, one case at a time. Not a crowd, and not a model grading a model.' },
              { k: 'Rigorous', t: 'Consensus quality control', d: 'Several clinicians review each item, combined with inter-reviewer agreement so you see where they concur.' },
              { k: 'Private', t: 'Isolated per client', d: 'Your data is de-identified before review and walled off from every other client.' },
              { k: 'Defensible', t: 'Fully audited', d: 'Every review recorded: who did it, what they decided, and when.' },
            ].map((p) => (
              <div key={p.t} style={{ background: 'var(--navy)', padding: PAD_CELL, display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center' }}>
                <Cell tag={p.k} title={p.t} body={p.d} />
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* MODALITIES  */}
      <section id="modalities" style={{ ...SECTION, scrollMarginTop: 90 }}>
        <div style={WIDE}>
          <Head tag="What we cover" title="Across every medical modality." />
          <div className="blocks-3col" style={{ marginTop: GAP_GRID }}>
            {[
              { t: 'Clinical text', d: 'Extraction, coding, and summaries.' },
              { t: 'Radiology', d: 'X-ray, CT, and MRI.' },
              { t: 'Pathology', d: 'Whole-slide and histology.' },
              { t: 'Medical LLMs', d: 'Evaluation, safety, and preference data.' },
              { t: 'Genomics and omics', d: 'Variant and multi-omic data.' },
              { t: 'De-identification', d: 'PHI detection and redaction.' },
            ].map((m, i) => (
              <div key={m.t} style={{ ...CELL, marginLeft: i % 3 === 0 ? 0 : -1, marginTop: i >= 3 ? -1 : 0 }}>
                <Cell title={m.t} body={m.d} />
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CLOSE  */}
      <section style={SECTION}>
        <div style={{ ...WIDE, textAlign: 'center' }}>
          <h2 style={{ ...T_H2, maxWidth: 720, margin: '0 auto', textWrap: 'balance' }}>
            Build medical AI on data you can defend.
          </h2>
          <p style={{ ...T_LEAD, maxWidth: 560, margin: '24px auto 0' }}>
            Start with a slice, see the value, then scale. Book a demo, or read the docs and
            start from code.
          </p>
          <div style={{ marginTop: 44 }}>
            <Ctas />
          </div>
        </div>
      </section>

      <EvalFooter />
    </>
  )
}
