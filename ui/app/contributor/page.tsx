'use client'
import { useEffect, useState, useCallback } from 'react'

const CODE_KEY = 'senebiclabs_work_code'

function Icon({ name }: { name: string }) {
  const p: Record<string, React.ReactNode> = {
    dashboard: <><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></>,
    market: <><path d="M3 9l1.5-5h15L21 9" /><path d="M4 9v10a1 1 0 001 1h14a1 1 0 001-1V9" /><path d="M9 13h6" /></>,
    projects: <><path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z" /></>,
    earnings: <><rect x="3" y="6" width="18" height="13" rx="2" /><path d="M3 10h18" /><circle cx="16" cy="14.5" r="1.4" /></>,
    metrics: <><path d="M4 20V10" /><path d="M10 20V4" /><path d="M16 20v-7" /><path d="M22 20H2" /></>,
    profile: <><circle cx="12" cy="8" r="4" /><path d="M4 20c0-4 4-6 8-6s8 2 8 6" /></>,
    help: <><circle cx="12" cy="12" r="9" /><path d="M9.5 9.5a2.5 2.5 0 014.5 1.5c0 1.7-2.5 2-2.5 3.5" /><circle cx="12" cy="17" r="0.6" fill="currentColor" /></>,
    search: <><circle cx="11" cy="11" r="7" /><path d="M21 21l-4-4" /></>,
    bell: <><path d="M6 9a6 6 0 1112 0c0 5 2 6 2 6H4s2-1 2-6" /><path d="M10 20a2 2 0 004 0" /></>,
    sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4 12H2M22 12h-2M5 5l1.5 1.5M17.5 17.5L19 19M19 5l-1.5 1.5M6.5 17.5L5 19" /></>,
    moon: <path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z" />,
    panel: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16" /></>,
    check: <><circle cx="12" cy="12" r="9" /><path d="M8 12l3 3 5-6" /></>,
    trend: <><path d="M3 17l6-6 4 4 8-8" /><path d="M17 7h4v4" /></>,
    flame: <path d="M12 3c0 3-4 4-4 8a4 4 0 008 0c0-2-1-3-1-3 0 1.5-1 2-1 2 0-3-1-5-2-7z" />,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    layers: <><path d="M12 3l9 5-9 5-9-5z" /><path d="M3 13l9 5 9-5" /></>,
    bolt: <path d="M13 2L4 14h6l-1 8 9-12h-6z" />,
  }
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">{p[name]}</svg>
}

const NAV = [
  { key: 'dashboard', label: 'Dashboard', icon: 'dashboard', href: '/contributor' },
  { key: 'projects', label: 'My Projects', icon: 'projects', href: '#work' },
]

type Project = {
  id: string; company: string; total: number; done: number; pending: number
  difficulty: string | null; payout: number | null; est_minutes: number
}
type Home = {
  name: string; total_labeled: number; this_week: number; streak: number; active_projects: number
  earned_total: number; earned_week: number
  daily: number[]; recent: { company: string; at: string }[]; projects: Project[]
}

function rel(iso: string) {
  const m = Math.floor((Date.now() - new Date(iso).getTime()) / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m} min ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} hour${h > 1 ? 's' : ''} ago`
  const d = Math.floor(h / 24)
  return `${d} day${d > 1 ? 's' : ''} ago`
}

const initials = (n: string) => n.split(' ').map(s => s[0]).filter(Boolean).slice(0, 2).join('').toUpperCase() || 'S'
const first = (n: string) => n.replace(/^(dr\.?|prof\.?)\s+/i, '').split(' ')[0] || n

export default function ContributorPage() {
  const [collapsed, setCollapsed] = useState(false)
  const [light, setLight] = useState(false)
  const [code, setCode] = useState('')
  const [codeInput, setCodeInput] = useState('')
  const [needCode, setNeedCode] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [data, setData] = useState<Home | null>(null)
  const [query, setQuery] = useState('')

  const toggleTheme = () => setLight(v => { const nv = !v; localStorage.setItem('cx_theme', nv ? 'light' : 'dark'); return nv })

  const load = useCallback(async (c: string) => {
    setLoading(true); setError('')
    try {
      const res = await fetch('/api/work/home', { headers: { 'x-work-code': c } })
      if (res.status === 403) { setNeedCode(true); setLoading(false); return }
      const d = await res.json()
      if (!d.ok) throw new Error(d.message ?? 'Could not load your dashboard.')
      setNeedCode(false); setCode(c); localStorage.setItem(CODE_KEY, c)
      setData(d)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Could not load your dashboard.')
    } finally { setLoading(false) }
  }, [])

  useEffect(() => {
    setLight(localStorage.getItem('cx_theme') === 'light')
    const fromLink = new URLSearchParams(window.location.search).get('code')
    if (fromLink) localStorage.setItem(CODE_KEY, fromLink)
    const c = fromLink || localStorage.getItem(CODE_KEY) || localStorage.getItem('senebiclabs_admin_key') || ''
    if (!c) { setNeedCode(true); setLoading(false); return }
    load(c)
  }, [load])

  // code gate
  if (needCode) {
    const inp: React.CSSProperties = { width: '100%', background: 'var(--cx-surface-2)', border: '1px solid var(--cx-border)', borderRadius: 10, padding: '12px 14px', color: 'var(--cx-text)', fontFamily: 'inherit', fontSize: 14, outline: 'none' }
    return (
      <div className={`cx-app${light ? ' light' : ''}`}>
        <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', padding: 24 }}>
          <form
            onSubmit={e => { e.preventDefault(); const c = codeInput.trim(); if (c) load(c) }}
            className="cx-card" style={{ width: '100%', maxWidth: 380, display: 'flex', flexDirection: 'column', gap: 14 }}
          >
            <span className="cx-logo" style={{ marginBottom: 4 }}>S</span>
            <div>
              <h1 style={{ fontSize: 20, fontWeight: 600 }}>Specialist sign-in</h1>
              <p style={{ color: 'var(--cx-muted)', fontSize: 14, marginTop: 4 }}>Enter the access code from your invite.</p>
            </div>
            <input style={inp} type="password" placeholder="Access code" value={codeInput} onChange={e => setCodeInput(e.target.value)} autoFocus />
            <button className="cx-btn" type="submit" style={{ width: '100%' }}>{loading ? 'Checking…' : 'Continue'}</button>
            {error && <p style={{ color: '#f87171', fontSize: 13 }}>{error}</p>}
          </form>
        </div>
      </div>
    )
  }

  const daily = data?.daily ?? [0, 0, 0, 0, 0, 0, 0]
  const w = 520, h = 150, pad = 10
  const max = Math.max(...daily, 1)
  const pts = daily.map((v, i) => {
    const x = pad + (i * (w - pad * 2)) / (daily.length - 1)
    const y = pad + (1 - v / max) * (h - pad * 2)
    return [x, y]
  })
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ')

  const stats = [
    { label: 'Earned this week', value: data ? `$${data.earned_week.toFixed(2)}` : ', ', icon: 'earnings' },
    { label: 'Items this week', value: data ? String(data.this_week) : ', ', icon: 'trend' },
    { label: 'Day streak', value: data ? String(data.streak) : ', ', icon: 'flame' },
    { label: 'Total labeled', value: data ? data.total_labeled.toLocaleString() : ', ', icon: 'check' },
  ]

  return (
    <div className={`cx-app${light ? ' light' : ''}`}>
      <div className={`cx-shell${collapsed ? ' collapsed' : ''}`}>

        <aside className="cx-side">
          <div className="cx-side-head">
            <span className="cx-logo">S</span>
            <span className="cx-logo-text cx-lbl">Senebiclabs</span>
          </div>
          <nav className="cx-nav">
            {NAV.map(n => (
              <a key={n.key} href={n.href} className={n.key === 'dashboard' ? 'active' : ''}>
                <Icon name={n.icon} /><span className="cx-lbl">{n.label}</span>
              </a>
            ))}
          </nav>
          <div className="cx-side-foot">
            <button className="cx-toggle" onClick={toggleTheme}>
              <Icon name={light ? 'moon' : 'sun'} /><span className="cx-lbl">{light ? 'Dark mode' : 'Light mode'}</span>
            </button>
            <div className="cx-user">
              <span className="cx-avatar">{data ? initials(data.name) : 'S'}</span>
              <span className="cx-user-meta cx-lbl">
                <span className="cx-user-name">{data?.name ?? 'Specialist'}</span>
                <span className="cx-user-role">Clinical specialist</span>
              </span>
            </div>
          </div>
        </aside>

        <main className="cx-main">
          <header className="cx-top">
            <button className="cx-collapse-btn" onClick={() => setCollapsed(v => !v)} aria-label="Toggle sidebar">
              <span style={{ width: 18, height: 18, display: 'block' }}><Icon name="panel" /></span>
            </button>
            <div className="cx-search">
              <span style={{ width: 17, height: 17, display: 'block' }}><Icon name="search" /></span>
              <input placeholder="Find projects…" value={query} onChange={e => setQuery(e.target.value)} />
            </div>
            <div className="cx-top-actions">
              <a className="cx-btn" href="#work">View work</a>
            </div>
          </header>

          <div className="cx-content">
            <h1 className="cx-h1">Welcome back{data ? `, ${first(data.name)}` : ''}</h1>
            <p className="cx-h1-sub">{loading ? 'Loading your work…' : error ? error : (data && data.earned_total > 0 ? `You've earned $${data.earned_total.toFixed(2)} in total. Here's your available work.` : 'Here is your work and what is available to pick up.')}</p>

            <div className="cx-stats" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(168px, 1fr))' }}>
              {stats.map(s => (
                <div className="cx-card" key={s.label}>
                  <div className="cx-stat-top">
                    <span className="cx-stat-label">{s.label}</span>
                    <span className="cx-stat-ico"><span style={{ width: 18, height: 18, display: 'block' }}><Icon name={s.icon} /></span></span>
                  </div>
                  <div className="cx-stat-val">{loading && !data ? <span className="cx-skel" style={{ width: 56, height: 28 }} /> : s.value}</div>
                </div>
              ))}
            </div>

            <div className="cx-grid2">
              <div className="cx-card">
                <span className="cx-section-label">Items labeled · last 7 days</span>
                <svg className="cx-chart" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ width: '100%' }}>
                  <path d={line} fill="none" stroke="#2563EB" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
                  {pts.map((p, i) => <circle key={i} cx={p[0]} cy={p[1]} r="3" fill="#2563EB" />)}
                </svg>
              </div>

              <div className="cx-card">
                <span className="cx-section-label">Recent activity</span>
                <div className="cx-feed">
                  {(data?.recent ?? []).length === 0 && <p style={{ color: 'var(--cx-muted)', fontSize: 13.5 }}>No activity yet. Pick up a project below.</p>}
                  {(data?.recent ?? []).map((f, i) => (
                    <div className="cx-feed-row" key={i}>
                      <span className="cx-feed-ico"><span style={{ width: 16, height: 16, display: 'block' }}><Icon name="check" /></span></span>
                      <div>
                        <div className="cx-feed-txt">Labeled an item in <b>{f.company}</b></div>
                        <div className="cx-feed-time">{rel(f.at)}</div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <span id="work" className="cx-section-label" style={{ display: 'block', marginTop: 38 }}>Available work</span>
            <div className="cx-wgrid">
              {loading && !data && [0, 1, 2].map(i => (
                <div className="cx-card cx-wcard" key={i}>
                  <span className="cx-skel" style={{ width: '60%', height: 18 }} />
                  <span className="cx-skel" style={{ width: '90%', height: 12, marginTop: 16 }} />
                  <span className="cx-skel" style={{ width: '100%', height: 38, marginTop: 18, borderRadius: 10 }} />
                </div>
              ))}
              {data && data.projects.length === 0 && (
                <div className="cx-empty">
                  <div className="cx-empty-ico"><span style={{ width: 22, height: 22, display: 'block' }}><Icon name="check" /></span></div>
                  <div className="cx-empty-title">You&rsquo;re all caught up</div>
                  <div className="cx-empty-sub">No projects have work available right now. We&rsquo;ll match you to new work as it opens.</div>
                </div>
              )}
              {(data?.projects ?? []).filter(p => p.company.toLowerCase().includes(query.trim().toLowerCase())).map(p => {
                const pc = p.total ? (p.done / p.total) * 100 : 0
                return (
                  <div className="cx-card cx-wcard" key={p.id}>
                    <div className="cx-wcard-head">
                      <span className="cx-wcard-title">{p.company}</span>
                      {p.difficulty && <span className="cx-chip">{p.difficulty}</span>}
                    </div>
                    <div className="cx-wmeta">
                      <span className="cx-meta"><span className="cx-meta-i"><Icon name="layers" /></span>{p.pending} left</span>
                      <span className="cx-meta"><span className="cx-meta-i"><Icon name="clock" /></span>~{p.est_minutes}m</span>
                      {p.payout != null && <span className="cx-meta cx-meta-pay">${p.payout.toFixed(2)}</span>}
                    </div>
                    <div className="cx-wbar"><i style={{ width: `${pc}%` }} /></div>
                    <div className="cx-wcard-foot">
                      <span className="cx-wcount">{p.done} / {p.total} done</span>
                      {p.pending > 0
                        ? <a className="cx-btn cx-btn-sm" href={`/work?project=${p.id}`}>Start working →</a>
                        : <span className="cx-done-tag">Complete</span>}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </main>
      </div>
    </div>
  )
}
