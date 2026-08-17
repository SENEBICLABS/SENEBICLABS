'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import NavBar from '../components/NavBar'

// ─── Types ────────────────────────────────────────────────────────────────────

interface ModelStatus {
  is_ready: boolean; n_healthy_cells: number | null; n_donors: number | null
  n_cell_types: number | null; n_pathways: number | null; message: string
}
interface CellTypeDeviation {
  cell_type: string; z_score: number; direction: string; magnitude: string; interpretation: string
  compartment: string; estimated_fraction: number; healthy_mean_fraction: number; healthy_std_fraction: number
}
interface GeneDeviation {
  gene: string; z_score: number; direction: string; magnitude: string
  sample_value: number; healthy_mean: number; healthy_std: number; healthy_p5: number; healthy_p95: number
}
interface PathwayDeviation {
  pathway: string; direction: string; n_active_genes: number; n_total_genes: number
  deviation_from_baseline: number | null; interpretation: string
  category: string; avg_expression: number; trigger_baseline_expr: number | null; active_genes: string[]
}
interface SpatialGeneContext {
  gene: string; sample_value: number; direction: string; z_score: number
  bronchus_baseline: number; parenchyma_baseline: number; log_fc: number
  tissue_enriched_in: string; clinical_note: string
}
interface SpatialContext {
  n_genes_with_spatial_data: number
  airway_signal_genes: string[]
  alveolar_signal_genes: string[]
  gene_spatial_contexts: SpatialGeneContext[]
  dominant_tissue_compartment: string
  tissue_localisation_summary: string
}
interface DeviationReport {
  sample_id: string; overall_deviation_score: number; summary: string
  cell_type_deviations: CellTypeDeviation[]; gene_deviations: GeneDeviation[]
  pathway_deviations: PathwayDeviation[]; healthy_reference_cells: number
  healthy_reference_donors: number; safety_disclaimer: string
  spatial_context: SpatialContext | null
  data_quality_warnings: string[]
}
interface BiologicalInterpretation {
  stage: string | null; overall_confidence: string | null
  final_interpretation: string; biological_question: string
  full_reasoning: string; anomalous_findings: string[]
}
interface GeneRow { gene: string; value: string }
interface ColumnOption { name: string; rows: GeneRow[] }
type NormMode = 'log1p_cp10k' | 'raw_counts' | 'cp10k'
type UploadMode = 'idle' | 'uploading_h5ad' | 'analysing' | 'interpreting'

// ─── Parsing utilities ────────────────────────────────────────────────────────

interface ParseResult {
  rows: GeneRow[]
  columnOptions?: ColumnOption[]   // multi-sample: let user pick
  warnings: string[]
  format: string
}

const ENSEMBL_RE = /^ENSG\d{11}$/i

function detectAndParse(text: string): ParseResult {
  const warnings: string[] = []
  const rawLines = text.trim().split(/\r?\n/).filter(l => l.trim())
  if (!rawLines.length) return { rows: [], warnings: ['Empty input.'], format: 'unknown' }

  // Detect separator: tab > comma > semicolon > space
  const firstLine = rawLines[0]
  const sep = firstLine.includes('\t') ? '\t'
    : firstLine.includes(',') ? ','
    : firstLine.includes(';') ? ';'
    : ' '

  const allCells = rawLines.map(l =>
    l.split(sep).map(c => c.trim().replace(/^["']|["']$/g, ''))
  )
  const numCols = Math.max(...allCells.map(r => r.length))

  // Detect header row: first row has a non-numeric value in the data position
  const firstRow = allCells[0]
  const hasHeader = numCols >= 2 && isNaN(parseFloat(firstRow[1]))
  const dataRows = hasHeader ? allCells.slice(1) : allCells

  // Warn if Ensembl IDs detected
  const firstGene = dataRows[0]?.[0] ?? ''
  if (ENSEMBL_RE.test(firstGene)) {
    warnings.push(
      'Ensembl IDs detected (e.g. ENSG…). The model uses HGNC gene symbols (e.g. COL1A1). ' +
      'Convert with biomaRt (R) or mygene.info before uploading for best coverage.'
    )
  }

  // ── Two-column: gene + single value ──────────────────────────────────────
  if (numCols <= 2) {
    const rows = dataRows
      .map(r => ({ gene: r[0]?.toUpperCase().trim() ?? '', value: r[1]?.trim() ?? '' }))
      .filter(r => r.gene && !isNaN(parseFloat(r.value)))
    return { rows, warnings, format: 'two-column' }
  }

  // ── Multi-column: find expression columns ─────────────────────────────────
  // Common lab format: col 0 = gene name/ID, cols 1+ = sample(s)
  // Also handle transposed Scanpy/Seurat format with empty first header cell
  const headerRow = hasHeader ? firstRow : null
  const colNames = headerRow?.slice(1) ?? []

  // Find which columns are mostly numeric (expression data)
  const numericCols: number[] = []
  for (let ci = 1; ci < numCols; ci++) {
    const sampleCount = Math.min(dataRows.length, 10)
    const numericCount = dataRows.slice(0, sampleCount)
      .filter(r => !isNaN(parseFloat(r[ci] ?? ''))).length
    if (numericCount >= sampleCount * 0.7) numericCols.push(ci)
  }

  if (numericCols.length === 0) {
    // Fallback: first two columns
    const rows = dataRows
      .map(r => ({ gene: r[0]?.toUpperCase().trim() ?? '', value: r[1]?.trim() ?? '' }))
      .filter(r => r.gene && !isNaN(parseFloat(r.value)))
    warnings.push('Could not detect expression columns, using first two columns.')
    return { rows, warnings, format: 'fallback' }
  }

  if (numericCols.length === 1) {
    const ci = numericCols[0]
    const rows = dataRows
      .map(r => ({ gene: r[0]?.toUpperCase().trim() ?? '', value: r[ci]?.trim() ?? '' }))
      .filter(r => r.gene && !isNaN(parseFloat(r.value)))
    return { rows, warnings, format: 'single-expression-column' }
  }

  // Multiple samples, return options so user can pick
  const columnOptions: ColumnOption[] = numericCols.map((ci, idx) => ({
    name: colNames[ci - 1]?.trim() || `Sample ${idx + 1}`,
    rows: dataRows
      .map(r => ({ gene: r[0]?.toUpperCase().trim() ?? '', value: r[ci]?.trim() ?? '' }))
      .filter(r => r.gene && !isNaN(parseFloat(r.value))),
  }))
  return { rows: [], columnOptions, warnings, format: 'multi-sample' }
}

// ─── Normalization ────────────────────────────────────────────────────────────

function applyNorm(rows: GeneRow[], mode: NormMode): GeneRow[] {
  if (mode === 'log1p_cp10k') return rows
  const vals = rows.map(r => parseFloat(r.value))
  if (mode === 'raw_counts') {
    const total = vals.reduce((a, b) => a + b, 0)
    if (total === 0) return rows
    return rows.map((r, i) => ({ gene: r.gene, value: String(Math.log1p(vals[i] / total * 1e4)) }))
  }
  if (mode === 'cp10k') {
    return rows.map((r, i) => ({ gene: r.gene, value: String(Math.log1p(vals[i])) }))
  }
  return rows
}

// ─── Download report ──────────────────────────────────────────────────────────

function buildReportCSV(report: DeviationReport): string {
  const lines: string[] = [
    `# Senebiclabs Respiratory Intelligence Engine · Deviation Report`,
    `# Sample ID,${report.sample_id}`,
    `# Analysed at,${new Date().toISOString()}`,
    `# Overall deviation score,${(report.overall_deviation_score * 100).toFixed(1)}%`,
    `# Severity,${report.overall_deviation_score < 0.2 ? 'Mild' : report.overall_deviation_score < 0.4 ? 'Moderate' : 'Substantial'}`,
    `# Reference cells,${report.healthy_reference_cells}`,
    `# Reference donors,${report.healthy_reference_donors}`,
    `# ${report.safety_disclaimer}`,
    ``,
    `Type,Name,Z-score,Direction,Magnitude,Sample value,Healthy mean,Healthy std,P5,P95,Compartment,Category,Interpretation`,
    ...report.cell_type_deviations.map(d =>
      `cell_type,"${d.cell_type}",${d.z_score},${d.direction},${d.magnitude},` +
      `${(d.estimated_fraction * 100).toFixed(3)}%,${(d.healthy_mean_fraction * 100).toFixed(3)}%,` +
      `${(d.healthy_std_fraction * 100).toFixed(3)}%,,,${d.compartment},,"${d.interpretation}"`
    ),
    ...report.gene_deviations.map(g =>
      `gene,${g.gene},${g.z_score},${g.direction},${g.magnitude},` +
      `${g.sample_value},${g.healthy_mean},${g.healthy_std},${g.healthy_p5},${g.healthy_p95},,,`
    ),
    ...report.pathway_deviations.map(p =>
      `pathway,"${p.pathway}",,${p.direction},,${p.avg_expression},` +
      `${p.trigger_baseline_expr ?? ''},,,,${p.category},,"${p.interpretation}"`
    ),
  ]
  return lines.join('\n')
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function Spinner({ size = 18 }: { size?: number }) {
  return (
    <span style={{
      display: 'inline-block', width: size, height: size, borderRadius: '50%',
      border: '2px solid rgba(255,255,255,0.12)', borderTopColor: '#fff',
      animation: 'spin 0.75s linear infinite', verticalAlign: 'middle', flexShrink: 0,
    }} />
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function AnalysePage() {
  const [modelStatus, setModelStatus] = useState<ModelStatus | null>(null)
  const [statusLoading, setStatusLoading] = useState(true)

  useEffect(() => {
    fetch('/api/model-status').then(r => r.json()).then(setModelStatus)
      .catch(() => setModelStatus(null)).finally(() => setStatusLoading(false))
  }, [])

  // ── Input ──
  const [rows, setRows]                 = useState<GeneRow[]>([])
  const [columnOptions, setColumnOptions] = useState<ColumnOption[] | null>(null)
  const [parseWarnings, setParseWarnings] = useState<string[]>([])
  const [inputError, setInputError]     = useState<string | null>(null)
  const [dragOver, setDragOver]         = useState(false)
  const [normMode, setNormMode]         = useState<NormMode>('log1p_cp10k')
  const [sampleId, setSampleId]         = useState('')
  // Manual entry
  const [geneInput, setGeneInput]       = useState('')
  const [valueInput, setValueInput]     = useState('')
  const [manualErr, setManualErr]       = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const geneInputRef = useRef<HTMLInputElement>(null)

  // ── Run ──
  const [loading, setLoading]       = useState(false)
  const [step, setStep]             = useState<UploadMode>('idle')
  const [report, setReport]         = useState<DeviationReport | null>(null)
  const [interp, setInterp]         = useState<BiologicalInterpretation | null>(null)
  const [interpUnavailable, setInterpUnavailable] = useState(false)
  const [error, setError]           = useState<string | null>(null)
  const [showReasoning, setShowReasoning] = useState(false)

  // ── Parse text ────────────────────────────────────────────────────────────

  const processText = useCallback((text: string) => {
    const result = detectAndParse(text)
    setParseWarnings(result.warnings)
    if (result.columnOptions?.length) {
      setColumnOptions(result.columnOptions)
      setRows([])
      setInputError(null)
    } else if (result.rows.length < 2) {
      setInputError(
        'Could not parse data. Expected: gene name in first column, expression value in second. ' +
        'Accepted formats: CSV, TSV, Excel export. Minimum 2 genes.'
      )
    } else {
      setRows(result.rows)
      setColumnOptions(null)
      setInputError(null)
    }
  }, [])

  // ── h5ad / zip: upload directly to backend, skip browser-parse step ────

  const uploadH5ad = useCallback(async (file: File) => {
    setLoading(true); setReport(null); setInterp(null)
    setInterpUnavailable(false); setError(null); setShowReasoning(false)
    setStep('uploading_h5ad')
    try {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('sample_id', sampleId.trim() || file.name.replace(/\.(h5ad|zip)$/i, '') || `sample_${Date.now()}`)

      const aRes = await fetch('/api/analyse-upload', { method: 'POST', body: fd })
      if (!aRes.ok) {
        const err = await aRes.json().catch(() => ({}))
        throw new Error(err.detail ?? `Analysis failed (${aRes.status})`)
      }
      const r: DeviationReport = await aRes.json()
      setReport(r)
      setStep('interpreting')
      const iRes = await fetch('/api/interpret', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(r),
      })
      if (iRes.ok) setInterp(await iRes.json())
      else setInterpUnavailable(true)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setLoading(false); setStep('idle')
    }
  }, [sampleId])

  const handleFile = useCallback(async (file: File) => {
    const ext = file.name.split('.').pop()?.toLowerCase()
    if (ext === 'h5ad' || ext === 'zip') {
      // h5ad and CellRanger MEX zips cannot be parsed in the browser, send to backend
      await uploadH5ad(file)
      return
    }
    if (ext === 'xlsx' || ext === 'xls' || ext === 'xlsm') {
      try {
        const XLSX = await import('xlsx')
        const buf = await file.arrayBuffer()
        const wb = XLSX.read(buf, { type: 'array' })
        const ws = wb.Sheets[wb.SheetNames[0]]
        const csv = XLSX.utils.sheet_to_csv(ws)
        processText(csv)
      } catch {
        setInputError('Could not read Excel file. Try exporting as CSV from Excel first.')
      }
    } else {
      const text = await file.text()
      processText(text)
    }
  }, [processText, uploadH5ad])

  function handleDrop(e: React.DragEvent) {
    e.preventDefault(); setDragOver(false)
    const file = e.dataTransfer.files[0]
    if (file) handleFile(file)
  }
  function handleFileInput(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (file) handleFile(file)
  }
  function handlePaste(e: React.ClipboardEvent) {
    const text = e.clipboardData.getData('text')
    if (!text.trim()) return
    e.preventDefault()
    processText(text)
  }

  // ── Manual entry ──────────────────────────────────────────────────────────

  function addGeneManually() {
    const gene = geneInput.trim().toUpperCase()
    const val  = valueInput.trim()
    if (!gene) { setManualErr('Enter a gene symbol (e.g. COL1A1).'); return }
    if (!val || isNaN(parseFloat(val))) { setManualErr('Enter a numeric expression value.'); return }
    if (rows.find(r => r.gene === gene)) { setManualErr(`${gene} is already in the list.`); return }
    setRows(prev => [...prev, { gene, value: val }])
    setGeneInput(''); setValueInput(''); setManualErr(null)
    geneInputRef.current?.focus()
  }
  function onManualKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter') addGeneManually()
  }
  function removeRow(i: number) { setRows(prev => prev.filter((_, idx) => idx !== i)) }
  function clearAll() {
    setRows([]); setReport(null); setInterp(null); setError(null)
    setInputError(null); setParseWarnings([]); setColumnOptions(null)
    setSampleId(''); setNormMode('log1p_cp10k')
    setGeneInput(''); setValueInput(''); setManualErr(null)
  }

  // ── Run ──────────────────────────────────────────────────────────────────

  const modelReady = modelStatus?.is_ready ?? false
  const canRun = !loading && rows.length >= 10

  async function run() {
    const normalised = applyNorm(rows, normMode)
    const genes: Record<string, number> = {}
    for (const r of normalised) genes[r.gene] = parseFloat(r.value)
    setLoading(true); setReport(null); setInterp(null)
    setInterpUnavailable(false); setError(null); setShowReasoning(false)
    setStep('analysing')
    try {
      const id = sampleId.trim() || `sample_${Date.now()}`
      const aRes = await fetch('/api/analyse', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sample_id: id, gene_expression: genes }),
      })
      if (!aRes.ok) {
        const err = await aRes.json().catch(() => ({}))
        throw new Error(err.detail ?? `${aRes.status}`)
      }
      const r: DeviationReport = await aRes.json()
      setReport(r)
      setStep('interpreting')
      const iRes = await fetch('/api/interpret', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(r),
      })
      if (iRes.ok) setInterp(await iRes.json())
      else setInterpUnavailable(true)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setLoading(false); setStep('idle')
    }
  }

  // ── Download report ───────────────────────────────────────────────────────

  function downloadReport() {
    if (!report) return
    const csv = buildReportCSV(report)
    const blob = new Blob([csv], { type: 'text/csv' })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href = url; a.download = `${report.sample_id}_deviation_report.csv`
    a.click(); URL.revokeObjectURL(url)
  }

  // ── Sample profile ────────────────────────────────────────────────────────

  function loadSampleProfile() {
    setRows([
      // ── Fibroblast / ECM expansion (hallmark of IPF)
      { gene: 'COL1A1',  value: '4.21' }, { gene: 'COL1A2',  value: '3.85' },
      { gene: 'COL3A1',  value: '3.55' }, { gene: 'FN1',     value: '3.84' },
      { gene: 'POSTN',   value: '3.18' }, { gene: 'ACTA2',   value: '3.12' },
      { gene: 'VIM',     value: '2.95' }, { gene: 'TGFB1',   value: '1.92' },
      { gene: 'CTGF',    value: '2.14' }, { gene: 'MMP2',    value: '2.31' },
      { gene: 'TIMP1',   value: '2.18' }, { gene: 'LOXL2',   value: '1.87' },
      // ── AT2 surfactant depletion
      { gene: 'SFTPC',   value: '0.82' }, { gene: 'SFTPB',   value: '0.91' },
      { gene: 'SFTPA1',  value: '0.75' }, { gene: 'ABCA3',   value: '1.12' },
      { gene: 'NAPSA',   value: '0.93' }, { gene: 'LPCAT1',  value: '1.05' },
      // ── AT1 markers (depleted in IPF)
      { gene: 'AGER',    value: '0.61' }, { gene: 'PDPN',    value: '0.72' },
      { gene: 'CAV1',    value: '0.88' }, { gene: 'HOPX',    value: '0.65' },
      // ── SPP1 macrophages and MUC5B (elevated in IPF)
      { gene: 'SPP1',    value: '2.41' }, { gene: 'MUC5B',   value: '2.94' },
      { gene: 'CD68',    value: '1.45' }, { gene: 'MRC1',    value: '1.12' },
      { gene: 'MARCO',   value: '0.89' },
      // ── Airway epithelium
      { gene: 'SCGB1A1', value: '0.45' }, { gene: 'SCGB3A2', value: '0.52' },
      { gene: 'KRT5',    value: '0.41' }, { gene: 'TP63',    value: '0.29' },
      // ── Vascular endothelium
      { gene: 'PECAM1',  value: '0.95' }, { gene: 'CDH5',    value: '0.88' },
      { gene: 'VWF',     value: '0.72' }, { gene: 'VEGFA',   value: '0.89' },
      // ── Smooth muscle
      { gene: 'MYH11',   value: '0.45' }, { gene: 'TAGLN',   value: '0.89' },
      { gene: 'CNN1',    value: '0.52' },
      // ── Immune / inflammatory
      { gene: 'CD8A',    value: '0.32' }, { gene: 'CD3E',    value: '0.28' },
      { gene: 'IL6',     value: '0.45' }, { gene: 'CXCL8',   value: '0.61' },
      { gene: 'TNF',     value: '0.38' },
      // ── Housekeeping (reference anchors)
      { gene: 'GAPDH',   value: '2.10' }, { gene: 'ACTB',    value: '3.20' },
      { gene: 'B2M',     value: '2.80' },
    ])
    setSampleId('IPF-DEMO-001')
    setReport(null); setInterp(null); setInterpUnavailable(false)
    setError(null); setColumnOptions(null); setParseWarnings([])
    setNormMode('log1p_cp10k')
  }

  const hasData = rows.length > 0
  const hasPicker = !!columnOptions?.length

  // ─── Render ──────────────────────────────────────────────────────────────

  return (
    <>
      <style suppressHydrationWarning>{`
        @keyframes spin   { to { transform: rotate(360deg); } }
        @keyframes fadeUp { from { opacity:0; transform:translateY(10px); } to { opacity:1; transform:translateY(0); } }
        .appear { animation: fadeUp 0.4s cubic-bezier(0.16,1,0.3,1) forwards; }
        .analyse-bg { background: #080C16; min-height: 100vh; }

        .drop-zone {
          border: 1.5px dashed rgba(255,255,255,0.22);
          border-radius: 14px; padding: 40px 24px;
          text-align: center; transition: border-color 0.2s, background 0.2s; cursor: pointer;
        }
        .drop-zone:hover, .drop-zone.over { border-color: rgba(255,255,255,0.6); background: rgba(255,255,255,0.025); }

        .gene-table { width: 100%; border-collapse: collapse; }
        .gene-table th {
          font-family: 'Geist Mono', monospace; font-size: 12px; letter-spacing: 0.1em;
          text-transform: uppercase; color: rgba(255,255,255,0.9);
          text-align: left; padding: 0 8px 10px; border-bottom: 1px solid rgba(255,255,255,0.08);
        }
        .gene-table td {
          padding: 7px 8px; border-bottom: 1px solid rgba(255,255,255,0.05);
          font-size: 14px; font-family: 'Geist Mono', monospace; color: rgba(255,255,255,0.95);
        }
        .gene-table td.val { text-align: right; color: rgba(255,255,255,0.85); }
        .gene-table tr:last-child td { border-bottom: none; }
        .del-btn { background: none; border: none; cursor: pointer; color: rgba(255,255,255,0.45); font-size: 16px; padding: 0; transition: color 0.15s; }
        .del-btn:hover { color: rgba(255,255,255,0.95); }

        .run-btn {
          width: 100%; padding: 16px 24px; border: none; border-radius: 10px;
          font-size: 17px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase;
          cursor: pointer; transition: opacity 0.15s, transform 0.12s;
          display: flex; align-items: center; justify-content: center; gap: 10px;
        }
        .run-btn.active { background: #fff; color: #080C16; }
        .run-btn.active:hover { opacity: 0.85; transform: translateY(-1px); }
        .run-btn.inactive { background: rgba(255,255,255,0.07); color: rgba(255,255,255,0.45); cursor: not-allowed; }

        .ghost-btn {
          width: 100%; padding: 13px 24px; background: transparent;
          border: 1px solid rgba(255,255,255,0.25); border-radius: 10px;
          font-size: 14px; font-family: 'Geist Mono', monospace; letter-spacing: 0.08em;
          text-transform: uppercase; color: rgba(255,255,255,0.88); cursor: pointer;
          transition: border-color 0.2s, color 0.2s;
        }
        .ghost-btn:hover { border-color: rgba(255,255,255,0.65); color: #fff; }
        .ghost-btn:disabled { opacity: 0.35; cursor: not-allowed; }

        .add-btn {
          padding: 9px 18px; background: rgba(255,255,255,0.08);
          border: 1px solid rgba(255,255,255,0.18); border-radius: 7px;
          font-size: 14px; font-family: 'Geist Mono', monospace; color: #fff;
          cursor: pointer; transition: background 0.15s, border-color 0.15s; white-space: nowrap;
          flex-shrink: 0;
        }
        .add-btn:hover { background: rgba(255,255,255,0.14); border-color: rgba(255,255,255,0.35); }

        .gene-input {
          flex: 1; padding: 9px 12px; background: rgba(255,255,255,0.05);
          border: 1px solid rgba(255,255,255,0.12); border-radius: 7px;
          font-size: 14px; font-family: 'Geist Mono', monospace; color: #fff;
          outline: none; transition: border-color 0.15s; min-width: 0;
        }
        .gene-input:focus { border-color: rgba(255,255,255,0.4); }
        .gene-input::placeholder { color: rgba(255,255,255,0.38); }

        .norm-radio { display: flex; gap: 6px; flex-direction: column; }
        .norm-option { display: flex; align-items: flex-start; gap: 10px; cursor: pointer; padding: 8px 10px; border-radius: 8px; transition: background 0.15s; }
        .norm-option:hover { background: rgba(255,255,255,0.04); }
        .norm-option input[type=radio] { margin-top: 3px; flex-shrink: 0; accent-color: #fff; }
        .norm-label { font-size: 14px; color: rgba(255,255,255,0.92); }
        .norm-sub { font-size: 12px; color: rgba(255,255,255,0.62); font-family: 'Geist Mono', monospace; margin-top: 2px; }

        .col-pill {
          padding: 10px 18px; border: 1px solid rgba(255,255,255,0.2); border-radius: 8px;
          background: rgba(255,255,255,0.04); font-family: 'Geist Mono', monospace;
          font-size: 13px; color: rgba(255,255,255,0.92); cursor: pointer;
          transition: background 0.15s, border-color 0.15s; text-align: left;
        }
        .col-pill:hover { background: rgba(255,255,255,0.1); border-color: rgba(255,255,255,0.4); }

        .sidebar {
          background: rgba(255,255,255,0.025); border: 1px solid rgba(255,255,255,0.09);
          border-radius: 18px; overflow: hidden; position: sticky; top: 92px;
        }

        .warn-box { background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.14); border-radius: 8px; padding: 12px 14px; }

        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 999px; }

        .analyse-layout {
          display: grid;
          grid-template-columns: 420px 1fr;
          gap: 36px;
          align-items: start;
        }
        .analyse-wrap {
          max-width: 1400px;
          margin: 0 auto;
          padding: 0 56px;
        }
        @media (max-width: 1100px) {
          .analyse-layout { grid-template-columns: 340px 1fr; gap: 24px; }
          .analyse-wrap { padding: 0 32px; }
        }
        @media (max-width: 860px) {
          .analyse-layout { grid-template-columns: 1fr; }
          .sidebar { position: static !important; }
          .analyse-wrap { padding: 0 20px; }
        }
        @media (max-width: 640px) {
          .analyse-wrap { padding: 0 16px; }
        }
      `}</style>

      <NavBar active="analyse" />

      <main className="analyse-bg" style={{ paddingTop: 100, paddingBottom: 160 }}>
        <div className="analyse-wrap">

          {/* ── Header ── */}
          <div style={{ marginBottom: 52, paddingBottom: 44, borderBottom: '1px solid rgba(255,255,255,0.07)' }}>
            <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 40 }}>
              <div>
                <p style={{ fontFamily: 'Geist Mono, monospace', fontSize: 13, letterSpacing: '0.14em', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.92)', marginBottom: 14 }}>
                  Senebiclabs · Respiratory Intelligence Engine
                </p>
                <h1 style={{ fontFamily: '"SF Pro Display", -apple-system, system-ui, sans-serif', fontSize: 'clamp(36px, 4vw, 60px)', fontWeight: 200, letterSpacing: '-0.01em', lineHeight: 1.1, marginBottom: 14 }}>
                  Gene deviation analysis
                </h1>
                <p style={{ fontSize: 19, color: 'rgba(255,255,255,0.88)', lineHeight: 1.75, maxWidth: 500, fontWeight: 300, margin: 0 }}>
                  Submit a patient gene expression profile. Every deviation is scored
                  against a reference built from real healthy human lung cells.
                </p>
              </div>

              {/* Model status widget */}
              <div style={{ flexShrink: 0, padding: '16px 22px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.09)', borderRadius: 14, minWidth: 210 }}>
                {statusLoading ? (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <Spinner size={13} />
                    <span style={{ fontSize: 15, color: 'rgba(255,255,255,0.88)', fontFamily: 'monospace' }}>Connecting…</span>
                  </div>
                ) : modelReady ? (
                  <>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
                      <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#fff', display: 'inline-block' }} />
                      <span style={{ fontSize: 13, color: 'rgba(255,255,255,0.92)', fontFamily: 'monospace', letterSpacing: '0.12em', textTransform: 'uppercase' as const }}>Model ready</span>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px 18px' }}>
                      {[
                        { l: 'Cells',      v: modelStatus?.n_healthy_cells?.toLocaleString() ?? ', ' },
                        { l: 'Donors',     v: String(modelStatus?.n_donors ?? ', ') },
                        { l: 'Cell types', v: String(modelStatus?.n_cell_types ?? ', ') },
                        { l: 'Pathways',   v: String(modelStatus?.n_pathways ?? ', ') },
                      ].map(({ l, v }) => (
                        <div key={l}>
                          <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.92)', letterSpacing: '0.1em', textTransform: 'uppercase' as const, fontFamily: 'monospace', marginBottom: 2 }}>{l}</div>
                          <div style={{ fontSize: 20, fontWeight: 300, color: '#fff' }}>{v}</div>
                        </div>
                      ))}
                    </div>
                  </>
                ) : (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'rgba(255,255,255,0.55)', display: 'inline-block' }} />
                    <span style={{ fontSize: 13, color: 'rgba(255,255,255,0.88)', fontFamily: 'monospace', letterSpacing: '0.1em', textTransform: 'uppercase' as const }}>
                      {modelStatus === null ? 'API offline' : 'Model loading'}
                    </span>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* ── Layout ── */}
          <div className="analyse-layout">

            {/* ════════════════════ LEFT, Input ════════════════════ */}
            <div className="sidebar">
              <div style={{ padding: '28px 28px 32px' }}>

                {/* ── Multi-sample column picker ── */}
                {hasPicker && (
                  <div className="appear">
                    <p style={{ fontSize: 13, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.12em', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.92)', marginBottom: 6 }}>
                      {columnOptions!.length} samples detected
                    </p>
                    <p style={{ fontSize: 15, color: 'rgba(255,255,255,0.82)', marginBottom: 20, lineHeight: 1.6 }}>
                      Your file contains multiple samples. Select the one to analyse:
                    </p>
                    <div style={{ display: 'flex', flexDirection: 'column' as const, gap: 7, marginBottom: 20 }}>
                      {columnOptions!.map((opt, i) => (
                        <button key={i} className="col-pill"
                          onClick={() => { setRows(opt.rows); setColumnOptions(null) }}>
                          <div style={{ fontWeight: 600 }}>{opt.name}</div>
                          <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.62)', marginTop: 2 }}>{opt.rows.length} genes</div>
                        </button>
                      ))}
                    </div>
                    <button className="ghost-btn" onClick={() => setColumnOptions(null)}>← back</button>
                  </div>
                )}

                {/* ── Empty: upload / paste / manual ── */}
                {!hasPicker && !hasData && (
                  <>
                    <p style={{ fontSize: 14, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.12em', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.92)', marginBottom: 18 }}>
                      Load expression data
                    </p>

                    {/* Drop zone */}
                    <div
                      className={`drop-zone${dragOver ? ' over' : ''}`}
                      onPaste={handlePaste}
                      onDrop={handleDrop}
                      onDragOver={e => { e.preventDefault(); setDragOver(true) }}
                      onDragLeave={() => setDragOver(false)}
                      onClick={() => fileRef.current?.click()}
                    >
                      <div style={{ fontSize: 26, marginBottom: 12, opacity: 0.65 }}>↑</div>
                      <p style={{ fontSize: 16, color: '#fff', fontWeight: 400, marginBottom: 8, lineHeight: 1.5 }}>
                        Drop file or click to browse
                      </p>
                      <p style={{ fontSize: 13, color: 'rgba(255,255,255,0.93)', fontFamily: 'Geist Mono, monospace', margin: 0 }}>
                        .h5ad · .zip · .csv · .tsv · .txt · .xlsx
                      </p>
                      <p style={{ fontSize: 14, color: 'rgba(255,255,255,0.62)', fontFamily: 'monospace', marginTop: 6, margin: '6px 0 0' }}>
                        or paste two columns from a spreadsheet
                      </p>
                      <input ref={fileRef} type="file" accept=".h5ad,.zip,.csv,.tsv,.txt,.xlsx,.xls,.xlsm" style={{ display: 'none' }} onChange={handleFileInput} />
                    </div>

                    {inputError && (
                      <p style={{ marginTop: 10, fontSize: 13, color: 'rgba(255,255,255,0.78)', fontFamily: 'monospace', lineHeight: 1.6 }}>{inputError}</p>
                    )}

                    {/* Format hint */}
                    <div style={{ marginTop: 16, padding: '14px 16px', background: 'rgba(255,255,255,0.025)', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 9 }}>
                      <p style={{ fontSize: 13, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.1em', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.88)', marginBottom: 8 }}>
                        Expected format
                      </p>
                      <pre style={{ fontSize: 13, color: 'rgba(255,255,255,0.92)', fontFamily: 'Geist Mono, monospace', lineHeight: 1.9, margin: 0 }}>{`GENE      VALUE
COL1A1    4.21
SFTPC     0.82
FN1       3.84`}</pre>
                      <p style={{ marginTop: 8, fontSize: 11, color: 'rgba(255,255,255,0.92)', fontFamily: 'monospace' }}>
                        log1p CP10K · gene symbols (COL1A1, not Ensembl IDs) · min 10 genes
                      </p>
                      <p style={{ marginTop: 4, fontSize: 11, color: 'rgba(255,255,255,0.62)', fontFamily: 'monospace' }}>
                        .h5ad and .zip (CellRanger output) are uploaded to the server. Analysis starts automatically
                      </p>
                    </div>

                    {/* Manual entry, always available */}
                    <div style={{ marginTop: 20, padding: '16px 16px 14px', background: 'rgba(255,255,255,0.025)', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 9 }}>
                      <p style={{ fontSize: 13, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.1em', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.88)', marginBottom: 12 }}>
                        Or enter genes manually
                      </p>
                      <div style={{ display: 'flex', gap: 7 }}>
                        <input ref={geneInputRef} className="gene-input" placeholder="Gene symbol" value={geneInput}
                          onChange={e => setGeneInput(e.target.value)} onKeyDown={onManualKeyDown} />
                        <input className="gene-input" style={{ width: 90, flexShrink: 0 }} placeholder="Value" value={valueInput}
                          onChange={e => setValueInput(e.target.value)} onKeyDown={onManualKeyDown} />
                        <button className="add-btn" onClick={addGeneManually}>+ Add</button>
                      </div>
                      {manualErr && <p style={{ marginTop: 6, fontSize: 12, color: 'rgba(255,255,255,0.78)', fontFamily: 'monospace' }}>{manualErr}</p>}
                      {rows.length > 0 && (
                        <p style={{ marginTop: 8, fontSize: 14, color: 'rgba(255,255,255,0.93)', fontFamily: 'monospace' }}>
                          {rows.length} gene{rows.length !== 1 ? 's' : ''} entered
                          {rows.length < 10 ? ` · ${10 - rows.length} more needed` : ' · ready'}
                        </p>
                      )}
                    </div>

                    {/* Sample profile */}
                    <div style={{ marginTop: 16, borderTop: '1px solid rgba(255,255,255,0.07)', paddingTop: 18 }}>
                      <p style={{ fontSize: 13, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.1em', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.88)', marginBottom: 12 }}>
                        No file? Try a sample profile
                      </p>
                      <button className="ghost-btn" onClick={loadSampleProfile}>
                        Load IPF demo profile →
                      </button>
                      <p style={{ marginTop: 8, fontSize: 14, color: 'rgba(255,255,255,0.92)', fontFamily: 'monospace' }}>
                        48 genes · pulmonary fibrosis case · fibroblast, AT2, macrophage, vascular compartments
                      </p>
                    </div>
                  </>
                )}

                {/* ── Has data: gene table + run ── */}
                {!hasPicker && hasData && (
                  <>
                    {/* Parse warnings */}
                    {parseWarnings.map((w, i) => (
                      <div key={i} className="warn-box" style={{ marginBottom: 14 }}>
                        <p style={{ fontSize: 13, color: 'rgba(255,255,255,0.88)', fontFamily: 'monospace', lineHeight: 1.6, margin: 0 }}>⚠ {w}</p>
                      </div>
                    ))}

                    {/* Header */}
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
                      <p style={{ fontSize: 13, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.12em', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.92)' }}>
                        {rows.length} genes loaded
                      </p>
                      <button onClick={clearAll} style={{ fontSize: 13, color: 'rgba(255,255,255,0.92)', background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'monospace', transition: 'color 0.15s' }}
                        onMouseOver={e => (e.currentTarget.style.color = '#fff')}
                        onMouseOut={e => (e.currentTarget.style.color = 'rgba(255,255,255,0.92)')}
                      >Clear ×</button>
                    </div>

                    {rows.length < 10 && (
                      <p style={{ fontSize: 13, color: 'rgba(255,255,255,0.78)', fontFamily: 'monospace', marginBottom: 12 }}>
                        {10 - rows.length} more gene{10 - rows.length !== 1 ? 's' : ''} needed (minimum 10)
                      </p>
                    )}

                    {/* Gene table */}
                    <div style={{ maxHeight: 260, overflowY: 'auto', marginBottom: 14, border: '1px solid rgba(255,255,255,0.07)', borderRadius: 8 }}>
                      <table className="gene-table">
                        <thead>
                          <tr>
                            <th style={{ paddingLeft: 12 }}>Gene</th>
                            <th style={{ textAlign: 'right', paddingRight: 10 }}>Value</th>
                            <th style={{ width: 28 }} />
                          </tr>
                        </thead>
                        <tbody>
                          {rows.map((r, i) => (
                            <tr key={i}>
                              <td style={{ paddingLeft: 12 }}>{r.gene}</td>
                              <td className="val">{parseFloat(r.value).toFixed(3)}</td>
                              <td style={{ textAlign: 'center' }}>
                                <button className="del-btn" onClick={() => removeRow(i)}>×</button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>

                    {/* Manual entry row, always at bottom of table */}
                    <div style={{ marginBottom: 18 }}>
                      <div style={{ display: 'flex', gap: 6 }}>
                        <input ref={geneInputRef} className="gene-input" placeholder="Add gene…" value={geneInput}
                          onChange={e => setGeneInput(e.target.value)} onKeyDown={onManualKeyDown} />
                        <input className="gene-input" style={{ width: 86, flexShrink: 0 }} placeholder="Value" value={valueInput}
                          onChange={e => setValueInput(e.target.value)} onKeyDown={onManualKeyDown} />
                        <button className="add-btn" onClick={addGeneManually}>+ Add</button>
                      </div>
                      {manualErr && <p style={{ marginTop: 5, fontSize: 12, color: 'rgba(255,255,255,0.78)', fontFamily: 'monospace' }}>{manualErr}</p>}
                    </div>

                    {/* Normalization */}
                    <div style={{ marginBottom: 16, padding: '14px 14px', background: 'rgba(255,255,255,0.025)', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 9 }}>
                      <p style={{ fontSize: 13, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.1em', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.88)', marginBottom: 10 }}>
                        Data format
                      </p>
                      <div className="norm-radio">
                        {([
                          { v: 'log1p_cp10k', label: 'log1p CP10K', sub: 'Default · Scanpy / Seurat normalized output' },
                          { v: 'raw_counts',  label: 'Raw counts',  sub: 'Will normalize: ÷ total × 10,000 → log1p' },
                          { v: 'cp10k',       label: 'CP10K (not log-transformed)', sub: 'Will apply log1p transform' },
                        ] as {v: NormMode; label: string; sub: string}[]).map(opt => (
                          <label key={opt.v} className="norm-option">
                            <input type="radio" name="norm" value={opt.v}
                              checked={normMode === opt.v} onChange={() => setNormMode(opt.v)} />
                            <div>
                              <div className="norm-label">{opt.label}</div>
                              <div className="norm-sub">{opt.sub}</div>
                            </div>
                          </label>
                        ))}
                      </div>
                    </div>

                    {/* Sample metadata */}
                    <div style={{ marginBottom: 16 }}>
                      <input
                        style={{ width: '100%', padding: '11px 14px', background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.11)', borderRadius: 8, fontSize: 14, color: '#fff', fontFamily: 'Geist Mono, monospace', outline: 'none', boxSizing: 'border-box' as const }}
                        placeholder="Sample ID  (optional)"
                        value={sampleId} onChange={e => setSampleId(e.target.value)}
                      />
                    </div>

                    {/* Run button */}
                    <button onClick={run} disabled={!canRun} className={`run-btn ${canRun ? 'active' : 'inactive'}`}>
                      {loading
                        ? <><Spinner size={16} />{step === 'analysing' ? 'Analysing…' : 'Interpreting…'}</>
                        : 'Run analysis →'}
                    </button>

                    {!modelReady && !statusLoading && (
                      <p style={{ marginTop: 12, fontSize: 13, color: 'rgba(255,255,255,0.78)', fontFamily: 'monospace', lineHeight: 1.7 }}>
                        Model not ready. You can still run. The API will return details.
                      </p>
                    )}
                  </>
                )}

                <p style={{ marginTop: 26, fontSize: 11, color: 'rgba(255,255,255,0.82)', textAlign: 'center', fontFamily: 'monospace', letterSpacing: '0.08em', lineHeight: 2 }}>
                  FOR RESEARCH USE ONLY · NOT A DIAGNOSTIC DEVICE
                </p>
              </div>
            </div>

            {/* ════════════════════ RIGHT, Results ════════════════════ */}
            <div>

              {/* Empty */}
              {!report && !loading && !error && (
                <div style={{ minHeight: 440, border: '1px dashed rgba(255,255,255,0.08)', borderRadius: 16, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 48 }}>
                  <div style={{ fontSize: 15, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.1em', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.92)', lineHeight: 2.8, textAlign: 'center' }}>
                    Results will appear here<br />
                    Load a gene expression file or use a sample profile
                  </div>
                </div>
              )}

              {/* Loading */}
              {loading && (
                <div style={{ minHeight: 440, border: '1px solid rgba(255,255,255,0.06)', borderRadius: 16, display: 'flex', flexDirection: 'column' as const, alignItems: 'center', justifyContent: 'center', gap: 22 }}>
                  <Spinner size={40} />
                  <div style={{ textAlign: 'center' }}>
                    <div style={{ fontSize: 22, color: '#fff', marginBottom: 10, fontWeight: 300 }}>
                      {step === 'uploading_h5ad' ? 'Uploading and parsing file'
                        : step === 'analysing' ? 'Comparing against healthy reference'
                        : 'Interpreting biology'}
                    </div>
                    <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.93)', fontFamily: 'monospace', letterSpacing: '0.1em', textTransform: 'uppercase' as const }}>
                      {step === 'uploading_h5ad' ? 'Normalising · pseudo-bulk · running analysis'
                        : step === 'analysing' ? `Computing Z-scores · ${rows.length} genes submitted`
                        : 'Running pattern analysis'}
                    </div>
                  </div>
                </div>
              )}

              {/* Error */}
              {error && !loading && (
                <div className="appear" style={{ padding: '20px 24px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.14)', borderRadius: 12 }}>
                  <pre style={{ whiteSpace: 'pre-wrap', fontFamily: 'monospace', margin: 0, fontSize: 14, color: 'rgba(255,255,255,0.82)', lineHeight: 1.7 }}>{error}</pre>
                </div>
              )}

              {/* Results */}
              {report && !loading && (
                <div className="appear" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>

                  {/* ── Data quality warnings ── */}
                  {report.data_quality_warnings?.length > 0 && (
                    <div style={{ padding: '18px 24px', background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.22)', borderRadius: 12 }}>
                      <div style={{ fontSize: 12, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.14em', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.7)', marginBottom: 12 }}>
                        Data quality warnings · review before interpreting results
                      </div>
                      <div style={{ display: 'flex', flexDirection: 'column' as const, gap: 10 }}>
                        {report.data_quality_warnings.map((w, i) => (
                          <div key={i} style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
                            <span style={{ fontSize: 14, color: '#fff', flexShrink: 0, marginTop: 1 }}>⚠</span>
                            <span style={{ fontSize: 14, color: 'rgba(255,255,255,0.9)', fontFamily: 'Geist Mono, monospace', lineHeight: 1.7 }}>{w}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* ── Report header ── */}
                  <div style={{ padding: '28px 32px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.09)', borderRadius: 16 }}>
                    <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 24, marginBottom: 20 }}>
                      <div>
                        <div style={{ fontSize: 13, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.14em', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.93)', marginBottom: 6 }}>Sample ID</div>
                        <div style={{ fontSize: 22, fontFamily: 'Geist Mono, monospace', color: '#fff', fontWeight: 500 }}>{report.sample_id}</div>
                      </div>
                      <div style={{ textAlign: 'right' as const, flexShrink: 0 }}>
                        <div style={{ fontSize: 13, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.14em', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.93)', marginBottom: 4 }}>Deviation score</div>
                        <div style={{ display: 'flex', alignItems: 'baseline', gap: 5, justifyContent: 'flex-end' }}>
                          <span style={{ fontSize: 56, fontWeight: 200, letterSpacing: '-0.04em', lineHeight: 1, color: '#fff' }}>
                            {(report.overall_deviation_score * 100).toFixed(0)}
                          </span>
                          <span style={{ fontSize: 18, color: 'rgba(255,255,255,0.93)', fontFamily: 'monospace' }}>%</span>
                        </div>
                        <div style={{ fontSize: 13, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.1em', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.88)', marginTop: 4 }}>
                          {report.overall_deviation_score < 0.2 ? 'Mild deviation' : report.overall_deviation_score < 0.4 ? 'Moderate deviation' : 'Substantial deviation'}
                        </div>
                      </div>
                    </div>

                    <div style={{ height: 5, background: 'rgba(255,255,255,0.08)', borderRadius: 999, marginBottom: 20, overflow: 'hidden' }}>
                      <div style={{ height: '100%', width: `${report.overall_deviation_score * 100}%`, background: 'rgba(255,255,255,0.85)', borderRadius: 999, transition: 'width 1.4s cubic-bezier(0.16,1,0.3,1)' }} />
                    </div>

                    <p style={{ fontSize: 18, color: 'rgba(255,255,255,0.95)', lineHeight: 1.8, fontWeight: 300, marginBottom: 18 }}>{report.summary}</p>

                    <div style={{ display: 'flex', gap: 28, flexWrap: 'wrap' as const, marginBottom: 20 }}>
                      {[
                        { l: 'Genes submitted', v: String(rows.length) },
                        { l: 'Reference cells',  v: report.healthy_reference_cells?.toLocaleString() },
                        { l: 'Donors',           v: String(report.healthy_reference_donors) },
                        { l: 'Cells flagged',    v: String(report.cell_type_deviations.filter(d => d.direction !== 'normal').length) },
                        { l: 'Genes flagged',    v: String(report.gene_deviations.length) },
                        { l: 'Pathways flagged', v: String(report.pathway_deviations.length) },
                      ].map(({ l, v }) => (
                        <div key={l}>
                          <div style={{ fontSize: 10, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.12em', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.92)', marginBottom: 3 }}>{l}</div>
                          <div style={{ fontSize: 18, fontWeight: 300, color: '#fff' }}>{v}</div>
                        </div>
                      ))}
                    </div>

                    {/* Download */}
                    <button onClick={downloadReport} style={{
                      display: 'inline-flex', alignItems: 'center', gap: 8,
                      padding: '9px 18px', background: 'rgba(255,255,255,0.07)',
                      border: '1px solid rgba(255,255,255,0.18)', borderRadius: 8,
                      fontSize: 13, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.08em',
                      textTransform: 'uppercase' as const, color: '#fff', cursor: 'pointer',
                      transition: 'background 0.15s',
                    }}
                      onMouseOver={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.12)')}
                      onMouseOut={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.07)')}
                    >
                      ↓ Download report (CSV)
                    </button>
                  </div>

                  {/* ── Cell type composition ── */}
                  {(() => {
                    const nonNormal = report.cell_type_deviations.filter(d => d.direction !== 'normal')
                    if (!nonNormal.length) return null
                    const maxZ = Math.max(...nonNormal.map(d => Math.abs(d.z_score)), 0.1)
                    return (
                      <div style={{ padding: '24px 28px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.09)', borderRadius: 16 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
                          <span style={{ fontSize: 13, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.14em', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.92)' }}>Cell type composition</span>
                          <span style={{ fontSize: 14, fontFamily: 'Geist Mono, monospace', color: 'rgba(255,255,255,0.93)' }}>◄ depleted · expanded ► · scaled to sample max Z</span>
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column' as const, gap: 20 }}>
                          {nonNormal.map((d, i) => {
                            const isElev = d.direction === 'expanded'
                            const pct = Math.min(Math.abs(d.z_score) / maxZ, 1) * 44
                            return (
                              <div key={i}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 5 }}>
                                  <div style={{ minWidth: 220, flexShrink: 0 }}>
                                    <div style={{ fontSize: 15, color: '#fff', fontWeight: 400, marginBottom: 2 }}>{d.cell_type}</div>
                                    <div style={{ fontSize: 13, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.1em', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.92)' }}>
                                      {d.compartment} · {d.magnitude}
                                    </div>
                                  </div>
                                  <div style={{ flex: 1, position: 'relative', height: 6, background: 'rgba(255,255,255,0.07)', borderRadius: 999 }}>
                                    <div style={{ position: 'absolute', left: '50%', top: -3, bottom: -3, width: 1, background: 'rgba(255,255,255,0.22)' }} />
                                    <div style={{ position: 'absolute', height: '100%', borderRadius: 999, background: isElev ? 'rgba(255,255,255,0.88)' : 'rgba(255,255,255,0.82)', ...(isElev ? { left: '50%', width: `${pct}%` } : { right: '50%', width: `${pct}%` }), transition: `width 1s cubic-bezier(0.16,1,0.3,1) ${i * 80}ms` }} />
                                  </div>
                                  <div style={{ flexShrink: 0, textAlign: 'right' as const, minWidth: 110 }}>
                                    <span style={{ fontSize: 15, fontFamily: 'Geist Mono, monospace', fontWeight: 600, color: isElev ? '#fff' : 'rgba(255,255,255,0.82)' }}>
                                      {d.z_score > 0 ? '+' : ''}{d.z_score.toFixed(1)}σ
                                    </span>
                                    <span style={{ fontSize: 13, fontFamily: 'Geist Mono, monospace', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.93)', letterSpacing: '0.08em', marginLeft: 8 }}>
                                      {d.direction}
                                    </span>
                                  </div>
                                </div>
                                <div style={{ paddingLeft: 232, marginBottom: d.interpretation ? 3 : 0 }}>
                                  <span style={{ fontSize: 14, fontFamily: 'Geist Mono, monospace', color: 'rgba(255,255,255,0.93)' }}>
                                    sample {(d.estimated_fraction * 100).toFixed(1)}% · healthy {(d.healthy_mean_fraction * 100).toFixed(1)}% ±{(d.healthy_std_fraction * 100).toFixed(1)}%
                                  </span>
                                </div>
                                {d.interpretation && (
                                  <div style={{ paddingLeft: 232, fontSize: 15, color: '#fff', lineHeight: 1.55 }}>{d.interpretation}</div>
                                )}
                              </div>
                            )
                          })}
                        </div>
                      </div>
                    )
                  })()}

                  {/* ── Genes + Pathways ── */}
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>

                    {/* Gene deviations */}
                    <div style={{ padding: '24px 28px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.09)', borderRadius: 16 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 22 }}>
                        <span style={{ fontSize: 13, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.14em', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.92)' }}>Gene deviations</span>
                        <span style={{ fontSize: 14, fontFamily: 'Geist Mono, monospace', color: 'rgba(255,255,255,0.82)' }}>{report.gene_deviations.length} flagged · |Z| ≥ 2.0</span>
                      </div>
                      {report.gene_deviations.length === 0 ? (
                        <p style={{ fontSize: 15, color: 'rgba(255,255,255,0.82)', fontFamily: 'monospace' }}>All submitted genes within healthy range (|Z| &lt; 2.0)</p>
                      ) : (() => {
                        const genes = report.gene_deviations.slice(0, 12)
                        const maxZ = Math.max(...genes.map(g => Math.abs(g.z_score)), 0.1)
                        return (
                          <div style={{ display: 'flex', flexDirection: 'column' as const }}>
                            {genes.map((g, i, a) => {
                              const isElev = g.direction === 'elevated'
                              const barW = Math.min(Math.abs(g.z_score) / maxZ, 1) * 100
                              return (
                                <div key={i} style={{ padding: '13px 0', borderBottom: i < a.length - 1 ? '1px solid rgba(255,255,255,0.06)' : 'none' }}>
                                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, marginBottom: 7 }}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                      <span style={{ fontSize: 17, fontFamily: 'Geist Mono, monospace', fontWeight: 600, color: '#fff' }}>{g.gene}</span>
                                      <span style={{ fontSize: 14, color: isElev ? '#fff' : 'rgba(255,255,255,0.82)', fontWeight: 600 }}>{isElev ? '↑' : '↓'}</span>
                                      <span style={{ fontSize: 9, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.1em', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.82)', padding: '2px 6px', border: '1px solid rgba(255,255,255,0.18)', borderRadius: 3 }}>{g.magnitude}</span>
                                    </div>
                                    <span style={{ fontSize: 15, fontFamily: 'Geist Mono, monospace', fontWeight: 600, color: isElev ? '#fff' : 'rgba(255,255,255,0.82)', flexShrink: 0 }}>
                                      {g.z_score > 0 ? '+' : ''}{g.z_score.toFixed(1)}σ
                                    </span>
                                  </div>
                                  <div style={{ height: 3, background: 'rgba(255,255,255,0.07)', borderRadius: 999, overflow: 'hidden', marginBottom: 7 }}>
                                    <div style={{ height: '100%', width: `${barW}%`, background: isElev ? 'rgba(255,255,255,0.85)' : 'rgba(255,255,255,0.82)', borderRadius: 999 }} />
                                  </div>
                                  <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' as const }}>
                                    <span style={{ fontSize: 13, fontFamily: 'Geist Mono, monospace', color: 'rgba(255,255,255,0.95)', fontWeight: 600 }}>sample {g.sample_value.toFixed(2)}</span>
                                    <span style={{ fontSize: 14, color: 'rgba(255,255,255,0.88)', fontFamily: 'monospace' }}>vs ref</span>
                                    <span style={{ fontSize: 14, fontFamily: 'Geist Mono, monospace', color: 'rgba(255,255,255,0.85)' }}>{g.healthy_mean.toFixed(2)} ±{g.healthy_std.toFixed(2)}</span>
                                    <span style={{ fontSize: 14, fontFamily: 'Geist Mono, monospace', color: 'rgba(255,255,255,0.92)' }}>(p5-p95: {g.healthy_p5.toFixed(2)}-{g.healthy_p95.toFixed(2)})</span>
                                  </div>
                                </div>
                              )
                            })}
                          </div>
                        )
                      })()}
                    </div>

                    {/* Pathway deviations */}
                    <div style={{ padding: '24px 28px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.09)', borderRadius: 16 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 22 }}>
                        <span style={{ fontSize: 13, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.14em', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.92)' }}>Biological pathways</span>
                        <span style={{ fontSize: 14, fontFamily: 'Geist Mono, monospace', color: 'rgba(255,255,255,0.82)' }}>{report.pathway_deviations.length} flagged · |Δ| ≥ 0.8</span>
                      </div>
                      {report.pathway_deviations.length === 0 ? (
                        <p style={{ fontSize: 15, color: 'rgba(255,255,255,0.82)', fontFamily: 'monospace' }}>No pathway deviations (|Δ| &lt; 0.8 log1p CP10K from baseline)</p>
                      ) : (
                        <div style={{ display: 'flex', flexDirection: 'column' as const }}>
                          {report.pathway_deviations.map((p, i, a) => {
                            const isOver = p.direction === 'over-active'
                            const delta = p.deviation_from_baseline
                            return (
                              <div key={i} style={{ padding: '16px 0', borderBottom: i < a.length - 1 ? '1px solid rgba(255,255,255,0.06)' : 'none' }}>
                                <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10, marginBottom: 7 }}>
                                  <div style={{ flex: 1 }}>
                                    <div style={{ fontSize: 15, color: '#fff', fontWeight: 400, lineHeight: 1.35 }}>{p.pathway}</div>
                                    {p.category && <div style={{ fontSize: 13, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.08em', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.92)', marginTop: 2 }}>{p.category.replace(/_/g, ' ')}</div>}
                                  </div>
                                  <span style={{ flexShrink: 0, fontSize: 13, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.08em', textTransform: 'uppercase' as const, padding: '3px 8px', borderRadius: 4, background: isOver ? 'rgba(255,255,255,0.1)' : 'rgba(255,255,255,0.05)', color: isOver ? '#fff' : 'rgba(255,255,255,0.88)', border: '1px solid rgba(255,255,255,0.16)' }}>
                                    {isOver ? '↑ over-active' : '↓ suppressed'}
                                  </span>
                                </div>
                                {p.trigger_baseline_expr !== null && (
                                  <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', marginBottom: 7, flexWrap: 'wrap' as const }}>
                                    <span style={{ fontSize: 14, fontFamily: 'Geist Mono, monospace', color: 'rgba(255,255,255,0.92)' }}>avg {p.avg_expression.toFixed(2)}</span>
                                    <span style={{ fontSize: 14, color: 'rgba(255,255,255,0.88)', fontFamily: 'monospace' }}>vs baseline</span>
                                    <span style={{ fontSize: 14, fontFamily: 'Geist Mono, monospace', color: 'rgba(255,255,255,0.85)' }}>{p.trigger_baseline_expr.toFixed(2)}</span>
                                    {delta !== null && <span style={{ fontSize: 14, fontFamily: 'Geist Mono, monospace', color: '#fff', fontWeight: 600 }}>Δ{delta > 0 ? '+' : ''}{delta.toFixed(2)} log1p</span>}
                                  </div>
                                )}
                                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                                  <div style={{ flex: 1, height: 3, background: 'rgba(255,255,255,0.07)', borderRadius: 999, overflow: 'hidden' }}>
                                    <div style={{ height: '100%', width: `${(p.n_active_genes / p.n_total_genes) * 100}%`, background: isOver ? 'rgba(255,255,255,0.92)' : 'rgba(255,255,255,0.42)', borderRadius: 999 }} />
                                  </div>
                                  <span style={{ fontSize: 14, fontFamily: 'Geist Mono, monospace', color: 'rgba(255,255,255,0.85)', flexShrink: 0 }}>{p.n_active_genes}/{p.n_total_genes} genes</span>
                                </div>
                                {p.active_genes?.length > 0 && (
                                  <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' as const, marginBottom: p.interpretation ? 6 : 0 }}>
                                    {p.active_genes.map(g => (
                                      <span key={g} style={{ fontSize: 10, fontFamily: 'Geist Mono, monospace', color: 'rgba(255,255,255,0.88)', background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.14)', borderRadius: 3, padding: '1px 5px' }}>{g}</span>
                                    ))}
                                  </div>
                                )}
                                {p.interpretation && <p style={{ fontSize: 15, color: '#fff', lineHeight: 1.6, margin: 0 }}>{p.interpretation}</p>}
                              </div>
                            )
                          })}
                        </div>
                      )}
                    </div>
                  </div>

                  {/* ── Spatial context ── */}
                  {report.spatial_context && (
                    <div style={{ padding: '24px 28px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.09)', borderRadius: 16 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
                        <span style={{ fontSize: 13, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.14em', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.92)' }}>Spatial localisation</span>
                        <span style={{
                          fontSize: 13, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.12em',
                          textTransform: 'uppercase' as const, padding: '3px 10px', borderRadius: 20,
                          background: 'rgba(255,255,255,0.08)',
                          color: '#fff',
                          border: '1px solid rgba(255,255,255,0.22)',
                        }}>
                          {report.spatial_context.dominant_tissue_compartment.toUpperCase()} dominant
                        </span>
                      </div>

                      {/* Summary sentence */}
                      <p style={{ fontSize: 15, lineHeight: 1.75, color: 'rgba(255,255,255,0.93)', fontWeight: 300, marginBottom: 24 }}>
                        {report.spatial_context.tissue_localisation_summary}
                      </p>

                      {/* Airway vs Alveolar gene lists */}
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: report.spatial_context.gene_spatial_contexts.length ? 24 : 0 }}>
                        {[
                          { label: 'Airway signal genes', genes: report.spatial_context.airway_signal_genes },
                          { label: 'Alveolar signal genes', genes: report.spatial_context.alveolar_signal_genes },
                        ].map(({ label, genes }) => (
                          <div key={label} style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.12)', borderRadius: 10, padding: '14px 16px' }}>
                            <div style={{ fontSize: 13, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.12em', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.88)', marginBottom: 10 }}>{label}</div>
                            {genes.length === 0
                              ? <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.75)', fontStyle: 'italic' }}>none flagged</div>
                              : <div style={{ display: 'flex', flexWrap: 'wrap' as const, gap: 6 }}>
                                  {genes.map(g => (
                                    <span key={g} style={{ fontFamily: 'Geist Mono, monospace', fontSize: 12, color: '#fff', background: 'rgba(255,255,255,0.08)', padding: '2px 8px', borderRadius: 4 }}>{g}</span>
                                  ))}
                                </div>
                            }
                          </div>
                        ))}
                      </div>

                      {/* Per-gene spatial table */}
                      {report.spatial_context.gene_spatial_contexts.length > 0 && (
                        <div>
                          <div style={{ fontSize: 13, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.12em', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.82)', marginBottom: 12 }}>
                            Gene spatial baselines · bronchus vs parenchyma
                          </div>
                          <div style={{ display: 'flex', flexDirection: 'column' as const, gap: 8 }}>
                            {report.spatial_context.gene_spatial_contexts.map(g => (
                              <div key={g.gene} style={{ display: 'grid', gridTemplateColumns: '120px 80px 1fr', alignItems: 'center', gap: 12, padding: '8px 0', borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                                <span style={{ fontFamily: 'Geist Mono, monospace', fontSize: 13, color: '#fff', fontWeight: 500 }}>{g.gene}</span>
                                <span style={{
                                  fontSize: 10, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.1em',
                                  textTransform: 'uppercase' as const, padding: '2px 7px', borderRadius: 12, textAlign: 'center' as const,
                                  background: 'rgba(255,255,255,0.07)',
                                  color: 'rgba(255,255,255,0.88)',
                                }}>
                                  {g.tissue_enriched_in}
                                </span>
                                <span style={{ fontSize: 14, color: 'rgba(255,255,255,0.82)', fontFamily: 'Geist Mono, monospace' }}>
                                  bronchus {g.bronchus_baseline.toFixed(2)} · parenchyma {g.parenchyma_baseline.toFixed(2)}
                                  {g.clinical_note && <span style={{ color: 'rgba(255,255,255,0.75)', marginLeft: 8 }}> · {g.clinical_note.slice(0, 60)}{g.clinical_note.length > 60 ? '…' : ''}</span>}
                                </span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {/* ── Interpretation unavailable ── */}
                  {interpUnavailable && !interp && (
                    <div style={{ padding: '18px 24px', background: 'rgba(255,255,255,0.025)', border: '1px solid rgba(255,255,255,0.09)', borderRadius: 12, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 20 }}>
                      <div>
                        <div style={{ fontSize: 14, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.14em', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.88)', marginBottom: 7 }}>Biological interpretation</div>
                        <div style={{ fontSize: 16, color: 'rgba(255,255,255,0.88)', lineHeight: 1.65, fontWeight: 300 }}>
                          Chain-of-thought reasoning is not enabled. Add{' '}
                          <code style={{ fontFamily: 'Geist Mono, monospace', fontSize: 14, color: '#fff', background: 'rgba(255,255,255,0.08)', padding: '1px 6px', borderRadius: 3 }}>ANTHROPIC_API_KEY</code>{' '}
                          to your <code style={{ fontFamily: 'Geist Mono, monospace', fontSize: 14, color: '#fff', background: 'rgba(255,255,255,0.08)', padding: '1px 6px', borderRadius: 3 }}>.env</code> to enable.
                        </div>
                      </div>
                      <div style={{ fontSize: 13, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.1em', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.88)', flexShrink: 0 }}>503 · not configured</div>
                    </div>
                  )}

                  {/* ── Biological interpretation ── */}
                  {interp && (
                    <div style={{ padding: '28px 32px', background: 'rgba(255,255,255,0.025)', border: '1px solid rgba(255,255,255,0.09)', borderRadius: 16 }}>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap' as const, gap: 10, marginBottom: 22, paddingBottom: 20, borderBottom: '1px solid rgba(255,255,255,0.07)' }}>
                        <span style={{ fontSize: 13, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.14em', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.92)' }}>Biological interpretation</span>
                        <div style={{ display: 'flex', gap: 8 }}>
                          {interp.stage && <span style={{ fontSize: 14, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.08em', textTransform: 'uppercase' as const, padding: '4px 10px', borderRadius: 6, background: 'rgba(255,255,255,0.09)', border: '1px solid rgba(255,255,255,0.18)', color: '#fff' }}>{interp.stage}</span>}
                          {interp.overall_confidence && <span style={{ fontSize: 14, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.08em', textTransform: 'uppercase' as const, padding: '4px 10px', borderRadius: 6, background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.12)', color: 'rgba(255,255,255,0.92)' }}>{interp.overall_confidence}</span>}
                        </div>
                      </div>

                      {interp.final_interpretation && (
                        <p style={{ fontSize: 19, color: 'rgba(255,255,255,0.95)', lineHeight: 1.88, fontWeight: 300, marginBottom: 24 }}>{interp.final_interpretation}</p>
                      )}

                      {interp.anomalous_findings.length > 0 && (
                        <div style={{ marginBottom: 24, padding: '18px 20px', background: 'rgba(255,255,255,0.025)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 10 }}>
                          <div style={{ fontSize: 13, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.14em', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.88)', marginBottom: 14 }}>Anomalous findings</div>
                          <div style={{ display: 'flex', flexDirection: 'column' as const, gap: 10 }}>
                            {interp.anomalous_findings.map((f, i) => (
                              <div key={i} style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
                                <span style={{ fontSize: 15, color: 'rgba(255,255,255,0.88)', flexShrink: 0, marginTop: 1 }}>›</span>
                                <span style={{ fontSize: 15, color: 'rgba(255,255,255,0.95)', lineHeight: 1.7 }}>{f}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {interp.biological_question && (
                        <div style={{ marginBottom: 20, padding: '16px 20px', background: 'rgba(255,255,255,0.025)', borderLeft: '2px solid rgba(255,255,255,0.28)', borderRadius: '0 8px 8px 0' }}>
                          <div style={{ fontSize: 13, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.14em', textTransform: 'uppercase' as const, color: 'rgba(255,255,255,0.88)', marginBottom: 8 }}>Key biological question</div>
                          <p style={{ fontSize: 16, color: 'rgba(255,255,255,0.92)', lineHeight: 1.78, fontStyle: 'italic', margin: 0 }}>{interp.biological_question}</p>
                        </div>
                      )}

                      {interp.full_reasoning && (
                        <div style={{ borderTop: '1px solid rgba(255,255,255,0.06)', paddingTop: 16 }}>
                          <button onClick={() => setShowReasoning(r => !r)}
                            style={{ fontSize: 14, color: 'rgba(255,255,255,0.85)', background: 'none', border: 'none', cursor: 'pointer', padding: 0, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.08em', transition: 'color 0.15s' }}
                            onMouseOver={e => (e.currentTarget.style.color = '#fff')}
                            onMouseOut={e => (e.currentTarget.style.color = 'rgba(255,255,255,0.85)')}
                          >
                            {showReasoning ? '↑ hide reasoning' : '↓ show full chain-of-thought reasoning'}
                          </button>
                          {showReasoning && (
                            <pre style={{ marginTop: 14, fontSize: 15, color: '#fff', lineHeight: 1.9, whiteSpace: 'pre-wrap', fontFamily: 'Geist Mono, monospace', background: 'rgba(0,0,0,0.25)', padding: '16px 18px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.06)' }}>
                              {interp.full_reasoning}
                            </pre>
                          )}
                        </div>
                      )}
                    </div>
                  )}

                  {/* Disclaimer */}
                  <p style={{ fontSize: 14, color: 'rgba(255,255,255,0.58)', lineHeight: 1.9, paddingTop: 4, fontFamily: 'Geist Mono, monospace', letterSpacing: '0.04em' }}>
                    {report.safety_disclaimer}
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>
      </main>
    </>
  )
}
