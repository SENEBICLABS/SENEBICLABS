'use client'
import { useState, useRef, useEffect } from 'react'
import NavBar from '../components/NavBar'
import Toast from '../components/ui/Toast'

const HOW_IT_WORKS = [
  {
    heading: 'Patient opens the app',
    body: 'They describe their symptoms, how long they have had them, and any history. No referral needed. No GP bottleneck.',
  },
  {
    heading: 'The app finds the right specialist',
    body: 'Senebic analyses the presentation and matches the patient to the specialist whose expertise fits best, not just the nearest available doctor.',
  },
  {
    heading: 'You receive the case',
    body: 'You get a structured summary: symptoms, duration, flags, and context. You decide whether to accept. Nothing is pushed to you without your consent.',
  },
  {
    heading: 'You consult, remotely or in person',
    body: 'The consultation happens on your terms: video, in clinic, or async. You get paid per case. No minimum commitment required.',
  },
]

const STEPS = [
  {
    n: '01',
    heading: 'Apply',
    body: 'Submit your name, email, and specialty. Every application is reviewed personally, not by an automated system.',
  },
  {
    n: '02',
    heading: 'Get verified',
    body: 'We verify your credentials before you go live. You will hear from us directly. This takes a few days, not weeks.',
  },
  {
    n: '03',
    heading: 'Go live when the app launches',
    body: 'Your profile enters the matching engine. The moment a patient\'s presentation fits your specialty, they can reach you.',
  },
]

// STEP FORM

const STORAGE_KEY = 'senebiclabs_expert_applied'

const FIELDS = [
  { key: 'name',  label: 'Full name',  placeholder: 'Dr. Your Name',      type: 'text',  required: true  },
  { key: 'email', label: 'Email',      placeholder: 'you@hospital.org',    type: 'email', required: true  },
  { key: 'note',  label: 'Specialty',  placeholder: 'e.g. Pulmonology',    type: 'text',  required: false },
] as const

type FieldKey = typeof FIELDS[number]['key']
type FormValues = Record<FieldKey, string>

function StepForm() {
  const [step, setStep]       = useState(0)
  const [form, setForm]       = useState<FormValues>({ name: '', email: '', note: '' })
  const [agreed, setAgreed]   = useState(false)
  const [loading, setLoading] = useState(false)
  const [done, setDone]       = useState(false)
  const [error, setError]     = useState('')
  const [coords, setCoords]   = useState<{ lat: number; lng: number } | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (typeof window === 'undefined') return
    if (localStorage.getItem(STORAGE_KEY) === 'true') setDone(true)
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        p => setCoords({ lat: p.coords.latitude, lng: p.coords.longitude }),
        () => {},
        { timeout: 8000 },
      )
    }
  }, [])

  useEffect(() => {
    if (step < FIELDS.length) inputRef.current?.focus()
  }, [step])

  const current = FIELDS[step]

  const advance = () => {
    const val = form[current.key].trim()
    if (current.required && !val) { inputRef.current?.focus(); return }
    setStep(s => s + 1)
  }

  const handleKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') { e.preventDefault(); advance() }
  }

  const submit = async () => {
    if (!agreed || loading) return
    setLoading(true); setError('')
    try {
      const res = await fetch('/api/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...form, agreed_to_terms: true, lat: coords?.lat ?? null, lng: coords?.lng ?? null }),
      })
      const data = await res.json()
      if (!data.ok) throw new Error(data.message ?? '')
      localStorage.setItem(STORAGE_KEY, 'true')
      setDone(true)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Something went wrong. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  if (done) return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16, paddingTop: 8 }}>
      <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--ink)', flexShrink: 0, marginTop: 6, display: 'block' }} />
      <div>
        <p style={{ fontFamily: 'Geist Mono, monospace', fontSize: 12, letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 8 }}>
          You're on the list
        </p>
        <p style={{ fontSize: 16, lineHeight: 1.65 }}>
          We will verify your credentials and reach out before the app launches.
        </p>
      </div>
    </div>
  )

  return (
    <div>
      {/* completed rows  */}
      {FIELDS.slice(0, step).map((f, i) => (
        <div key={f.key} style={{
          display: 'flex',
          alignItems: 'baseline',
          gap: 24,
          padding: '12px 0',
          borderBottom: '1px solid var(--hairline)',
        }}>
          <span style={{ fontFamily: 'Geist Mono, monospace', fontSize: 12, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--ink)', minWidth: 72 }}>
            {f.label}
          </span>
          <span style={{ fontSize: 16, flex: 1 }}>
            {form[f.key] || ', '}
          </span>
          <button
            onClick={() => setStep(i)}
            style={{ fontFamily: 'Geist Mono, monospace', fontSize: 12, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--ink)', background: 'none', border: 'none', cursor: 'pointer', padding: 0, flexShrink: 0 }}
          >
            edit
          </button>
        </div>
      ))}

      {/* active field  */}
      {step < FIELDS.length && (
        <div style={{ padding: '28px 0 8px' }}>
          <label style={{ display: 'block', fontFamily: 'Geist Mono, monospace', fontSize: 12, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--ink)', marginBottom: 16 }}>
            {current.label}
            {!current.required && <span style={{ marginLeft: 8 }}>(optional)</span>}
          </label>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <input
              ref={inputRef}
              type={current.type}
              placeholder={current.placeholder}
              value={form[current.key]}
              onChange={e => setForm(f => ({ ...f, [current.key]: e.target.value }))}
              onKeyDown={handleKey}
              autoComplete="off"
              style={{
                flex: 1,
                background: 'none',
                border: 'none',
                borderBottom: '1px solid #FFFFFF',
                outline: 'none',
                fontSize: 22,
                fontWeight: 400,
                fontFamily: 'inherit',
                color: 'var(--ink)',
                padding: '6px 0 10px',
              }}
            />
            <button
              onClick={advance}
              style={{
                fontFamily: 'Geist Mono, monospace',
                fontSize: 12,
                letterSpacing: '0.12em',
                textTransform: 'uppercase',
                color: '#000000',
                background: '#FFFFFF',
                border: 'none',
                borderRadius: 999,
                padding: '10px 22px',
                cursor: 'pointer',
                flexShrink: 0,
                whiteSpace: 'nowrap',
                fontWeight: 600,
              }}
            >
              {!current.required ? 'Skip →' : 'Next ↵'}
            </button>
          </div>

          {/* progress bar  */}
          <div style={{ display: 'flex', gap: 4, marginTop: 24 }}>
            {FIELDS.map((_, i) => (
              <div key={i} style={{ flex: 1, height: 2, borderRadius: 1, background: i <= step ? '#FFFFFF' : 'transparent', border: i <= step ? 'none' : '1px solid #FFFFFF' }} />
            ))}
          </div>
        </div>
      )}

      {/* confirm step  */}
      {step === FIELDS.length && (
        <div style={{ paddingTop: 32 }}>
          <label style={{ display: 'flex', alignItems: 'flex-start', gap: 14, cursor: 'pointer' }}>
            <div
              role="checkbox"
              aria-checked={agreed}
              tabIndex={0}
              onClick={() => setAgreed(a => !a)}
              onKeyDown={e => { if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); setAgreed(a => !a) } }}
              style={{
                width: 18, height: 18,
                border: '1px solid #FFFFFF',
                borderRadius: 4,
                background: agreed ? 'var(--ink)' : 'none',
                flexShrink: 0,
                marginTop: 2,
                display: 'grid',
                placeItems: 'center',
                cursor: 'pointer',
              }}
            >
              {agreed && (
                <svg width="10" height="8" viewBox="0 0 10 8" fill="none">
                  <polyline points="1 4 4 7 9 1" stroke="var(--navy)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              )}
            </div>
            <span style={{ fontSize: 15, lineHeight: 1.65 }}>
              I am a licensed medical professional and I consent to Senebic contacting me when the app launches.
            </span>
          </label>

          <div style={{ marginTop: 28, display: 'flex', alignItems: 'center', gap: 20 }}>
            <button
              onClick={submit}
              disabled={!agreed || loading}
              style={{
                fontFamily: 'Geist Mono, monospace',
                fontSize: 11,
                letterSpacing: '0.14em',
                textTransform: 'uppercase',
                background: agreed ? 'var(--ink)' : 'transparent',
                color: agreed ? 'var(--navy)' : 'var(--ink)',
                border: '1px solid #FFFFFF',
                borderRadius: 999,
                padding: '13px 28px',
                cursor: agreed && !loading ? 'pointer' : 'default',
              }}
            >
              {loading ? 'Submitting…' : 'Reserve my place'}
            </button>
            <button
              onClick={() => setStep(FIELDS.length - 1)}
              style={{ fontFamily: 'Geist Mono, monospace', fontSize: 12, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--ink)', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
            >
              ← back
            </button>
          </div>

          {error && <Toast message={error} onDismiss={() => setError('')} />}
        </div>
      )}
    </div>
  )
}

// PAGE

export default function ExpertsPage() {
  return (
    <>
      <NavBar active="experts" />

      {/* Hero  */}
      <section className="experts-hero" style={{ textAlign: 'center' }}>
        <div className="wrap">
          <span className="micro" style={{ display: 'block', color: 'var(--ink)', marginBottom: 28 }}>Senebic App · Pre-launch</span>
          <h1 style={{
            fontWeight: 300,
            fontSize: 'clamp(44px, 6vw, 80px)',
            lineHeight: 1.06,
            letterSpacing: '0.02em',
            maxWidth: 800,
            marginBottom: 32,
            marginLeft: 'auto',
            marginRight: 'auto',
          }}>
            An app that sends you the patients who actually need you.
          </h1>
          <p style={{ fontSize: 18, lineHeight: 1.75, maxWidth: 540, marginBottom: 48, marginLeft: 'auto', marginRight: 'auto' }}>
            Senebic is a mobile app for patients. They describe their symptoms. The app finds the specialist whose expertise fits their exact presentation. That specialist is you, if you are on the network when it launches.
          </p>
          <a href="#apply" className="nav-join-cta" style={{ fontSize: 11, padding: '12px 24px' }}>
            Join the network →
          </a>
        </div>
      </section>

      {/* How the app works  */}
      <section style={{ padding: '80px 0 60px', borderTop: '1px solid var(--hairline)' }}>
        <div className="wrap">
          <span className="micro" style={{ display: 'block', color: 'var(--ink)', marginBottom: 52, textAlign: 'center' }}>How the app works</span>
          <div className="experts-how-grid">
            {HOW_IT_WORKS.map((item, i) => (
              <div key={item.heading} style={{ borderTop: '1px solid var(--hairline)', padding: '36px 0' }}>
                <span style={{ fontFamily: 'Geist Mono, monospace', fontSize: 12, letterSpacing: '0.14em', textTransform: 'uppercase' as const, color: 'var(--ink)', display: 'block', marginBottom: 16 }}>
                  0{i + 1}
                </span>
                <h3 style={{ fontSize: 21, fontWeight: 400, letterSpacing: '-0.01em', marginBottom: 12 }}>
                  {item.heading}
                </h3>
                <p style={{ fontSize: 16, lineHeight: 1.7 }}>{item.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Why join now  */}
      <section style={{ padding: '80px 0', borderTop: '1px solid var(--hairline)', textAlign: 'center' }}>
        <div className="wrap">
          <div style={{ maxWidth: 680, margin: '0 auto', marginBottom: 64 }}>
            <span className="micro" style={{ display: 'block', color: 'var(--ink)', marginBottom: 28 }}>Why join before launch</span>
            <h2 style={{ fontWeight: 300, fontSize: 'clamp(32px, 4vw, 56px)', lineHeight: 1.08, letterSpacing: '0.02em', marginBottom: 24 }}>
              The network launches with you already in it.
            </h2>
            <p style={{ fontSize: 18, lineHeight: 1.75 }}>
              We verify every specialist before the app goes live. Joining now means your profile is in the matching engine from day one, not waiting in a queue after launch while unverified doctors catch up.
            </p>
          </div>
        </div>
      </section>

      {/* How to join  */}
      <section style={{ padding: '0 0 80px', borderTop: '1px solid var(--hairline)' }}>
        <div className="wrap" style={{ paddingTop: 80 }}>
          <span className="micro" style={{ display: 'block', color: 'var(--ink)', marginBottom: 52, textAlign: 'center' }}>How to join</span>
          <div className="philosophy-pillars" style={{ marginTop: 0 }}>
            {STEPS.map(s => (
              <div className="pillar" key={s.n}>
                <div className="pn">{s.n}</div>
                <h3>{s.heading}</h3>
                <p style={{ color: 'var(--ink)' }}>{s.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Application  */}
      <section id="apply" style={{ padding: '80px 0 120px', borderTop: '1px solid var(--hairline)', textAlign: 'center' }}>
        <div className="wrap">
          <div style={{ maxWidth: 560, margin: '0 auto' }}>
            <span className="micro" style={{ display: 'block', color: 'var(--ink)', marginBottom: 24 }}>Apply to join</span>
            <h2 style={{ fontWeight: 300, fontSize: 'clamp(28px, 3vw, 44px)', letterSpacing: '0.02em', lineHeight: 1.1, marginBottom: 20 }}>
              Reserve your place on the network.
            </h2>
            <p style={{ fontSize: 16, lineHeight: 1.7, marginBottom: 52 }}>
              Submit your details. We verify your credentials and add you to the network before the app launches. When the first patients open the app, you will already be there.
            </p>
            <div style={{ textAlign: 'left' }}>
              <StepForm />
            </div>
          </div>
        </div>
      </section>
    </>
  )
}
