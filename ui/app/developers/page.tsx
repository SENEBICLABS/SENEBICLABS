'use client'

import { useEffect, useState } from 'react'
import '../docs/docs.css'

const MONO = 'ui-monospace, "SF Mono", Menlo, Consolas, monospace'

type Key = { id: string; label: string; last4: string; created_at: string | null; revoked: boolean }

export default function DevelopersPage() {
  const [token, setToken] = useState<string | null>(null)
  const [ready, setReady] = useState(false)

  // form (no token) state
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)

  // console (token) state
  const [acctEmail, setAcctEmail] = useState('')
  const [keys, setKeys] = useState<Key[]>([])
  const [label, setLabel] = useState('')
  const [newKey, setNewKey] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [expired, setExpired] = useState(false)

  useEffect(() => {
    const urlToken = new URLSearchParams(window.location.search).get('token')
    if (urlToken) {
      // Fresh magic link: start a session and clean the token out of the URL/history.
      try { localStorage.setItem('sb_dev_token', urlToken) } catch {}
      window.history.replaceState({}, '', '/developers')
      setToken(urlToken)
    } else {
      // Returning visit: reuse the stored session if the link hasn't expired.
      let stored: string | null = null
      try { stored = localStorage.getItem('sb_dev_token') } catch {}
      setToken(stored)
    }
    setReady(true)
  }, [])

  useEffect(() => {
    if (!token) return
    ;(async () => {
      try {
        const res = await fetch(`/api/portal/keys?token=${encodeURIComponent(token)}`, { cache: 'no-store' })
        const data = await res.json()
        if (res.status === 401) {
          try { localStorage.removeItem('sb_dev_token') } catch {}
          setExpired(true); return
        }
        if (!res.ok) throw new Error(data.detail || data.message || 'Could not load your keys.')
        setAcctEmail(data.email)
        setKeys(data.keys || [])
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Could not load your keys.')
      }
    })()
  }, [token])

  async function sendLink(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true); setError(null)
    try {
      const res = await fetch('/api/portal/dev-link', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      })
      if (!res.ok) { const d = await res.json(); throw new Error(d.detail || d.message || 'Could not send the link.') }
      setSent(true)
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not send the link.') }
    finally { setBusy(false) }
  }

  async function createKey() {
    setBusy(true); setError(null); setNewKey(null); setCopied(false)
    try {
      const res = await fetch('/api/portal/keys', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, label: label || undefined }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || data.message || 'Could not create the key.')
      setNewKey(data.api_key)
      setKeys(k => [{ id: data.id, label: data.label, last4: data.last4, created_at: data.created_at, revoked: false }, ...k])
      setLabel('')
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not create the key.') }
    finally { setBusy(false) }
  }

  async function revokeKey(id: string) {
    if (!confirm('Revoke this key? Any client using it will immediately stop working.')) return
    setError(null)
    try {
      const res = await fetch('/api/portal/keys/revoke', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, key_id: id }),
      })
      if (!res.ok) { const d = await res.json(); throw new Error(d.detail || d.message || 'Could not revoke.') }
      setKeys(k => k.map(x => x.id === id ? { ...x, revoked: true } : x))
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not revoke.') }
  }

  function signOut() {
    try { localStorage.removeItem('sb_dev_token') } catch {}
    setToken(null); setSent(false); setKeys([]); setNewKey(null); setAcctEmail(''); setExpired(false)
  }

  const eyebrow: React.CSSProperties = { fontFamily: MONO, fontSize: 11, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--slate)' }
  const btn: React.CSSProperties = { fontFamily: MONO, fontSize: 12, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--navy)', background: 'var(--ink)', border: 0, borderRadius: 8, padding: '11px 20px', cursor: 'pointer' }
  const input: React.CSSProperties = { width: '100%', padding: '12px 14px', borderRadius: 8, border: '1px solid var(--hairline)', background: 'rgba(255,255,255,0.04)', color: 'var(--ink)', fontSize: 15, fontFamily: 'inherit' }
  const card: React.CSSProperties = { border: '1px solid var(--hairline)', borderRadius: 12, padding: '20px 22px', background: 'rgba(255,255,255,0.02)' }

  if (!ready) return null

  return (
    <div className="docs-shell">
      {/* Sidebar */}
      <aside className="docs-side" style={{ display: 'flex', flexDirection: 'column' }}>
        <a href="/" className="docs-brand">Senebiclabs</a>
        <div className="docs-brand-sub">Developers</div>
        <nav className="docs-nav">
          <a href="/developers" className="active">API keys</a>
          <a href="/docs">Documentation</a>
          <a href="/docs#quickstart">Quickstart</a>
        </nav>
        {token && !expired && acctEmail && (
          <div style={{ marginTop: 'auto', paddingTop: 22, borderTop: '1px solid var(--hairline)' }}>
            <div style={{ fontSize: 12.5, color: 'var(--slate)', marginBottom: 8, wordBreak: 'break-all' }}>{acctEmail}</div>
            <button onClick={signOut} style={{ fontFamily: MONO, fontSize: 11, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--ink)', background: 'none', border: 0, padding: 0, cursor: 'pointer', opacity: 0.7 }}>Sign out →</button>
          </div>
        )}
      </aside>

      {/* Content */}
      <main className="docs-main">
      <span className="docs-eyebrow">Manage access</span>
      <h1 style={{ margin: '10px 0 22px' }}>API keys</h1>

      {error && <p style={{ color: '#f87171', fontSize: 14, marginBottom: 16 }}>{error}</p>}

      {/* ── No token: email sign-in ── */}
      {!token && !sent && (
        <>
          <p style={{ fontSize: 16, lineHeight: 1.6, color: 'rgba(255,255,255,0.72)', maxWidth: '54ch', margin: '0 0 24px' }}>
            Enter your email and we&rsquo;ll send you a sign-in link to create and manage your API keys.
            No password. The link verifies your address.
          </p>
          <form onSubmit={sendLink} style={{ display: 'flex', gap: 10, flexWrap: 'wrap', maxWidth: 460 }}>
            <input style={{ ...input, flex: 1, minWidth: 240 }} type="email" required placeholder="you@company.com" value={email} onChange={e => setEmail(e.target.value)} />
            <button style={{ ...btn, opacity: busy ? 0.6 : 1 }} disabled={busy} type="submit">{busy ? 'Sending…' : 'Send link'}</button>
          </form>
        </>
      )}

      {!token && sent && (
        <div style={card}>
          <p style={{ margin: 0, fontSize: 15, color: 'rgba(255,255,255,0.82)' }}>
            Check your inbox for a sign-in link from Senebiclabs. Open it on this device to manage your keys.
          </p>
        </div>
      )}

      {/* ── Token expired ── */}
      {token && expired && (
        <div style={card}>
          <p style={{ margin: '0 0 14px', fontSize: 15, color: 'rgba(255,255,255,0.82)' }}>This sign-in link has expired.</p>
          <a href="/developers" style={{ ...eyebrow, color: 'var(--ink)', textDecoration: 'none' }}>Request a new link →</a>
        </div>
      )}

      {/* ── Token: console ── */}
      {token && !expired && (
        <>
          {/* the freshly-created key, shown once */}
          {newKey && (
            <div style={{ ...card, borderColor: 'var(--ink)', marginBottom: 22 }}>
              <p style={{ margin: '0 0 10px', fontSize: 13.5, color: 'rgba(255,255,255,0.82)' }}>
                <b style={{ color: 'var(--ink)' }}>Copy this key now.</b> For your security it is shown only once.
              </p>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <code style={{ fontFamily: MONO, fontSize: 12.5, color: '#e2e8f0', background: '#0d1117', border: '1px solid var(--hairline)', borderRadius: 8, padding: '10px 12px', wordBreak: 'break-all', flex: 1, minWidth: 220 }}>{newKey}</code>
                <button style={btn} onClick={() => { navigator.clipboard.writeText(newKey); setCopied(true) }}>{copied ? 'Copied' : 'Copy'}</button>
              </div>
            </div>
          )}

          {/* create */}
          <div style={{ ...card, marginBottom: 26 }}>
            <span style={eyebrow}>Create a key</span>
            <div style={{ display: 'flex', gap: 10, marginTop: 12, flexWrap: 'wrap' }}>
              <input style={{ ...input, flex: 1, minWidth: 200 }} placeholder="Label (optional), e.g. production" value={label} onChange={e => setLabel(e.target.value)} />
              <button style={{ ...btn, opacity: busy ? 0.6 : 1 }} disabled={busy} onClick={createKey}>{busy ? 'Creating…' : 'Create API key'}</button>
            </div>
          </div>

          {/* list */}
          <span style={eyebrow}>Your keys</span>
          {keys.length === 0 && <p style={{ fontSize: 14, color: 'var(--slate)', marginTop: 12 }}>No keys yet. Create one above.</p>}
          <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {keys.map(k => (
              <div key={k.id} style={{ ...card, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', opacity: k.revoked ? 0.55 : 1 }}>
                <div>
                  <div style={{ fontSize: 14.5, fontWeight: 600, color: 'var(--ink)' }}>{k.label} <span style={{ fontFamily: MONO, fontWeight: 400, color: 'var(--slate)' }}>· ••••{k.last4}</span></div>
                  <div style={{ fontSize: 12.5, color: 'var(--slate)', marginTop: 3 }}>
                    {k.created_at ? new Date(k.created_at).toLocaleDateString() : ''}{k.revoked ? ' · revoked' : ' · active'}
                  </div>
                </div>
                {!k.revoked && (
                  <button onClick={() => revokeKey(k.id)} style={{ fontFamily: MONO, fontSize: 11.5, letterSpacing: '0.06em', textTransform: 'uppercase', color: '#f87171', background: 'none', border: '1px solid rgba(248,113,113,0.4)', borderRadius: 7, padding: '7px 13px', cursor: 'pointer' }}>Revoke</button>
                )}
              </div>
            ))}
          </div>

          <p style={{ fontSize: 13.5, color: 'var(--slate)', marginTop: 30, lineHeight: 1.6 }}>
            Use your key as a bearer token: <code style={{ fontFamily: MONO, fontSize: '0.9em', background: 'rgba(255,255,255,0.06)', border: '1px solid var(--hairline)', borderRadius: 5, padding: '1px 6px' }}>Authorization: Bearer &lt;key&gt;</code>. See the <a href="/docs" style={{ color: 'var(--ink)' }}>API reference</a>.
          </p>
        </>
      )}
      </main>
    </div>
  )
}
