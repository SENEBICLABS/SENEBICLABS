'use client'
import { useState } from 'react'
import NavBar from '../components/NavBar'
import EvalFooter from '../components/EvalFooter'
import Toast from '../components/ui/Toast'

const TASK_TYPES = [
  'Medical Image Annotation and Curation',
  'Clinical Text Labeling and Extraction',
  'Data Generation and RLHF for Medical LLMs',
  'Model Test and Evaluation for Healthcare AI',
  'Genomics and Omics Annotation',
  'De-identification and PHI Redaction',
  'Expert Clinical Review and Second Opinion',
  'Other',
]

const POINTS = [
  {
    heading: 'A short intro call',
    body: 'We reply within one business day to set up a quick walkthrough of the platform on your use case.',
  },
  {
    heading: 'See it on your data',
    body: 'We show how credentialed medical specialists label and evaluate your data, with gold-standard checks and agreement built into every task.',
  },
  {
    heading: 'Scope a first pilot',
    body: 'If it is a fit, we scope a small pilot so you see the quality before committing to anything larger.',
  },
]

type Form = {
  firstName: string; lastName: string; email: string; company: string
  jobTitle: string; description: string; task_type: string[]
}

const check = (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden style={{ flexShrink: 0, marginTop: 2 }}>
    <circle cx="8" cy="8" r="7.25" stroke="var(--teal)" strokeWidth="1.1" />
    <polyline points="5 8.2 7.2 10.4 11 6" stroke="var(--teal)" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
)

export default function SubmitPage() {
  const [form, setForm] = useState<Form>({
    firstName: '', lastName: '', email: '', company: '',
    jobTitle: '', description: '', task_type: [],
  })
  const [loading, setLoading] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState('')

  const set = (k: 'firstName' | 'lastName' | 'email' | 'company' | 'jobTitle' | 'description') =>
    (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      setForm({ ...form, [k]: e.target.value })

  const toggle = (v: string) =>
    setForm(f => ({ ...f, task_type: f.task_type.includes(v) ? f.task_type.filter(x => x !== v) : [...f.task_type, v] }))

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (loading) return
    setLoading(true)
    setError('')
    try {
      const name = `${form.firstName} ${form.lastName}`.trim()
      const description = form.jobTitle ? `Role: ${form.jobTitle}\n\n${form.description}` : form.description
      const res = await fetch('/api/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name,
          email: form.email,
          company: form.company,
          description,
          task_type: form.task_type.join(', '),
        }),
      })
      const data = await res.json()
      if (!data.ok) throw new Error(data.message ?? '')
      setDone(true)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Something went wrong. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <div className="submit-page">
      <NavBar minimal />

      <section className="submit-section">
        <div className="wrap">
          <div className="submit-split">

            {/* Left, copy  */}
            <div className="submit-copy">
              <span className="micro" style={{ display: 'block', color: 'var(--ink)', marginBottom: 28 }}>For companies</span>
              <h1 style={{
                fontFamily: 'Geist, sans-serif',
                fontWeight: 300,
                fontSize: 'clamp(38px, 4.6vw, 60px)',
                lineHeight: 1.06,
                letterSpacing: '0.01em',
                textWrap: 'balance',
                marginBottom: 24,
              }}>
                Book a demo.
              </h1>
              <p style={{ fontSize: 18, color: 'var(--ink)', lineHeight: 1.75, maxWidth: 480, marginBottom: 44 }}>
                See clinician-verified evaluation and labeling on your own models.
                Tell us a bit about what you&rsquo;re building and we&rsquo;ll set up a
                walkthrough with our team.
              </p>

              <div style={{ display: 'flex', flexDirection: 'column', gap: 26, maxWidth: 460 }}>
                {POINTS.map(p => (
                  <div key={p.heading} style={{ display: 'flex', gap: 14 }}>
                    {check}
                    <div>
                      <h3 style={{ fontSize: 16, fontWeight: 500, letterSpacing: '-0.01em', marginBottom: 5 }}>{p.heading}</h3>
                      <p style={{ fontSize: 14.5, lineHeight: 1.65, color: 'var(--slate)' }}>{p.body}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Right, form  */}
            <div className="submit-form-col">
              <span className="micro" style={{ display: 'block', color: 'var(--slate)', marginBottom: 18 }}>Tell us about your use case</span>

              {done ? (
                <div className="sf-success">
                  <svg className="sf-success-icon" width="22" height="22" viewBox="0 0 20 20" fill="none">
                    <circle cx="10" cy="10" r="9" stroke="currentColor" strokeWidth="1.3" />
                    <polyline points="6 10.5 9 13.5 14 7" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                  <div>
                    <h4>Got it{form.firstName ? `, ${form.firstName}` : ''}.</h4>
                    <p>Last step, pick a time and we&rsquo;ll walk you through it live.</p>
                    <a
                      className="sf-submit"
                      style={{ display: 'inline-block', width: 'auto', marginTop: 18, padding: '14px 24px', textDecoration: 'none' }}
                      href={`https://calendly.com/senebiclabs/30min?name=${encodeURIComponent(`${form.firstName} ${form.lastName}`.trim())}&email=${encodeURIComponent(form.email)}`}
                    >
                      Choose a time →
                    </a>
                  </div>
                </div>
              ) : (
                <form onSubmit={submit} className="sf-form">
                  <div className="sf-row">
                    <div className="sf-field">
                      <label className="sf-label" htmlFor="firstName">First name *</label>
                      <input id="firstName" className="sf-input" value={form.firstName} onChange={set('firstName')} required disabled={loading} autoComplete="given-name" />
                    </div>
                    <div className="sf-field">
                      <label className="sf-label" htmlFor="lastName">Last name *</label>
                      <input id="lastName" className="sf-input" value={form.lastName} onChange={set('lastName')} required disabled={loading} autoComplete="family-name" />
                    </div>
                  </div>

                  <div className="sf-field">
                    <label className="sf-label" htmlFor="email">Work email *</label>
                    <input id="email" className="sf-input" type="email" value={form.email} onChange={set('email')} required disabled={loading} autoComplete="email" />
                  </div>

                  <div className="sf-row">
                    <div className="sf-field">
                      <label className="sf-label" htmlFor="company">Company *</label>
                      <input id="company" className="sf-input" value={form.company} onChange={set('company')} required disabled={loading} autoComplete="organization" />
                    </div>
                    <div className="sf-field">
                      <label className="sf-label" htmlFor="jobTitle">Job title *</label>
                      <input id="jobTitle" className="sf-input" value={form.jobTitle} onChange={set('jobTitle')} required disabled={loading} autoComplete="organization-title" />
                    </div>
                  </div>

                  <div className="sf-field">
                    <label className="sf-label">What are you interested in? <span className="opt">Select all that apply</span></label>
                    <div className="sf-checks">
                      {TASK_TYPES.map(o => (
                        <label key={o} className="sf-check">
                          <input type="checkbox" checked={form.task_type.includes(o)} onChange={() => toggle(o)} disabled={loading} />
                          <span>{o}</span>
                        </label>
                      ))}
                    </div>
                  </div>

                  <div className="sf-field">
                    <label className="sf-label" htmlFor="description">How can we help? *</label>
                    <p className="sf-help">A line on your model and what you&rsquo;d want reviewed, labeled, or evaluated, so we can tailor the demo to your use case.</p>
                    <textarea id="description" className="sf-textarea" value={form.description} onChange={set('description')} required disabled={loading} rows={5} />
                  </div>

                  <button className="sf-submit" type="submit" disabled={loading}>
                    {loading ? 'Submitting…' : 'Book a demo'}
                  </button>
                  <p className="sf-note">We&rsquo;ll reply within one business day to set up your demo. Your details stay private.</p>
                </form>
              )}

              {error && <Toast message={error} onDismiss={() => setError('')} />}
            </div>

          </div>
        </div>
      </section>

      <EvalFooter />
      </div>
    </>
  )
}
