'use client'
import { useEffect, useState, useCallback } from 'react'
import { parseDataset, type ParsedDataset } from '../lib/dataset'

const STAGES = ['submitted', 'scoping', 'agreement', 'pilot', 'production', 'delivered']
const STORAGE_KEY = 'senebiclabs_admin_key'
const API_BASE = 'https://senebiclabs-api-777437555578.us-central1.run.app/api/v1/project'
// value, label, required data columns
const LS_TASK_TYPES: [string, string, string][] = [
  ['eval_rating', 'Rate response (1-5)', 'prompt, output'],
  ['rubric_eval', 'Rubric evaluation (multi-axis)', 'prompt, output'],
  ['preference', 'Compare A / B', 'prompt, output_a, output_b'],
  ['response_writing', 'Write ideal answer', 'prompt'],
  ['safety_review', 'Safety review', 'prompt, output'],
  ['error_annotation', 'Error annotation (highlight)', 'prompt, output'],
  ['fact_verification', 'Fact verification (highlight)', 'prompt, output'],
  ['de_identification', 'De-identify PHI (highlight)', 'text'],
  ['clinical_extraction', 'Clinical extraction (NER)', 'text'],
  ['classification', 'Classify text', 'text'],
]
const TASK_COLUMNS = Object.fromEntries(LS_TASK_TYPES.map(([v, , cols]) => [v, cols]))

type Submission = {
  id: string
  name: string | null
  email: string | null
  company: string | null
  description: string | null
  task_type: string | null
  volume: string | null
  timeline: string | null
  stage: string | null
  stage_note: string | null
  status: string | null
  created_at: string | null
  updated_at: string | null
  total?: number
  done?: number
  rate_per_item: number | null
  difficulty: string | null
}

type Edit = { stage: string; note: string }

type Clinician = {
  id: string
  name: string
  email: string | null
  access_code: string
  active: boolean
  created_at: string | null
}

const page: React.CSSProperties = {
  minHeight: '100vh',
  background: '#ffffff',
  color: '#0f172a',
}
const card: React.CSSProperties = {
  border: '1px solid #e6e8eb',
  borderRadius: 14,
  padding: 26,
  marginBottom: 18,
  background: '#ffffff',
  boxShadow: '0 1px 2px rgba(15,23,42,0.04)',
}
const input: React.CSSProperties = {
  background: '#ffffff',
  border: '1px solid #d6dae0',
  borderRadius: 8,
  padding: '11px 13px',
  color: '#0f172a',
  fontFamily: 'inherit',
  fontSize: 14,
  outline: 'none',
}
const btn: React.CSSProperties = {
  background: '#15a34a',
  color: '#ffffff',
  border: 'none',
  borderRadius: 8,
  padding: '11px 18px',
  fontSize: 13.5,
  fontWeight: 600,
  cursor: 'pointer',
  fontFamily: 'inherit',
}
const label: React.CSSProperties = {
  fontFamily: 'Geist Mono, monospace',
  fontSize: 10.5,
  letterSpacing: '0.12em',
  textTransform: 'uppercase',
  color: '#64748b',
}
const dropzone: React.CSSProperties = {
  display: 'block',
  border: '1px dashed #cbd1d8',
  borderRadius: 12,
  padding: '32px 20px',
  textAlign: 'center',
  cursor: 'pointer',
  background: '#fafbfc',
}

export default function AdminPage() {
  const [key, setKey] = useState('')
  const [authed, setAuthed] = useState(false)
  const [keyInput, setKeyInput] = useState('')
  const [subs, setSubs] = useState<Submission[]>([])
  const [edits, setEdits] = useState<Record<string, Edit>>({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [savingId, setSavingId] = useState<string | null>(null)
  const [savedId, setSavedId] = useState<string | null>(null)
  const [openId, setOpenId] = useState<string | null>(null)
  const [progress, setProgress] = useState<Record<string, { total: number; done: number }>>({})
  const [parsed, setParsed] = useState<Record<string, ParsedDataset & { filename: string }>>({})
  const [parseErr, setParseErr] = useState<Record<string, string>>({})
  const [addingId, setAddingId] = useState<string | null>(null)
  const [addMsg, setAddMsg] = useState<Record<string, string>>({})
  const [clinicians, setClinicians] = useState<Clinician[]>([])
  const [cName, setCName] = useState('')
  const [cEmail, setCEmail] = useState('')
  const [creatingC, setCreatingC] = useState(false)
  const [cMsg, setCMsg] = useState('')
  const [pickClin, setPickClin] = useState<Record<string, string>>({})
  const [copied, setCopied] = useState<string | null>(null)
  const [lsSyncing, setLsSyncing] = useState<string | null>(null)
  const [lsMsg, setLsMsg] = useState<Record<string, string>>({})
  const [lsType, setLsType] = useState<Record<string, string>>({})
  const [meta, setMeta] = useState<Record<string, { rate: string; difficulty: string }>>({})
  const [metaSaving, setMetaSaving] = useState<string | null>(null)
  const [metaMsg, setMetaMsg] = useState<Record<string, string>>({})
  const [cfgOpen, setCfgOpen] = useState<string | null>(null)
  const [cfgText, setCfgText] = useState<Record<string, string>>({})
  const [cfgSaving, setCfgSaving] = useState<string | null>(null)
  const [cfgMsg, setCfgMsg] = useState<Record<string, string>>({})
  const [assigning, setAssigning] = useState<string | null>(null)
  const [assignMsg, setAssignMsg] = useState<Record<string, string>>({})
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [reportData, setReportData] = useState<Record<string, any>>({})
  const [reportLoading, setReportLoading] = useState<string | null>(null)
  const [apiKey, setApiKey] = useState<Record<string, string>>({})
  const [apiKeyLoading, setApiKeyLoading] = useState<string | null>(null)

  const load = useCallback(async (k: string) => {
    setLoading(true)
    setError('')
    try {
      const res = await fetch('/api/admin/submissions', { headers: { 'x-admin-key': k } })
      const data = await res.json()
      if (!data.ok) throw new Error(data.message ?? 'Could not load.')
      const list: Submission[] = data.submissions ?? []
      setSubs(list)
      setEdits(Object.fromEntries(list.map(s => [s.id, { stage: s.stage ?? 'submitted', note: s.stage_note ?? '' }])))
      setAuthed(true)
      setKey(k)
      localStorage.setItem(STORAGE_KEY, k)
      try {
        const cr = await fetch('/api/admin/clinicians', { headers: { 'x-admin-key': k } })
        const cd = await cr.json()
        if (cd.ok) setClinicians(cd.clinicians ?? [])
      } catch { /* ignore  */ }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Could not load.')
      setAuthed(false)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved) load(saved)
  }, [load])

  const update = async (id: string) => {
    const edit = edits[id]
    if (!edit) return
    setSavingId(id)
    setError('')
    try {
      const res = await fetch('/api/admin/advance', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-admin-key': key },
        body: JSON.stringify({ submission_id: id, stage: edit.stage, note: edit.note || null }),
      })
      const data = await res.json()
      if (!data.ok) throw new Error(data.message ?? 'Update failed.')
      setSubs(prev => prev.map(s => (s.id === id ? { ...s, stage: edit.stage, stage_note: edit.note } : s)))
      setSavedId(id)
      setTimeout(() => setSavedId(p => (p === id ? null : p)), 2000)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Update failed.')
    } finally {
      setSavingId(null)
    }
  }

  const logout = () => {
    localStorage.removeItem(STORAGE_KEY)
    setAuthed(false)
    setKey('')
    setSubs([])
    setClinicians([])
  }

  const fetchProgress = useCallback(async (id: string, k: string) => {
    try {
      const res = await fetch(`/api/admin/progress?project=${id}`, { headers: { 'x-admin-key': k } })
      const data = await res.json()
      if (data.ok) setProgress(p => ({ ...p, [id]: { total: data.total, done: data.done } }))
    } catch { /* ignore  */ }
  }, [])

  const toggleItems = (id: string) => {
    const opening = openId !== id
    setOpenId(opening ? id : null)
    if (opening) fetchProgress(id, key)
  }

  const onFile = async (id: string, file: File) => {
    setParseErr(e => ({ ...e, [id]: '' }))
    setAddMsg(m => ({ ...m, [id]: '' }))
    try {
      const text = await file.text()
      const { items, columns } = parseDataset(file.name, text)
      if (!items.length) throw new Error('No rows found in that file.')
      setParsed(p => ({ ...p, [id]: { items, columns, filename: file.name } }))
    } catch (err: unknown) {
      setParsed(p => { const n = { ...p }; delete n[id]; return n })
      setParseErr(e => ({ ...e, [id]: err instanceof Error ? err.message : 'Could not read that file.' }))
    }
  }

  const clearParsed = (id: string) =>
    setParsed(p => { const n = { ...p }; delete n[id]; return n })

  const addParsed = async (id: string) => {
    const p = parsed[id]
    if (!p) return
    setAddingId(id)
    try {
      const res = await fetch('/api/admin/items', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-admin-key': key },
        body: JSON.stringify({ project_id: id, items: p.items }),
      })
      const data = await res.json()
      if (!data.ok) throw new Error(data.message ?? 'Failed')
      setAddMsg(m => ({ ...m, [id]: data.message }))
      clearParsed(id)
      fetchProgress(id, key)
    } catch (err: unknown) {
      setAddMsg(m => ({ ...m, [id]: err instanceof Error ? err.message : 'Failed' }))
    } finally {
      setAddingId(null)
    }
  }

  const exportProject = async (id: string, company: string | null) => {
    const res = await fetch(`/api/admin/export?project=${id}`, { headers: { 'x-admin-key': key } })
    const data = await res.json()
    if (!data.ok) return
    const blob = new Blob([JSON.stringify(data.items, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${(company || 'project').replace(/\s+/g, '_')}_labels.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  const prog = (s: Submission) => progress[s.id] ?? { total: s.total ?? 0, done: s.done ?? 0 }
  const stageLocked = (st: string, p: { total: number; done: number }) =>
    ((st === 'pilot' || st === 'production') && p.total === 0) ||
    (st === 'delivered' && (p.total === 0 || p.done < p.total))

  const createClinician = async () => {
    if (!cName.trim() || creatingC) return
    setCreatingC(true); setCMsg('')
    try {
      const res = await fetch('/api/admin/clinicians', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-admin-key': key },
        body: JSON.stringify({ name: cName.trim(), email: cEmail.trim() || null }),
      })
      const data = await res.json()
      if (!data.ok) throw new Error(data.message ?? 'Failed')
      setCMsg(`Added ${data.clinician.name}`)
      setCName(''); setCEmail('')
      const cr = await fetch('/api/admin/clinicians', { headers: { 'x-admin-key': key } })
      const cd = await cr.json()
      if (cd.ok) setClinicians(cd.clinicians ?? [])
    } catch (err: unknown) {
      setCMsg(err instanceof Error ? err.message : 'Failed')
    } finally {
      setCreatingC(false)
    }
  }

  const mrate = (s: Submission) => meta[s.id]?.rate ?? (s.rate_per_item != null ? String(s.rate_per_item) : '')
  const mdiff = (s: Submission) => meta[s.id]?.difficulty ?? s.difficulty ?? ''

  const saveMeta = async (s: Submission) => {
    setMetaSaving(s.id); setMetaMsg(m => ({ ...m, [s.id]: '' }))
    try {
      const rate = mrate(s).trim()
      const res = await fetch('/api/admin/project-meta', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-admin-key': key },
        body: JSON.stringify({ project_id: s.id, rate_per_item: rate ? parseFloat(rate) : null, difficulty: mdiff(s) || null }),
      })
      const data = await res.json()
      if (!data.ok) throw new Error(data.message ?? 'Failed')
      setMetaMsg(m => ({ ...m, [s.id]: 'Saved ✓' }))
      setTimeout(() => setMetaMsg(m => ({ ...m, [s.id]: '' })), 1600)
    } catch (err: unknown) {
      setMetaMsg(m => ({ ...m, [s.id]: err instanceof Error ? err.message : 'Failed' }))
    } finally {
      setMetaSaving(null)
    }
  }

  const copyLink = (projectId: string, code: string) => {
    const link = `${window.location.origin}/work?project=${projectId}&code=${code}`
    navigator.clipboard?.writeText(link)
    const tag = projectId + code
    setCopied(tag)
    setTimeout(() => setCopied(c => (c === tag ? null : c)), 2000)
  }

  const lsSync = async (id: string) => {
    setLsSyncing(id)
    setLsMsg(m => ({ ...m, [id]: '' }))
    try {
      const res = await fetch('/api/admin/ls-sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-admin-key': key },
        body: JSON.stringify({ project_id: id, task_type: lsType[id] || 'eval_rating' }),
      })
      const data = await res.json()
      if (!data.ok) throw new Error(data.message ?? 'Sync failed.')
      setLsMsg(m => ({ ...m, [id]: `Sent ${data.pushed} tasks to Label Studio` }))
    } catch (err: unknown) {
      setLsMsg(m => ({ ...m, [id]: err instanceof Error ? err.message : 'Sync failed.' }))
    } finally {
      setLsSyncing(null)
    }
  }

  const lsPull = async (id: string) => {
    setLsSyncing(id)
    setLsMsg(m => ({ ...m, [id]: '' }))
    try {
      const res = await fetch('/api/admin/ls-pull', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-admin-key': key },
        body: JSON.stringify({ project_id: id }),
      })
      const data = await res.json()
      if (!data.ok) throw new Error(data.message ?? 'Pull failed.')
      setLsMsg(m => ({ ...m, [id]: `Pulled ${data.pulled} labeled items back` }))
      load(key)
    } catch (err: unknown) {
      setLsMsg(m => ({ ...m, [id]: err instanceof Error ? err.message : 'Pull failed.' }))
    } finally {
      setLsSyncing(null)
    }
  }

  const IMAGE_TEMPLATE = JSON.stringify({
    title: 'Chest X-ray classification review',
    reviewers_per_item: 5,
    schema: {
      input: 'image',
      classes: ['Normal', 'Pneumonia', 'Effusion'],
      multi_label: false,
      case_id_field: 'study_id',
      fields: {
        verdict: { type: 'single', options: ['Correct', 'Incorrect', 'Partially correct'], required: true },
        correct_label: { type: 'from_classes', visible_when: 'verdict!=Correct' },
        critical_miss: { type: 'structured', visible_when: 'verdict!=Correct' },
      },
    },
  }, null, 2)

  const TEXT_TEMPLATE = JSON.stringify({
    title: 'Response review',
    reviewers_per_item: 5,
    schema: {
      input: 'text',
      context: [
        { key: 'prompt', label: 'Prompt' },
        { key: 'output', label: 'Model output' },
      ],
      classes: ['Correct', 'Incorrect', 'Partially correct'],
      fields: {
        verdict: { type: 'single', options: ['Correct', 'Incorrect', 'Partially correct'], required: true },
        quality: { type: 'scale', max: 5 },
        notes: { type: 'text', placeholder: 'Rationale (optional)' },
      },
    },
  }, null, 2)

  const toggleConfig = (id: string) => {
    const opening = cfgOpen !== id
    setCfgOpen(opening ? id : null)
    if (opening && cfgText[id] === undefined) setCfgText(t => ({ ...t, [id]: IMAGE_TEMPLATE }))
  }

  const saveConfig = async (id: string) => {
    let cfg: unknown
    try { cfg = JSON.parse(cfgText[id] ?? '') } catch { setCfgMsg(m => ({ ...m, [id]: 'Invalid JSON, check your brackets/commas.' })); return }
    setCfgSaving(id); setCfgMsg(m => ({ ...m, [id]: '' }))
    try {
      const res = await fetch('/api/admin/eval-config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-admin-key': key },
        body: JSON.stringify({ project_id: id, eval_config: cfg }),
      })
      const data = await res.json()
      if (!data.ok) throw new Error(data.message ?? 'Failed')
      setCfgMsg(m => ({ ...m, [id]: 'Config saved ✓' }))
    } catch (err: unknown) {
      setCfgMsg(m => ({ ...m, [id]: err instanceof Error ? err.message : 'Failed' }))
    } finally { setCfgSaving(null) }
  }

  const assignClinician = async (id: string, clinicianId: string) => {
    if (!clinicianId) return
    setAssigning(id); setAssignMsg(m => ({ ...m, [id]: '' }))
    try {
      const res = await fetch('/api/admin/assign-clinician', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-admin-key': key },
        body: JSON.stringify({ project_id: id, clinician_id: clinicianId }),
      })
      const data = await res.json()
      if (!data.ok) throw new Error(data.message ?? 'Failed')
      setAssignMsg(m => ({ ...m, [id]: 'Assigned ✓' }))
    } catch (err: unknown) {
      setAssignMsg(m => ({ ...m, [id]: err instanceof Error ? err.message : 'Failed' }))
    } finally { setAssigning(null) }
  }

  const viewReport = async (id: string) => {
    setReportLoading(id)
    try {
      const res = await fetch(`/api/admin/report?project=${id}`, { headers: { 'x-admin-key': key } })
      const data = await res.json()
      if (!data.ok) throw new Error(data.message ?? 'Failed')
      setReportData(r => ({ ...r, [id]: data.report }))
    } catch (err: unknown) {
      setReportData(r => ({ ...r, [id]: { error: err instanceof Error ? err.message : 'Failed' } }))
    } finally { setReportLoading(null) }
  }

  const rpct = (v: number | null | undefined) => (v == null ? 'n/a' : `${Math.round(v * 100)}%`)

  const genApiKey = async (id: string) => {
    setApiKeyLoading(id)
    try {
      const res = await fetch('/api/admin/api-key', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-admin-key': key },
        body: JSON.stringify({ project_id: id }),
      })
      const data = await res.json()
      if (!data.ok) throw new Error(data.message ?? 'Failed')
      setApiKey(k => ({ ...k, [id]: data.api_key }))
    } catch (err: unknown) {
      setApiKey(k => ({ ...k, [id]: 'ERROR: ' + (err instanceof Error ? err.message : 'Failed') }))
    } finally { setApiKeyLoading(null) }
  }

  // Key gate
  if (!authed) {
    return (
      <main style={{ ...page, display: 'grid', placeItems: 'center', padding: 24 }}>
        <form
          onSubmit={e => { e.preventDefault(); if (keyInput.trim()) load(keyInput.trim()) }}
          style={{ width: '100%', maxWidth: 360, display: 'flex', flexDirection: 'column', gap: 16 }}
        >
          <h1 style={{ fontFamily: 'Geist, sans-serif', fontWeight: 500, fontSize: 26 }}>Admin</h1>
          <input
            style={input}
            type="password"
            placeholder="Admin key"
            value={keyInput}
            onChange={e => setKeyInput(e.target.value)}
            autoFocus
          />
          <button style={btn} type="submit" disabled={loading}>{loading ? 'Checking…' : 'Enter'}</button>
          {error && <p style={{ color: '#dc2626', fontSize: 13 }}>{error}</p>}
        </form>
      </main>
    )
  }

  // Dashboard
  return (
    <div style={page}>
    <main style={{ maxWidth: 880, margin: '0 auto', padding: '48px 24px 96px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <h1 style={{ fontFamily: 'Geist, sans-serif', fontWeight: 500, fontSize: 30 }}>Projects</h1>
        <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
          <button onClick={() => load(key)} style={{ ...label, background: 'none', border: 'none', cursor: 'pointer' }}>Refresh</button>
          <button onClick={logout} style={{ ...label, background: 'none', border: 'none', cursor: 'pointer' }}>Sign out</button>
        </div>
      </div>
      <p style={{ ...label, marginBottom: 28 }}>{subs.length} total</p>

      {/* Clinicians  */}
      <div style={{ ...card, marginBottom: 30 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 16 }}>
          <h2 style={{ fontSize: 16, fontWeight: 600 }}>Clinicians</h2>
          <span style={label}>{clinicians.length}</span>
        </div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
          <input style={{ ...input, minWidth: 150 }} placeholder="Name" value={cName} onChange={e => setCName(e.target.value)} />
          <input style={{ ...input, minWidth: 180 }} placeholder="Email (optional)" value={cEmail} onChange={e => setCEmail(e.target.value)} />
          <button style={btn} onClick={createClinician} disabled={creatingC || !cName.trim()}>{creatingC ? 'Adding…' : 'Add clinician'}</button>
          {cMsg && <span style={{ ...label, color: '#15a34a' }}>{cMsg}</span>}
        </div>
        {clinicians.map(c => (
          <div key={c.id} style={{ display: 'flex', gap: 12, alignItems: 'center', padding: '10px 0', borderTop: '1px solid #eef0f2', flexWrap: 'wrap', marginTop: 4 }}>
            <span style={{ fontSize: 14, fontWeight: 500, minWidth: 130 }}>{c.name}</span>
            {c.email && <span style={{ fontSize: 13, color: '#475569' }}>{c.email}</span>}
            <code style={{ marginLeft: 'auto', fontFamily: 'Geist Mono, monospace', fontSize: 12.5, color: '#0f172a', background: '#f8fafc', border: '1px solid #eef0f2', borderRadius: 6, padding: '4px 9px' }}>{c.access_code}</code>
          </div>
        ))}
      </div>

      {error && <p style={{ color: '#dc2626', fontSize: 14, marginBottom: 20 }}>{error}</p>}
      {loading && <p style={{ color: '#475569', fontSize: 15 }}>Loading…</p>}

      {!loading && subs.length === 0 && (
        <p style={{ color: '#475569', fontSize: 15 }}>No submissions yet.</p>
      )}

      {subs.map(s => {
        const edit = edits[s.id] ?? { stage: 'submitted', note: '' }
        const p = prog(s)
        const selClin = clinicians.find(c => c.id === pickClin[s.id])
        return (
          <div key={s.id} style={card}>
            <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
              <h2 style={{ fontSize: 20, fontWeight: 600 }}>{s.company || ', '}</h2>
              <span style={{ ...label, color: '#15a34a' }}>{s.stage ?? 'submitted'}</span>
            </div>
            <p style={{ fontSize: 14, color: '#475569', marginTop: 4 }}>
              {s.name} · <a href={`mailto:${s.email}`} style={{ color: '#0f172a' }}>{s.email}</a>
              {s.created_at && <> · {new Date(s.created_at).toLocaleDateString()}</>}
            </p>

            {s.task_type && <p style={{ fontSize: 14, color: '#0f172a', marginTop: 12 }}><span style={label}>Wants </span>{s.task_type}</p>}
            {s.description && <p style={{ fontSize: 14.5, lineHeight: 1.6, color: '#0f172a', marginTop: 10, whiteSpace: 'pre-wrap' }}>{s.description}</p>}

            <div style={{ display: 'flex', gap: 10, marginTop: 20, flexWrap: 'wrap', alignItems: 'center' }}>
              <select
                style={{ ...input, cursor: 'pointer' }}
                value={edit.stage}
                onChange={e => setEdits(pp => ({ ...pp, [s.id]: { ...edit, stage: e.target.value } }))}
              >
                {STAGES.map(st => {
                  const locked = stageLocked(st, p)
                  return (
                    <option key={st} value={st} disabled={locked} style={{ background: '#ffffff', color: locked ? '#9aa1a9' : '#0f172a' }}>
                      {st}{locked ? ', locked' : ''}
                    </option>
                  )
                })}
              </select>
              <input
                style={{ ...input, flex: 1, minWidth: 200 }}
                placeholder="Note shown to the customer (optional)"
                value={edit.note}
                onChange={e => setEdits(pp => ({ ...pp, [s.id]: { ...edit, note: e.target.value } }))}
              />
              <button style={btn} onClick={() => update(s.id)} disabled={savingId === s.id}>
                {savingId === s.id ? 'Saving…' : savedId === s.id ? 'Saved ✓' : 'Update'}
              </button>
            </div>
            {(p.total === 0 || p.done < p.total) && (
              <p style={{ ...label, color: '#94a3b8', marginTop: 8 }}>
                {p.total === 0 ? 'Add items to unlock pilot / production / delivered' : `Label all items (${p.done}/${p.total}) to unlock delivered`}
              </p>
            )}

            {/* Work: items, queue, export  */}
            <div style={{ marginTop: 18, paddingTop: 18, borderTop: '1px solid #eef0f2', display: 'flex', gap: 18, alignItems: 'center', flexWrap: 'wrap' }}>
              <button onClick={() => toggleItems(s.id)} style={{ ...label, background: 'none', border: 'none', cursor: 'pointer' }}>
                {openId === s.id ? 'Hide items ▲' : 'Add items ▼'}
              </button>
              <button onClick={() => toggleConfig(s.id)} style={{ ...label, background: 'none', border: 'none', cursor: 'pointer', color: '#2563EB' }}>
                {cfgOpen === s.id ? 'Hide config ▲' : 'Set config ▼'}
              </button>
              <button onClick={() => exportProject(s.id, s.company)} style={{ ...label, background: 'none', border: 'none', cursor: 'pointer' }}>Export labels ↓</button>
              <button onClick={() => genApiKey(s.id)} disabled={apiKeyLoading === s.id} style={{ ...label, background: 'none', border: 'none', cursor: 'pointer', color: '#2563EB' }}>
                {apiKeyLoading === s.id ? 'Generating…' : 'API key ⚙'}
              </button>
              {p.total > 0 && (
                <select value={lsType[s.id] ?? 'eval_rating'} onChange={e => setLsType(t => ({ ...t, [s.id]: e.target.value }))} style={{ ...input, padding: '6px 8px', fontSize: 12, cursor: 'pointer' }}>
                  {LS_TASK_TYPES.map(([v, lbl]) => <option key={v} value={v} style={{ background: '#fff' }}>{lbl}</option>)}
                </select>
              )}
              {p.total > 0 && (
                <span style={{ fontSize: 11.5, color: '#94a3b8' }}>needs: {TASK_COLUMNS[lsType[s.id] ?? 'eval_rating']}</span>
              )}
              <button onClick={() => lsSync(s.id)} disabled={lsSyncing === s.id || p.total === 0} style={{ ...label, background: 'none', border: 'none', cursor: p.total === 0 ? 'not-allowed' : 'pointer', color: '#2563EB' }}>
                {lsSyncing === s.id ? 'Sending…' : 'Send to Label Studio ↗'}
              </button>
              <button onClick={() => lsPull(s.id)} disabled={lsSyncing === s.id} style={{ ...label, background: 'none', border: 'none', cursor: 'pointer', color: '#2563EB' }}>
                Pull results ↓
              </button>
              <button onClick={() => viewReport(s.id)} disabled={reportLoading === s.id} style={{ ...label, background: 'none', border: 'none', cursor: 'pointer', color: '#2563EB' }}>
                {reportLoading === s.id ? 'Building…' : 'View report ↧'}
              </button>
              {p.total > 0 && <span style={label}>{p.done}/{p.total} labeled</span>}
              {lsMsg[s.id] && <span style={{ ...label, color: lsMsg[s.id].startsWith('Sent') ? '#15a34a' : '#dc2626' }}>{lsMsg[s.id]}</span>}
              {clinicians.length > 0 && (
                <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center', marginLeft: 'auto' }}>
                  <select
                    value={pickClin[s.id] ?? ''}
                    onChange={e => setPickClin(pc => ({ ...pc, [s.id]: e.target.value }))}
                    style={{ ...input, padding: '6px 10px', fontSize: 12.5, cursor: 'pointer' }}
                  >
                    <option value="">Clinician…</option>
                    {clinicians.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                  </select>
                  {selClin && (
                    <>
                      <button onClick={() => assignClinician(s.id, selClin.id)} disabled={assigning === s.id} style={{ ...label, background: 'none', border: 'none', cursor: 'pointer', color: '#2563EB' }}>
                        {assigning === s.id ? 'Assigning…' : 'Assign'}
                      </button>
                      <button onClick={() => copyLink(s.id, selClin.access_code)} style={{ ...label, background: 'none', border: 'none', cursor: 'pointer', color: '#15a34a' }}>
                        {copied === s.id + selClin.access_code ? 'Copied ✓' : 'Copy link'}
                      </button>
                    </>
                  )}
                  {assignMsg[s.id] && <span style={{ ...label, color: assignMsg[s.id].includes('✓') ? '#15a34a' : '#dc2626' }}>{assignMsg[s.id]}</span>}
                </span>
              )}
            </div>

            <div style={{ display: 'flex', gap: 9, alignItems: 'center', marginTop: 10, flexWrap: 'wrap' }}>
              <span style={label}>Pay</span>
              <span style={{ color: '#475569', fontSize: 13 }}>$</span>
              <input style={{ ...input, width: 70, padding: '6px 9px', fontSize: 13 }} placeholder="0.00" value={mrate(s)} onChange={e => setMeta(m => ({ ...m, [s.id]: { rate: e.target.value, difficulty: mdiff(s) } }))} />
              <span style={{ color: '#475569', fontSize: 13 }}>/ item</span>
              <select style={{ ...input, padding: '6px 9px', fontSize: 13, cursor: 'pointer' }} value={mdiff(s)} onChange={e => setMeta(m => ({ ...m, [s.id]: { rate: mrate(s), difficulty: e.target.value } }))}>
                <option value="">Difficulty…</option>
                <option value="Easy">Easy</option>
                <option value="Standard">Standard</option>
                <option value="Hard">Hard</option>
              </select>
              <button style={{ ...label, background: 'none', border: 'none', cursor: 'pointer', color: '#2563EB' }} onClick={() => saveMeta(s)} disabled={metaSaving === s.id}>
                {metaSaving === s.id ? 'Saving…' : 'Save pay'}
              </button>
              {metaMsg[s.id] && <span style={{ ...label, color: metaMsg[s.id].includes('✓') ? '#15a34a' : '#dc2626' }}>{metaMsg[s.id]}</span>}
            </div>

            {cfgOpen === s.id && (
              <div style={{ marginTop: 16, padding: 16, border: '1px solid #eef0f2', borderRadius: 10, background: '#fafbfc' }}>
                <p style={{ ...label, marginBottom: 4 }}>Task config</p>
                <p style={{ fontSize: 12.5, color: '#475569', marginBottom: 10 }}>Pick a template: <strong>Image</strong> for X-rays/scans (each item needs an image), <strong>Text</strong> for rating a model&apos;s written answers. Then edit the <code style={{ fontFamily: 'Geist Mono, monospace' }}>classes</code> to match the client&apos;s labels.</p>
                <textarea
                  value={cfgText[s.id] ?? ''}
                  onChange={e => setCfgText(t => ({ ...t, [s.id]: e.target.value }))}
                  spellCheck={false}
                  style={{ width: '100%', minHeight: 240, boxSizing: 'border-box', fontFamily: 'Geist Mono, monospace', fontSize: 12.5, lineHeight: 1.5, padding: 12, border: '1px solid #d6dae0', borderRadius: 8, color: '#0f172a', background: '#ffffff', resize: 'vertical' }}
                />
                <div style={{ display: 'flex', gap: 14, alignItems: 'center', marginTop: 12, flexWrap: 'wrap' }}>
                  <button style={btn} onClick={() => saveConfig(s.id)} disabled={cfgSaving === s.id}>{cfgSaving === s.id ? 'Saving…' : 'Save config'}</button>
                  <button onClick={() => setCfgText(t => ({ ...t, [s.id]: IMAGE_TEMPLATE }))} style={{ ...label, background: 'none', border: 'none', cursor: 'pointer' }}>Image template</button>
                  <button onClick={() => setCfgText(t => ({ ...t, [s.id]: TEXT_TEMPLATE }))} style={{ ...label, background: 'none', border: 'none', cursor: 'pointer' }}>Text template</button>
                  {cfgMsg[s.id] && <span style={{ ...label, color: cfgMsg[s.id].includes('✓') ? '#15a34a' : '#dc2626' }}>{cfgMsg[s.id]}</span>}
                </div>
              </div>
            )}

            {reportData[s.id] && (
              <div style={{ marginTop: 16, padding: 16, border: '1px solid #d9f0e0', background: '#f4fbf6', borderRadius: 10 }}>
                {reportData[s.id].error ? (
                  <p style={{ color: '#dc2626', fontSize: 13 }}>{reportData[s.id].error}</p>
                ) : (
                  <>
                    <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 }}>
                      <span style={{ ...label, color: '#15803d' }}>Report</span>
                      <button onClick={() => setReportData(r => { const n = { ...r }; delete n[s.id]; return n })} style={{ ...label, background: 'none', border: 'none', cursor: 'pointer' }}>Hide</button>
                    </div>
                    <p style={{ fontSize: 15, margin: '8px 0 4px' }}>
                      <strong style={{ fontSize: 22 }}>{rpct(reportData[s.id].accuracy?.value)}</strong>{' '}
                      accuracy
                      {reportData[s.id].accuracy?.assessable != null && (
                        <span style={{ color: '#475569' }}>, {reportData[s.id].accuracy?.correct}/{reportData[s.id].accuracy?.assessable} correct</span>
                      )}
                    </p>
                    {reportData[s.id].qa && (
                      <p style={{ fontSize: 13, margin: '2px 0 4px', color: '#334155' }}>
                        <strong>{rpct(reportData[s.id].qa.mean_agreement)}</strong> inter-reviewer agreement
                        {' '}({reportData[s.id].qa.reviewers} reviewers/item)
                        {reportData[s.id].qa.disagreements > 0 && (
                          <span style={{ color: '#b45309' }}> · {reportData[s.id].qa.disagreements} to adjudicate</span>
                        )}
                      </p>
                    )}
                    {Array.isArray(reportData[s.id].critical_misses) && reportData[s.id].critical_misses.length > 0 && (
                      <div style={{ marginTop: 12 }}>
                        <span style={{ ...label, color: '#b91c1c' }}>Critical misses ({reportData[s.id].critical_misses.length})</span>
                        {/* eslint-disable-next-line @typescript-eslint/no-explicit-any  */}
                        {reportData[s.id].critical_misses.map((c: any, i: number) => (
                          <div key={i} style={{ fontSize: 13, marginTop: 8, lineHeight: 1.5, paddingTop: 8, borderTop: i ? '1px solid #eef0f2' : 'none' }}>
                            <div style={{ display: 'flex', gap: 8, justifyContent: 'space-between', flexWrap: 'wrap' }}>
                              <code style={{ fontFamily: 'Geist Mono, monospace', color: '#334155' }}>{c.case_id || `#${c.idx}`}</code>
                              {c.finding && <span style={{ color: '#b91c1c', fontWeight: 600, fontSize: 12 }}>{c.finding}</span>}
                            </div>
                            {c.prompt && <p style={{ margin: '5px 0 6px', color: '#0f172a' }}>&ldquo;{c.prompt}&rdquo;</p>}
                            <span>model said <strong>{c.model_prediction ?? ', '}</strong>{' → clinician read '}<strong>{c.correct_label ?? ', '}</strong></span>
                            {c.rationale && <p style={{ margin: '5px 0 0', color: '#64748b', fontStyle: 'italic' }}>{c.rationale}</p>}
                          </div>
                        ))}
                      </div>
                    )}
                  </>
                )}
              </div>
            )}

            {apiKey[s.id] && (
              <div style={{ marginTop: 16, padding: 16, border: '1px solid #eef0f2', borderRadius: 10, background: '#0f172a', color: '#e2e8f0' }}>
                <p style={{ ...label, color: '#7dd3fc', marginBottom: 10 }}>API key for this project — send to the client</p>
                <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 14 }}>
                  <code style={{ fontFamily: 'Geist Mono, monospace', fontSize: 12, background: '#020617', border: '1px solid #1e293b', borderRadius: 6, padding: '8px 10px', wordBreak: 'break-all', flex: 1, minWidth: 240 }}>{apiKey[s.id]}</code>
                  <button onClick={() => { navigator.clipboard?.writeText(apiKey[s.id]); setCopied('apikey' + s.id); setTimeout(() => setCopied(c => (c === 'apikey' + s.id ? null : c)), 1500) }} style={{ ...btn, padding: '8px 14px', fontSize: 12 }}>
                    {copied === 'apikey' + s.id ? 'Copied ✓' : 'Copy'}
                  </button>
                </div>
                <p style={{ fontFamily: 'Geist Mono, monospace', fontSize: 11.5, color: '#94a3b8', lineHeight: 1.8 }}>
                  Base: {API_BASE}<br />
                  POST /ingest &nbsp;·&nbsp; body {'{'} project_id: <span style={{ color: '#7dd3fc' }}>{s.id}</span>, items: [...], webhook_url? {'}'}<br />
                  GET&nbsp; /results?project_id=<span style={{ color: '#7dd3fc' }}>{s.id}</span><br />
                  Auth: header <span style={{ color: '#e2e8f0' }}>Authorization: Bearer &lt;key&gt;</span>
                </p>
              </div>
            )}

            {openId === s.id && (
              <div style={{ marginTop: 16 }}>
                {!parsed[s.id] ? (
                  <>
                    <label
                      style={dropzone}
                      onDragOver={e => e.preventDefault()}
                      onDrop={e => { e.preventDefault(); const f = e.dataTransfer.files?.[0]; if (f) onFile(s.id, f) }}
                    >
                      <input
                        type="file"
                        accept=".csv,.json,text/csv,application/json"
                        style={{ display: 'none' }}
                        onChange={e => { const f = e.target.files?.[0]; if (f) onFile(s.id, f); e.target.value = '' }}
                      />
                      <span style={{ color: '#0f172a', fontWeight: 500, fontSize: 15 }}>Drop a CSV or JSON file</span>
                      <span style={{ display: 'block', marginTop: 6, fontSize: 13, color: '#475569' }}>
                        or click to browse, each row becomes one task
                      </span>
                    </label>
                    {parseErr[s.id] && <p style={{ color: '#dc2626', fontSize: 13, marginTop: 10 }}>{parseErr[s.id]}</p>}
                  </>
                ) : (
                  <div style={{ border: '1px solid #e6e8eb', borderRadius: 12, padding: 18 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
                      <span style={{ fontSize: 15, fontWeight: 600 }}>{parsed[s.id].filename}</span>
                      <span style={{ ...label, color: '#15a34a' }}>{parsed[s.id].items.length} items</span>
                    </div>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 12 }}>
                      {parsed[s.id].columns.map(col => (
                        <span key={col} style={{ ...label, border: '1px solid #e6e8eb', borderRadius: 999, padding: '4px 10px' }}>{col}</span>
                      ))}
                    </div>
                    <div style={{ marginTop: 14, padding: '12px 14px', background: '#f8fafc', borderRadius: 8, border: '1px solid #eef0f2' }}>
                      {Object.entries(parsed[s.id].items[0]).slice(0, 4).map(([k, v]) => (
                        <div key={k} style={{ fontSize: 12.5, marginBottom: 4, lineHeight: 1.5 }}>
                          <span style={{ color: '#64748b', fontFamily: 'Geist Mono, monospace' }}>{k}: </span>
                          <span style={{ color: '#0f172a' }}>{String(v).slice(0, 140)}</span>
                        </div>
                      ))}
                    </div>
                    <div style={{ display: 'flex', gap: 14, marginTop: 16, alignItems: 'center', flexWrap: 'wrap' }}>
                      <button style={btn} onClick={() => addParsed(s.id)} disabled={addingId === s.id}>
                        {addingId === s.id ? 'Adding…' : `Add ${parsed[s.id].items.length} items to queue`}
                      </button>
                      <button onClick={() => clearParsed(s.id)} style={{ ...label, background: 'none', border: 'none', cursor: 'pointer' }}>
                        Choose another file
                      </button>
                    </div>
                  </div>
                )}
                {addMsg[s.id] && <p style={{ ...label, color: '#15a34a', marginTop: 12 }}>{addMsg[s.id]}</p>}
              </div>
            )}
          </div>
        )
      })}
    </main>
    </div>
  )
}
