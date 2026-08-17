'use client'
import { useEffect, useState, useCallback } from 'react'

const CODE_KEY = 'senebiclabs_work_code'

type Brief = {
  company: string; task_type: string | null; ls_link: string | null
  total: number; done: number; pending: number; labeler: string
  rate_per_item: number | null; payout: number | null
}

const GUIDELINES = [
  'Read the prompt and the full response before scoring.',
  'Judge factual accuracy and clinical safety, a confident but wrong answer scores low.',
  'Flag anything that could mislead a clinician or harm a patient.',
  'Add a one-line rationale so your judgement is auditable.',
]

export default function WorkPage() {
  const [light, setLight] = useState(false)
  const [code, setCode] = useState('')
  const [codeInput, setCodeInput] = useState('')
  const [needCode, setNeedCode] = useState(false)
  const [project, setProject] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [brief, setBrief] = useState<Brief | null>(null)

  const load = useCallback(async (c: string, proj: string) => {
    setLoading(true); setError('')
    try {
      const res = await fetch(`/api/work/brief?project=${encodeURIComponent(proj)}`, { headers: { 'x-work-code': c } })
      if (res.status === 403) { setNeedCode(true); setLoading(false); return }
      const data = await res.json()
      if (!data.ok) throw new Error(data.message ?? 'Could not load the project.')
      setNeedCode(false); setCode(c); localStorage.setItem(CODE_KEY, c)
      setBrief(data)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Could not load the project.')
    } finally { setLoading(false) }
  }, [])

  useEffect(() => {
    setLight(localStorage.getItem('cx_theme') === 'light')
    const params = new URLSearchParams(window.location.search)
    const proj = params.get('project') ?? ''
    const fromLink = params.get('code')
    if (fromLink) localStorage.setItem(CODE_KEY, fromLink)
    const c = fromLink || localStorage.getItem(CODE_KEY) || localStorage.getItem('senebiclabs_admin_key') || ''
    if (!proj) { window.location.replace('/contributor'); return }
    setProject(proj)
    if (!c) { setNeedCode(true); setLoading(false); return }
    load(c, proj)
  }, [load])

  const app = `cx-app${light ? ' light' : ''}`

  if (needCode) {
    const inp: React.CSSProperties = { width: '100%', background: 'var(--cx-surface-2)', border: '1px solid var(--cx-border)', borderRadius: 10, padding: '12px 14px', color: 'var(--cx-text)', fontFamily: 'inherit', fontSize: 14, outline: 'none' }
    return (
      <div className={app}>
        <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', padding: 24 }}>
          <form onSubmit={e => { e.preventDefault(); const c = codeInput.trim(); if (c) load(c, project) }}
            className="cx-card" style={{ width: '100%', maxWidth: 380, display: 'flex', flexDirection: 'column', gap: 14 }}>
            <span className="cx-logo">S</span>
            <div><h1 style={{ fontSize: 20, fontWeight: 600 }}>Specialist sign-in</h1><p style={{ color: 'var(--cx-muted)', fontSize: 14, marginTop: 4 }}>Enter your access code to start.</p></div>
            <input style={inp} type="password" placeholder="Access code" value={codeInput} onChange={e => setCodeInput(e.target.value)} autoFocus />
            <button className="cx-btn" type="submit" style={{ width: '100%' }}>Continue</button>
          </form>
        </div>
      </div>
    )
  }

  return (
    <div className={app}>
      <div className="cx-brief-wrap">
        <a className="cx-ws-back" href="/contributor">← Dashboard</a>

        {loading && <p style={{ color: 'var(--cx-muted)', marginTop: 32 }}>Loading…</p>}
        {error && <p style={{ color: '#f87171', marginTop: 32 }}>{error}</p>}

        {brief && (
          <div className="cx-brief">
            <span className="cx-section-label" style={{ marginBottom: 10 }}>{brief.task_type || 'Clinical evaluation'}</span>
            <h1 className="cx-h1">{brief.company}</h1>
            <p className="cx-h1-sub">
              {brief.pending} item{brief.pending === 1 ? '' : 's'} to label · {brief.done} of {brief.total} done
              {brief.payout != null && <> · <span style={{ color: 'var(--cx-good)', fontWeight: 600 }}>${brief.payout.toFixed(2)} to earn</span></>}
            </p>

            <div className="cx-card cx-brief-card">
              <span className="cx-section-label">Before you start</span>
              <ol className="cx-brief-list">
                {GUIDELINES.map((g, i) => <li key={i}>{g}</li>)}
              </ol>
              <p className="cx-brief-note">Full task instructions and examples are inside Label Studio, on the Instructions tab.</p>
            </div>

            {brief.ls_link ? (
              <a className="cx-btn cx-btn-lg cx-brief-cta" href={brief.ls_link} target="_blank" rel="noopener noreferrer">
                Open in Label Studio →
              </a>
            ) : (
              <div className="cx-brief-pending">
                This project isn&rsquo;t ready for labeling yet. Your tasks are being prepared, check back shortly.
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
