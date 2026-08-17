'use client'
import { useEffect, useState, useCallback } from 'react'
import NavBar from '../components/NavBar'
import FooterSection from '../components/sections/FooterSection'
import Toast from '../components/ui/Toast'
import { parseDataset } from '../lib/dataset'

const dropzone: React.CSSProperties = {
  display: 'block', border: '1px dashed var(--hairline-strong)', borderRadius: 12,
  padding: '26px 20px', textAlign: 'center', cursor: 'pointer',
}

const STAGES = [
  { key: 'submitted',  label: 'Submitted',     desc: 'We have your project and are reviewing it.' },
  { key: 'scoping',    label: 'Scoping call',  desc: 'We align on your data, accuracy target, and timeline.' },
  { key: 'agreement',  label: 'Agreement',     desc: 'NDA and work agreement signed.' },
  { key: 'pilot',      label: 'Pilot sample',  desc: 'Specialists label a small batch for your review.' },
  { key: 'production', label: 'Production',    desc: 'The full batch is labeled, with quality control on every task.' },
  { key: 'delivered',  label: 'Delivered',     desc: 'Your labeled data is handed back, ready for your models.' },
]

type Project = {
  id: string
  company: string | null
  description: string | null
  task_type: string | null
  stage: string
  stage_note: string | null
  created_at: string | null
  total: number
  done: number
}

const CheckMark = (
  <svg width="11" height="9" viewBox="0 0 11 9" fill="none" aria-hidden>
    <polyline points="1 5 4 8 10 1" stroke="var(--navy)" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
)

function DataUpload({ project, token, onUploaded }: { project: Project; token: string; onUploaded: () => void }) {
  const [rows, setRows] = useState<Record<string, string>[] | null>(null)
  const [columns, setColumns] = useState<string[]>([])
  const [manifestName, setManifestName] = useState('')
  const [images, setImages] = useState<Record<string, File>>({})
  const [err, setErr] = useState('')
  const [uploading, setUploading] = useState(false)
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null)
  const [msg, setMsg] = useState('')

  const fname = (r: Record<string, string>) => r.filename || r.image || r.file || ''
  const imageCount = Object.keys(images).length
  const matched = rows ? rows.filter(r => images[fname(r)]).length : 0
  // If the manifest names files but no images are attached yet, it's an image task
  // waiting for its images.
  const looksLikeImages = columns.some(c => ['filename', 'image', 'file'].includes(c))

  const addImages = (list: FileList) => {
    const map: Record<string, File> = { ...images }
    Array.from(list).forEach(f => { if (f.type.startsWith('image/')) map[f.name] = f })
    setImages(map)
  }

  const onFiles = async (list: FileList) => {
    setErr(''); setMsg('')
    const arr = Array.from(list)
    const manifest = arr.find(f => /\.(csv|json)$/i.test(f.name) || f.type === 'text/csv' || f.type === 'application/json')
    const imgs = arr.filter(f => f.type.startsWith('image/'))
    if (imgs.length) {
      const map: Record<string, File> = { ...images }
      imgs.forEach(f => { map[f.name] = f })
      setImages(map)
    }
    if (manifest) {
      try {
        const text = await manifest.text()
        const { items, columns } = parseDataset(manifest.name, text)
        if (!items.length) throw new Error('No rows found in that file.')
        setRows(items as Record<string, string>[]); setColumns(columns); setManifestName(manifest.name)
      } catch (e: unknown) {
        setErr(e instanceof Error ? e.message : 'Could not read that file.')
      }
    } else if (!imgs.length) {
      setErr('Drop a CSV or JSON file (and, for image tasks, the image files too).')
    }
  }

  const reset = () => { setRows(null); setColumns([]); setManifestName(''); setImages({}); setProgress(null) }

  const upload = async () => {
    if (!rows) return
    setUploading(true); setErr(''); setMsg('')
    try {
      if (imageCount > 0) {
        // Image task: send one row at a time, each image matched to its row by filename.
        setProgress({ done: 0, total: rows.length })
        for (let i = 0; i < rows.length; i++) {
          const r = rows[i]; const f = images[fname(r)]
          if (!f) throw new Error(`No image file matching row ${i + 1} ("${fname(r)}"). Add it and re-upload.`)
          const fd = new FormData()
          fd.append('token', token); fd.append('project_id', project.id); fd.append('idx', String(i))
          fd.append('prediction', r.prediction ?? ''); fd.append('study_id', r.study_id ?? '')
          fd.append('file', f)
          const res = await fetch('/api/portal/upload-image', { method: 'POST', body: fd })
          const data = await res.json()
          if (!data.ok) throw new Error(data.message ?? `Failed at row ${i + 1}`)
          setProgress({ done: i + 1, total: rows.length })
        }
        setMsg(`Uploaded ${rows.length} cases.`)
      } else {
        // Text task: one bulk request, each row becomes a task.
        const res = await fetch('/api/portal/items', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token, project_id: project.id, items: rows }),
        })
        const data = await res.json()
        if (!data.ok) throw new Error(data.message ?? 'Upload failed.')
        setMsg(data.message)
      }
      reset(); onUploaded()
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Upload failed.')
    } finally {
      setUploading(false); setProgress(null)
    }
  }

  const busy = uploading || !!progress
  const label = progress ? `Uploading ${progress.done}/${progress.total}…`
    : busy ? 'Uploading…'
    : imageCount > 0 ? `Upload ${rows?.length ?? 0} cases`
    : `Upload ${(rows?.length ?? 0).toLocaleString()} tasks`

  return (
    <div style={{ marginTop: 28 }}>
      <span className="micro" style={{ display: 'block', marginBottom: 10 }}>Your data</span>
      {project.total > 0 ? (
        <p style={{ fontSize: 15, color: 'var(--ink)', marginBottom: 14 }}>
          <strong>{project.total.toLocaleString()} tasks</strong> uploaded · {project.done.toLocaleString()} labeled
        </p>
      ) : (
        <p style={{ fontSize: 14.5, color: 'var(--ink)', lineHeight: 1.6, marginBottom: 14 }}>
          Drop your dataset, each row becomes one labeling task. For image tasks (X-rays, scans),
          drop the image files alongside your CSV and we&apos;ll match them by name.
        </p>
      )}

      {!rows ? (
        <label style={dropzone}
          onDragOver={e => e.preventDefault()}
          onDrop={e => { e.preventDefault(); if (e.dataTransfer.files?.length) onFiles(e.dataTransfer.files) }}
        >
          <input type="file" accept=".csv,.json,text/csv,application/json,image/*" multiple style={{ display: 'none' }}
            onChange={e => { if (e.target.files?.length) onFiles(e.target.files); e.target.value = '' }} />
          <span style={{ color: 'var(--ink)', fontWeight: 500, fontSize: 15 }}>Drop your CSV or JSON, plus image files if it&apos;s an image task</span>
          <span style={{ display: 'block', marginTop: 6, fontSize: 13, color: 'var(--slate)' }}>or click to browse · each row becomes one task</span>
        </label>
      ) : (
        <div style={{ border: '1px solid var(--hairline-strong)', borderRadius: 12, padding: 18 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 15, fontWeight: 600 }}>{manifestName}</span>
            <span className="micro">
              {rows.length.toLocaleString()} rows{imageCount > 0 ? ` · ${matched}/${rows.length} matched to images` : ''}
            </span>
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 12 }}>
            {columns.map(c => (
              <span key={c} className="micro" style={{ border: '1px solid var(--hairline-strong)', borderRadius: 999, padding: '4px 10px' }}>{c}</span>
            ))}
          </div>
          {(imageCount > 0 || looksLikeImages) && (
            <label style={{ ...dropzone, marginTop: 14 }}>
              <input type="file" accept="image/*" multiple style={{ display: 'none' }}
                onChange={e => { if (e.target.files) addImages(e.target.files); e.target.value = '' }} />
              <span style={{ color: 'var(--ink)', fontWeight: 500, fontSize: 15 }}>
                {imageCount > 0 ? `${imageCount} image files selected, add more if needed` : 'Add the image files named in your CSV'}
              </span>
              <span style={{ display: 'block', marginTop: 6, fontSize: 13, color: 'var(--slate)' }}>each image is stored privately, de-identified</span>
            </label>
          )}
          <div style={{ display: 'flex', gap: 14, marginTop: 16, alignItems: 'center', flexWrap: 'wrap' }}>
            <button className="sf-submit" onClick={upload} disabled={busy || (imageCount > 0 && matched === 0)} style={{ padding: '11px 20px', fontSize: 14 }}>
              {label}
            </button>
            <button onClick={reset} className="micro" style={{ background: 'none', border: 'none', cursor: 'pointer' }}>Start over</button>
          </div>
        </div>
      )}
      {err && <p style={{ color: '#dc2626', fontSize: 13, marginTop: 10 }}>{err}</p>}
      {msg && <p className="micro" style={{ marginTop: 12 }}>{msg}</p>}
    </div>
  )
}

type ResultItem = { idx: number; content: Record<string, unknown>; label: Record<string, unknown> | null; labeled_at: string | null }

function toCSV(items: ResultItem[]): string {
  if (!items.length) return ''
  const ck = new Set<string>(), lk = new Set<string>()
  for (const it of items) {
    Object.keys(it.content ?? {}).forEach(k => ck.add(k))
    Object.keys(it.label ?? {}).forEach(k => { if (k !== '_result') lk.add(k) })
  }
  const cols = Array.from(ck), labs = Array.from(lk)
  const esc = (v: unknown) => {
    const s = v == null ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const headers = ['idx', ...cols, ...labs.map(k => `label_${k}`), 'labeled_at']
  const lines = [headers.join(',')]
  for (const it of items) {
    lines.push([
      it.idx,
      ...cols.map(k => esc(it.content?.[k])),
      ...labs.map(k => esc(it.label?.[k])),
      esc(it.labeled_at),
    ].join(','))
  }
  return lines.join('\n')
}

type Report = {
  qa?: { reviewers: number | null; mean_agreement: number | null; reviewed_items: number; disagreements: number } | null
  accuracy: { correct: number; assessable: number; value: number | null }
  per_class: Record<string, { support: number; precision: number | null; recall: number | null }>
  confusion_matrix: { labels: string[]; matrix: number[][] }
  critical_misses: { case_id: string | null; idx: number; prompt?: string | null; model_prediction: string; correct_label: string | null; finding?: string | null; rationale?: string | null }[]
}
const pct = (v: number | null | undefined) => (v == null ? 'n/a' : `${Math.round(v * 100)}%`)

function DeliveredResults({ project, token }: { project: Project; token: string }) {
  const [items, setItems] = useState<ResultItem[] | null>(null)
  const [report, setReport] = useState<Report | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    fetch(`/api/portal/results?token=${encodeURIComponent(token)}&project=${encodeURIComponent(project.id)}`)
      .then(r => r.json())
      .then(d => {
        if (!d.ok) throw new Error(d.message ?? 'Could not load results.')
        setItems(d.items ?? [])
        setReport(d.report ?? null)
      })
      .catch(e => setErr(e instanceof Error ? e.message : 'Could not load results.'))
  }, [project.id, token])

  const download = (fmt: 'json' | 'csv') => {
    if (!items) return
    const base = (project.company || 'results').replace(/\s+/g, '_')
    const blob = fmt === 'json'
      ? new Blob([JSON.stringify(items, null, 2)], { type: 'application/json' })
      : new Blob([toCSV(items)], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a'); a.href = url; a.download = `${base}_results.${fmt}`; a.click()
    URL.revokeObjectURL(url)
  }

  const btn: React.CSSProperties = { padding: '11px 18px', borderRadius: 10, fontSize: 14, fontWeight: 600, cursor: 'pointer', border: '1px solid #111', background: '#111', color: '#fff' }
  const ghost: React.CSSProperties = { ...btn, background: '#fff', color: '#111' }
  const cell: React.CSSProperties = { padding: '9px 12px', borderBottom: '1px solid #eee', fontSize: 14 }
  const num: React.CSSProperties = { ...cell, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }

  if (err) return <p style={{ color: '#dc2626', fontSize: 13, marginTop: 20 }}>{err}</p>
  if (!items) return <p style={{ fontSize: 15, color: 'var(--slate)', marginTop: 24 }}>Loading your results…</p>

  const acc = report?.accuracy
  const classes = report?.confusion_matrix.labels ?? []

  return (
    <div style={{ marginTop: 24, border: '1px solid #d9f0e0', background: '#f4fbf6', borderRadius: 14, padding: '22px 24px' }}>
      <span className="micro" style={{ display: 'block', marginBottom: 6, color: '#15803d' }}>Results ready</span>

      {report && acc && (
        <>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, flexWrap: 'wrap', margin: '4px 0 22px' }}>
            <span style={{ fontFamily: 'Geist, sans-serif', fontWeight: 300, fontSize: 46, lineHeight: 1, letterSpacing: '-0.02em', color: '#111' }}>{pct(acc.value)}</span>
            <span style={{ fontSize: 14, color: '#3a4655', maxWidth: 320, lineHeight: 1.5 }}>of your model&rsquo;s outputs agreed with the clinician, on {acc.assessable} reviewed cases.</span>
          </div>

          {report.qa && report.qa.mean_agreement != null && (
            <p style={{ fontSize: 13.5, color: '#3a4655', marginBottom: 22 }}>
              Each case reviewed by <strong>{report.qa.reviewers} clinicians</strong>, with{' '}
              <strong>{pct(report.qa.mean_agreement)}</strong> inter-reviewer agreement
              {report.qa.disagreements > 0 && <> ({report.qa.disagreements} adjudicated)</>}.
            </p>
          )}

          <span className="micro" style={{ display: 'block', marginBottom: 8, color: '#3d5878' }}>Per-finding performance</span>
          <div style={{ overflowX: 'auto', background: '#fff', border: '1px solid #eee', borderRadius: 10, marginBottom: 22 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr>
                <th style={{ ...cell, textAlign: 'left', color: '#3d5878', fontWeight: 600 }}>Finding</th>
                <th style={{ ...num, color: '#3d5878', fontWeight: 600 }}>n</th>
                <th style={{ ...num, color: '#3d5878', fontWeight: 600 }}>Precision</th>
                <th style={{ ...num, color: '#3d5878', fontWeight: 600 }}>Recall</th>
              </tr></thead>
              <tbody>
                {classes.map(c => { const m = report.per_class[c]; return (
                  <tr key={c}>
                    <td style={{ ...cell, textAlign: 'left' }}>{c}</td>
                    <td style={num}>{m?.support ?? 0}</td>
                    <td style={num}>{pct(m?.precision)}</td>
                    <td style={num}>{pct(m?.recall)}</td>
                  </tr>) })}
              </tbody>
            </table>
          </div>

          {report.critical_misses.length > 0 && (
            <div style={{ marginBottom: 22 }}>
              <span className="micro" style={{ display: 'block', marginBottom: 8, color: '#b91c1c' }}>Critical misses ({report.critical_misses.length}), findings the clinician caught, the model didn&rsquo;t</span>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {report.critical_misses.map((c, i) => (
                  <div key={i} style={{ background: '#fff', border: '1px solid #f0d0d0', borderLeft: '3px solid #b91c1c', borderRadius: 8, padding: '12px 14px', fontSize: 14 }}>
                    <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', justifyContent: 'space-between', flexWrap: 'wrap' }}>
                      <span style={{ fontFamily: 'monospace', fontSize: 12, color: '#666' }}>{c.case_id || `#${c.idx}`}</span>
                      {c.finding && <span style={{ fontSize: 12, fontWeight: 600, color: '#b91c1c' }}>{c.finding}</span>}
                    </div>
                    {c.prompt && <p style={{ margin: '7px 0 9px', color: '#111', lineHeight: 1.5 }}>&ldquo;{c.prompt}&rdquo;</p>}
                    <div style={{ color: '#3a4655' }}>Model said <strong style={{ color: '#b91c1c' }}>{c.model_prediction}</strong>{' → clinician read '}<strong style={{ color: '#111' }}>{c.correct_label}</strong></div>
                    {c.rationale && <p style={{ margin: '8px 0 0', fontSize: 13, color: '#3a4655', fontStyle: 'italic', lineHeight: 1.5 }}>{c.rationale}</p>}
                  </div>
                ))}
              </div>
            </div>
          )}

          <p style={{ fontSize: 12.5, color: '#3a4655', lineHeight: 1.6, marginBottom: 18 }}>
            Measured on the {acc.assessable} reviewed cases, this describes your model&rsquo;s agreement with the clinician on this sample, not its accuracy across a full population.
          </p>
        </>
      )}

      <p style={{ fontSize: 14, color: '#111', marginBottom: 12 }}>Download the full reviewed dataset:</p>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <button style={btn} onClick={() => download('json')}>Download JSON</button>
        <button style={ghost} onClick={() => download('csv')}>Download CSV</button>
      </div>
    </div>
  )
}

function Tracker({ project, token, onUploaded }: { project: Project; token: string; onUploaded: () => void }) {
  const current = Math.max(0, STAGES.findIndex(s => s.key === project.stage))
  return (
    <div className="pt-project">
      <h2 className="pt-company">{project.company || 'Your project'}</h2>
      {project.task_type && <p className="pt-services">{project.task_type}</p>}

      <DataUpload project={project} token={token} onUploaded={onUploaded} />

      <div className="pt-status" style={{ marginTop: 28 }}>
        <span className="micro" style={{ display: 'block', marginBottom: 8 }}>Current status</span>
        <div className="pt-status-stage">{STAGES[current].label}</div>
        <p className="pt-status-desc">{project.stage_note || STAGES[current].desc}</p>
      </div>

      {project.stage === 'delivered' && <DeliveredResults project={project} token={token} />}

      {project.description && (
        <div className="pt-brief">
          <span className="micro" style={{ display: 'block', marginBottom: 8 }}>Your brief</span>
          <p className="pt-desc">{project.description}</p>
        </div>
      )}

      <span className="micro" style={{ display: 'block', margin: '28px 0 18px' }}>Progress</span>
      <div className="pt-steps">
        {STAGES.map((s, i) => {
          const state = i < current ? 'done' : i === current ? 'current' : 'upcoming'
          return (
            <div className={`pt-step ${state}`} key={s.key}>
              <div className="pt-rail">
                <span className="pt-dot">{state === 'done' && CheckMark}</span>
                {i < STAGES.length - 1 && <span className="pt-bar" />}
              </div>
              <div className="pt-body">
                <div className="pt-label">{s.label}</div>
                <div className="pt-sub">{s.desc}</div>
                {state === 'current' && project.stage_note && (
                  <div className="pt-note">{project.stage_note}</div>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default function PortalPage() {
  const [token, setToken] = useState<string | null>(null)
  const [checked, setChecked] = useState(false)        // finished reading the URL
  const [loading, setLoading] = useState(false)
  const [projects, setProjects] = useState<Project[] | null>(null)
  const [fetchError, setFetchError] = useState('')

  const [email, setEmail] = useState('')
  const [requesting, setRequesting] = useState(false)
  const [requested, setRequested] = useState(false)
  const [reqError, setReqError] = useState('')

  const loadProjects = useCallback((t: string) => {
    setLoading(true)
    fetch(`/api/portal/projects?token=${encodeURIComponent(t)}`)
      .then(r => r.json())
      .then(data => {
        if (!data.ok) throw new Error(data.message ?? 'This link is no longer valid.')
        setProjects(data.projects ?? [])
      })
      .catch(err => setFetchError(err instanceof Error ? err.message : 'Could not load your project.'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get('token')
    setToken(t)
    setChecked(true)
    if (t) loadProjects(t)
  }, [loadProjects])

  const requestLink = async (e: React.FormEvent) => {
    e.preventDefault()
    if (requesting) return
    setRequesting(true)
    setReqError('')
    try {
      const res = await fetch('/api/portal/request', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      })
      const data = await res.json()
      if (!data.ok) throw new Error(data.message ?? '')
      setRequested(true)
    } catch (err: unknown) {
      setReqError(err instanceof Error ? err.message : 'Something went wrong. Please try again.')
    } finally {
      setRequesting(false)
    }
  }

  const showProjects = token && projects && projects.length > 0
  const showEmailForm = checked && (!token || !!fetchError)

  return (
    <>
      <div className="submit-light">
        <NavBar onLight />

        <section className="submit-section">
          <div className="wrap" style={{ maxWidth: 760 }}>
            <span className="micro" style={{ display: 'block', color: 'var(--ink)', marginBottom: 28 }}>Customer portal</span>
            <h1 style={{
              fontFamily: 'Geist, sans-serif',
              fontWeight: 300,
              fontSize: 'clamp(38px, 4.6vw, 60px)',
              lineHeight: 1.06,
              letterSpacing: '0.01em',
              marginBottom: 24,
            }}>
              Your project.
            </h1>

            {/* Loading  */}
            {token && loading && (
              <p style={{ fontSize: 17, color: 'var(--ink)', lineHeight: 1.7 }}>Loading your project…</p>
            )}

            {/* Projects  */}
            {showProjects && (
              <div style={{ marginTop: 44 }}>
                {projects!.map(p => <Tracker key={p.id} project={p} token={token!} onUploaded={() => loadProjects(token!)} />)}
                <p style={{ fontSize: 14.5, color: 'var(--ink)', lineHeight: 1.6, marginTop: 8 }}>
                  Questions about your project? Just reply to any email from us.
                </p>
              </div>
            )}

            {/* Token but no projects  */}
            {token && !loading && projects && projects.length === 0 && (
              <p style={{ fontSize: 17, color: 'var(--ink)', lineHeight: 1.7, marginTop: 20 }}>
                We could not find a project tied to this link. If you have submitted one, request a fresh link below.
              </p>
            )}

            {/* Email request form (no token, or expired/invalid token)  */}
            {showEmailForm && !showProjects && (
              <div style={{ maxWidth: 460, marginTop: token ? 40 : 8 }}>
                {fetchError && (
                  <p style={{ fontSize: 16, color: 'var(--ink)', lineHeight: 1.7, marginBottom: 28 }}>{fetchError}</p>
                )}
                {!fetchError && (
                  <p style={{ fontSize: 18, color: 'var(--ink)', lineHeight: 1.75, marginBottom: 36 }}>
                    Enter the email you submitted your project with. We will send you a secure sign-in link.
                  </p>
                )}

                {requested ? (
                  <div className="sf-success">
                    <svg className="sf-success-icon" width="22" height="22" viewBox="0 0 20 20" fill="none">
                      <circle cx="10" cy="10" r="9" stroke="currentColor" strokeWidth="1.3" />
                      <polyline points="6 10.5 9 13.5 14 7" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                    <div>
                      <h4>Check your inbox.</h4>
                      <p>If that email has a project with us, a sign-in link is on its way. It is valid for 14 days.</p>
                    </div>
                  </div>
                ) : (
                  <form onSubmit={requestLink} className="sf-form">
                    <div className="sf-field">
                      <label className="sf-label" htmlFor="email">Work email</label>
                      <input id="email" className="sf-input" type="email" value={email} onChange={e => setEmail(e.target.value)} required disabled={requesting} autoComplete="email" />
                    </div>
                    <button className="sf-submit" type="submit" disabled={requesting}>
                      {requesting ? 'Sending…' : 'Email me a sign-in link'}
                    </button>
                  </form>
                )}

                {reqError && <Toast message={reqError} onDismiss={() => setReqError('')} />}
              </div>
            )}
          </div>
        </section>

        <FooterSection />
      </div>
    </>
  )
}
