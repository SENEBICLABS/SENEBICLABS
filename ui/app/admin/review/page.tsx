'use client'
/**
 * Run a project — the operator screen used while work is under way.
 *
 * Ordered the way a pilot runs: how far along it is, the cases clinicians could not agree
 * on, delivery, and the report the client receives. Setup (config, items, keys, clinicians)
 * stays on /admin, because it happens once per project rather than twice a week.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'

const STORAGE_KEY = 'senebiclabs_admin_key'

type Submission = {
  id: string
  name: string | null
  company: string | null
  stage: string | null
  total?: number
  done?: number
}

type Annotation = { by?: string | null; at?: string | null; label: Record<string, unknown> }

type Held = {
  idx: number
  agreement: number | null
  reviewers: number | null
  consensus_verdict?: string | null
  annotations: Annotation[] | null
  content: Record<string, unknown> | null
  second_reading?: Record<string, unknown> | null
}

/* eslint-disable @typescript-eslint/no-explicit-any */
type Report = any

const show = (v: unknown): string => {
  if (v === null || v === undefined || v === '') return '—'
  if (Array.isArray(v)) return v.map(show).join(' · ')
  if (typeof v === 'object') {
    const o = v as Record<string, unknown>
    if ('present' in o) return o.present ? `Yes — ${show(o.finding)}` : 'No'
    return JSON.stringify(o)
  }
  return String(v)
}

/** Field names a clinician answered, excluding internal keys. */
const answeredFields = (anns: Annotation[]): string[] => {
  const names: string[] = []
  for (const a of anns) {
    for (const k of Object.keys(a.label ?? {})) {
      if (!k.startsWith('_') && !names.includes(k)) names.push(k)
    }
  }
  return names
}

export default function RunProjectPage() {
  const [key, setKey] = useState('')
  const [keyInput, setKeyInput] = useState('')
  const [authed, setAuthed] = useState(false)
  const [subs, setSubs] = useState<Submission[]>([])
  const [selected, setSelected] = useState<string>('')
  const [progress, setProgress] = useState<{ total: number; done: number } | null>(null)
  const [held, setHeld] = useState<Held[]>([])
  const [report, setReport] = useState<Report | null>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<Record<number, string>>({})
  const [decidedBy, setDecidedBy] = useState('')
  const [picked, setPicked] = useState<Record<string, string>>({})   // `${idx}:${field}` -> value
  const [msg, setMsg] = useState('')
  const [error, setError] = useState('')

  const project = useMemo(() => subs.find(s => s.id === selected) ?? null, [subs, selected])

  const loadProjects = useCallback(async (k: string) => {
    setError('')
    try {
      const res = await fetch('/api/admin/submissions', { headers: { 'x-admin-key': k } })
      const data = await res.json()
      if (!data.ok) throw new Error(data.message ?? 'That key was not accepted.')
      setSubs(data.submissions ?? [])
      setAuthed(true)
      setKey(k)
      localStorage.setItem(STORAGE_KEY, k)
    } catch (err: unknown) {
      setAuthed(false)
      setError(err instanceof Error ? err.message : 'Could not load.')
    }
  }, [])

  const loadProject = useCallback(async (id: string, k: string) => {
    setProgress(null); setHeld([]); setReport(null); setMsg('')
    if (!id) return
    try {
      const [p, q] = await Promise.all([
        fetch(`/api/admin/progress?project=${id}`, { headers: { 'x-admin-key': k } }).then(r => r.json()),
        fetch(`/api/admin/adjudication?project=${id}`, { headers: { 'x-admin-key': k } }).then(r => r.json()),
      ])
      if (p.ok) setProgress({ total: p.total, done: p.done })
      if (q.ok) setHeld(q.items ?? [])
      else setError(q.message ?? '')
    } catch {
      setError('Unable to reach the server.')
    }
  }, [])

  useEffect(() => {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved) loadProjects(saved)
  }, [loadProjects])

  useEffect(() => {
    if (authed && selected) loadProject(selected, key)
  }, [authed, selected, key, loadProject])

  const resolve = async (item: Held) => {
    const anns = item.annotations ?? []
    const final: Record<string, unknown> = {}
    for (const f of answeredFields(anns)) {
      const chosen = picked[`${item.idx}:${f}`]
      if (chosen !== undefined) final[f] = chosen === '__null__' ? null : chosen
    }
    if (!Object.keys(final).length) {
      setMsg('Choose the answer that stands for at least one field.')
      return
    }
    setBusy(true); setMsg('')
    try {
      const res = await fetch('/api/admin/adjudicate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-admin-key': key },
        body: JSON.stringify({
          project_id: selected, idx: item.idx, final_label: final,
          note: note[item.idx] || null, decided_by: decidedBy.trim() || null,
        }),
      })
      const data = await res.json()
      if (!data.ok) throw new Error(data.message ?? 'Could not record the decision.')
      setMsg(data.message ?? 'Recorded.')
      await loadProject(selected, key)
    } catch (err: unknown) {
      setMsg(err instanceof Error ? err.message : 'Could not record the decision.')
    } finally {
      setBusy(false)
    }
  }

  const deliver = async () => {
    setBusy(true); setMsg('')
    try {
      const res = await fetch('/api/admin/advance', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-admin-key': key },
        body: JSON.stringify({ submission_id: selected, stage: 'delivered' }),
      })
      const data = await res.json()
      setMsg(data.message ?? (data.ok ? 'Delivered.' : 'Could not deliver.'))
      if (data.ok) {
        setSubs(prev => prev.map(s => (s.id === selected ? { ...s, stage: 'delivered' } : s)))
        loadReport()
      }
    } catch {
      setMsg('Unable to reach the server.')
    } finally {
      setBusy(false)
    }
  }

  const loadReport = async () => {
    setBusy(true)
    try {
      const res = await fetch(`/api/admin/report?project=${selected}`, { headers: { 'x-admin-key': key } })
      const data = await res.json()
      if (data.ok) setReport(data.report)
      else setMsg(data.message ?? 'Could not build the report.')
    } finally {
      setBusy(false)
    }
  }

  if (!authed) {
    return (
      <main className="run-shell">
        <div className="run-gate">
          <h1 className="run-h1">Run a project</h1>
          <p className="run-lead">Paste your operator key. Every decision you record is kept under your name.</p>
          <input
            className="run-input"
            type="password"
            value={keyInput}
            placeholder="Operator key"
            onChange={e => setKeyInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') loadProjects(keyInput.trim()) }}
          />
          <button className="run-btn" onClick={() => loadProjects(keyInput.trim())}>Open</button>
          {error && <p className="run-error">{error}</p>}
        </div>
      </main>
    )
  }

  const pct = progress && progress.total ? Math.round((progress.done / progress.total) * 100) : 0
  const clinical = report?.clinical ?? null
  const acc = report?.accuracy ?? null

  return (
    <main className="run-shell">
      <div className="run-head">
        <h1 className="run-h1">Run a project</h1>
        <Link className="run-link" href="/admin">Setup and projects →</Link>
      </div>

      <select className="run-select" value={selected} onChange={e => setSelected(e.target.value)}>
        <option value="">Choose a project…</option>
        {subs.map(s => (
          <option key={s.id} value={s.id}>
            {s.company || s.name || s.id.slice(0, 8)} — {s.stage ?? 'submitted'}
          </option>
        ))}
      </select>

      {project && (
        <>
          {/* 1 — where it stands */}
          <section className="run-card">
            <p className="run-eyebrow">Progress</p>
            <p className="run-big">
              {progress ? `${progress.done} of ${progress.total} cases finished` : 'Loading…'}
            </p>
            <div className="run-bar"><span style={{ width: `${pct}%` }} /></div>
            <p className="run-meta">
              Stage: {project.stage ?? 'submitted'}
              {held.length > 0 && ` · ${held.length} case${held.length > 1 ? 's' : ''} waiting on you`}
            </p>
          </section>

          {/* 2 — the cases clinicians could not agree on */}
          <section className="run-card">
            <p className="run-eyebrow">Needs you</p>
            {held.length === 0 ? (
              <p className="run-quiet">
                Nothing held. Cases where clinicians disagree appear here, and are kept out of the
                results until you decide.
              </p>
            ) : (
              held.map(item => {
                const anns = item.annotations ?? []
                const fields = answeredFields(anns)
                const caseId = (item.content?.case_id as string) ?? `Item ${item.idx}`
                return (
                  <div key={item.idx} className="run-held" >
                    <p className="run-case">
                      {caseId}
                      <span className="run-tag">
                        {item.reviewers ?? anns.length} reviewers · agreement{' '}
                        {item.agreement === null || item.agreement === undefined ? '—' : `${Math.round(item.agreement * 100)}%`}
                      </span>
                    </p>

                    {item.content && (
                      <div className="run-context">
                        {Object.entries(item.content)
                          .filter(([k]) => !k.startsWith('_'))
                          .map(([k, v]) => (
                            <p key={k}><b>{k.replace(/_/g, ' ')}:</b> {show(v)}</p>
                          ))}
                      </div>
                    )}

                    <div className="run-grid">
                      {fields.map(f => {
                        const values = anns.map(a => show((a.label ?? {})[f]))
                        const differ = new Set(values).size > 1
                        return (
                          <div key={f} className={differ ? 'run-field run-differ' : 'run-field'}>
                            <p className="run-fname">{f.replace(/_/g, ' ')}{differ && <span className="run-split">split</span>}</p>
                            {anns.map((a, i) => {
                              const raw = (a.label ?? {})[f]
                              const val = typeof raw === 'string' ? raw : show(raw)
                              const id = `${item.idx}:${f}`
                              return (
                                <label key={i} className="run-choice">
                                  {differ && (
                                    <input
                                      type="radio"
                                      name={id}
                                      checked={picked[id] === val}
                                      onChange={() => setPicked(p => ({ ...p, [id]: val }))}
                                    />
                                  )}
                                  <span>
                                    <em>Reviewer {i + 1}</em> {show(raw)}
                                  </span>
                                </label>
                              )
                            })}
                          </div>
                        )
                      })}
                    </div>

                    <textarea
                      className="run-note"
                      rows={3}
                      placeholder="Why this answer stands — this is kept with the case."
                      value={note[item.idx] ?? ''}
                      onChange={e => setNote(n => ({ ...n, [item.idx]: e.target.value }))}
                    />
                    <input
                      className="run-input run-note"
                      placeholder="Clinical decision by — name and credentials"
                      value={decidedBy}
                      onChange={e => setDecidedBy(e.target.value)}
                    />
                    <p className="run-quiet">
                      The judgement is theirs; your key records who entered it. Neither name
                      reaches the client.
                    </p>
                    <button className="run-btn" disabled={busy} onClick={() => resolve(item)}>
                      Record the decision
                    </button>
                  </div>
                )
              })
            )}
          </section>

          {/* 3 — deliver */}
          <section className="run-card">
            <p className="run-eyebrow">Deliver</p>
            <p className="run-quiet">
              Nothing is released until you say so. Unfinished or unresolved work is refused, with the
              reason.
            </p>
            <button className="run-btn" disabled={busy || project.stage === 'delivered'} onClick={deliver}>
              {project.stage === 'delivered' ? 'Delivered' : 'Mark delivered'}
            </button>
            <button className="run-btn run-ghost" disabled={busy} onClick={loadReport}>
              {report ? 'Refresh the report' : 'Build the report'}
            </button>
            {msg && <p className="run-msg">{msg}</p>}
          </section>

          {/* 4 — what the client receives */}
          {report && (
            <section className="run-card">
              <p className="run-eyebrow">What the client receives</p>
              {acc && (
                <p className="run-big">
                  {acc.basis === 'verdict' ? 'Pass rate' : 'Accuracy'}:{' '}
                  {acc.value === null || acc.value === undefined ? '—' : `${Math.round(acc.value * 100)}%`}
                  <span className="run-meta"> ({acc.correct} of {acc.assessable} assessable)</span>
                </p>
              )}
              {clinical?.severity?.distribution && (
                <p className="run-line"><b>Severity:</b>{' '}
                  {Object.entries(clinical.severity.distribution).map(([k, v]) => `${v} ${k}`).join(' · ')}
                </p>
              )}
              {clinical?.taxonomy?.distribution && (
                <p className="run-line"><b>Failure types:</b>{' '}
                  {Object.entries(clinical.taxonomy.distribution).map(([k, v]) => `${k} ${v}`).join(' · ')}
                </p>
              )}
              {clinical?.triage && (
                <p className="run-line"><b>Urgency:</b>{' '}
                  under-triage {clinical.triage.under_triage?.count ?? 0} ·{' '}
                  over-triage {clinical.triage.over_triage?.count ?? 0} ·{' '}
                  missed emergencies {clinical.triage.missed_emergency?.count ?? 0}
                </p>
              )}
              {report.second_reading && (
                <p className="run-line"><b>Second reading:</b>{' '}
                  {report.second_reading.approved} approved ·{' '}
                  {report.second_reading.sent_back_at_least_once} sent back
                </p>
              )}
              {report.qa && (
                <p className="run-line"><b>Reviewer agreement:</b>{' '}
                  {Math.round((report.qa.mean_agreement ?? 0) * 100)}% across {report.qa.reviewed_items} cases ·{' '}
                  {report.qa.disagreements} needed a decision
                </p>
              )}
              {Array.isArray(report.caveats) && report.caveats.length > 0 && (
                <ul className="run-caveats">
                  {report.caveats.map((c: string, i: number) => <li key={i}>{c}</li>)}
                </ul>
              )}
            </section>
          )}
        </>
      )}
    </main>
  )
}
