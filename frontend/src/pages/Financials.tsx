import { useEffect, useMemo, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import {
  getAssessment,
  getFinancialsIndex,
  getFinancialAnalytics,
  getReportStatus,
  getStatementBlock,
  getNarrativeMarkdown,
  getLinkedNote,
  reportDocxUrl,
  triggerReportGeneration,
  csvUrl,
  pdfUrl,
  type FinancialsIndex,
  type FinancialsBlock,
  type FinancialAnalytics,
  type ReportStatus,
  type StatementBlock,
  type StatementRow,
} from '../api'

// ---- constants ------------------------------------------------------------

type StatementKind = 'sofp' | 'soci' | 'socf'
type Tab = 'spreads' | 'ratios' | 'cashflow' | 'risk'

const STATEMENT_LABELS: Record<StatementKind, string> = {
  sofp: 'Balance Sheet',
  soci: 'Profit & Loss',
  socf: 'Cash Flow',
}

const TAB_LABELS: Record<Tab, string> = {
  spreads: 'Spreads',
  ratios: 'Ratios & Trends',
  cashflow: 'Cash Flow & Runway',
  risk: 'Risk View',
}

type Unit = 'raw' | 'thousand' | 'million'
const UNIT_DIVISORS: Record<Unit, number> = { raw: 1, thousand: 1000, million: 1_000_000 }
const UNIT_LABELS: Record<Unit, string> = { raw: '1', thousand: "'000", million: 'Mn' }

const RATIO_LABELS: Record<string, string> = {
  gross_margin: 'Gross margin',
  ebitda_margin: 'EBITDA margin',
  ebit_margin: 'EBIT margin',
  pat_margin: 'PAT margin',
  current_ratio: 'Current ratio',
  quick_ratio: 'Quick ratio',
  cash_ratio: 'Cash ratio',
  debt_equity: 'Debt / equity',
  debt_ebitda: 'Debt / EBITDA',
  interest_coverage: 'Interest cover',
  receivable_days: 'Receivable days',
  payable_days: 'Payable days',
  inventory_days: 'Inventory days',
  asset_turnover: 'Asset turnover',
  return_on_assets: 'ROA',
  return_on_equity: 'ROE',
  cfo_to_debt: 'CFO / debt',
  fcf_to_debt: 'FCF / debt',
}

const RATIO_GROUPS = [
  { title: 'Profitability', keys: ['gross_margin', 'ebitda_margin', 'ebit_margin', 'pat_margin', 'return_on_assets', 'return_on_equity'] },
  { title: 'Liquidity', keys: ['current_ratio', 'quick_ratio', 'cash_ratio'] },
  { title: 'Leverage & Coverage', keys: ['debt_equity', 'debt_ebitda', 'interest_coverage', 'cfo_to_debt', 'fcf_to_debt'] },
  { title: 'Working Capital', keys: ['receivable_days', 'payable_days', 'inventory_days', 'asset_turnover'] },
]

// Policy thresholds — "pass" is the boundary you want to stay above (or below for `direction: below`),
// "watch" is the boundary outside which the metric becomes a Risk flag.
type PolicyStatus = 'pass' | 'watch' | 'risk'
type PolicyDir = 'above' | 'below'
const RATIO_POLICY: Record<string, { pass: number; watch: number; direction: PolicyDir; unit?: string }> = {
  current_ratio:      { pass: 1.0,  watch: 0.7,  direction: 'above' },
  quick_ratio:        { pass: 0.8,  watch: 0.5,  direction: 'above' },
  cash_ratio:         { pass: 0.3,  watch: 0.1,  direction: 'above' },
  debt_equity:        { pass: 3.0,  watch: 5.0,  direction: 'below' },
  debt_ebitda:        { pass: 4.0,  watch: 6.0,  direction: 'below' },
  interest_coverage:  { pass: 1.5,  watch: 1.0,  direction: 'above' },
  ebitda_margin:      { pass: 0.10, watch: 0.05, direction: 'above' },
  pat_margin:         { pass: 0.05, watch: 0.0,  direction: 'above' },
  gross_margin:       { pass: 0.20, watch: 0.10, direction: 'above' },
  return_on_equity:   { pass: 0.10, watch: 0.0,  direction: 'above' },
  return_on_assets:   { pass: 0.05, watch: 0.0,  direction: 'above' },
  cfo_to_debt:        { pass: 0.20, watch: 0.05, direction: 'above' },
  fcf_to_debt:        { pass: 0.10, watch: 0.0,  direction: 'above' },
  receivable_days:    { pass: 60,   watch: 90,   direction: 'below' },
  payable_days:       { pass: 90,   watch: 120,  direction: 'below' },
  inventory_days:     { pass: 60,   watch: 90,   direction: 'below' },
}

function policyStatus(key: string, v: number | null | undefined): PolicyStatus | null {
  const p = RATIO_POLICY[key]
  if (!p || v === null || v === undefined || !Number.isFinite(v)) return null
  if (p.direction === 'above') {
    if (v >= p.pass) return 'pass'
    if (v >= p.watch) return 'watch'
    return 'risk'
  } else {
    if (v <= p.pass) return 'pass'
    if (v <= p.watch) return 'watch'
    return 'risk'
  }
}

function policyLabel(key: string): string {
  const p = RATIO_POLICY[key]
  if (!p) return ''
  const op = p.direction === 'above' ? '≥' : '≤'
  if (key.includes('margin') || key.startsWith('return_') || key.endsWith('_to_debt')) {
    return `${op} ${(p.pass * 100).toFixed(0)}%`
  }
  if (key.includes('days')) return `${op} ${p.pass} days`
  return `${op} ${p.pass.toFixed(p.pass < 1 ? 1 : 1)}x`
}

// ---- formatters -----------------------------------------------------------

function formatValue(v: number | null | undefined, unit: Unit): string {
  if (v === null || v === undefined || Number.isNaN(v)) return ''
  const scaled = v / UNIT_DIVISORS[unit]
  const abs = Math.abs(scaled)
  const opts: Intl.NumberFormatOptions = abs >= 100
    ? { maximumFractionDigits: 0 }
    : { maximumFractionDigits: 2 }
  const formatted = abs.toLocaleString('en-US', opts)
  return scaled < 0 ? `(${formatted})` : formatted
}

function formatCompactValue(v: number | null | undefined, currency: string): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  const abs = Math.abs(v)
  let body = ''
  if (abs >= 1_000_000) body = `${(abs / 1_000_000).toFixed(2)}M`
  else if (abs >= 1_000) body = `${(abs / 1_000).toFixed(1)}K`
  else body = abs.toFixed(0)
  const out = `${currency} ${body}`
  return v < 0 ? `(${out})` : out
}

function formatMetric(key: string, v: number | null | undefined, unit: Unit = 'raw'): string {
  if (v === null || v === undefined || Number.isNaN(v)) return 'n/a'
  if (key.includes('margin') || key.startsWith('return_') || key.endsWith('_to_debt') || key.endsWith('_yoy') || key.includes('_growth')) {
    return `${(v * 100).toFixed(1)}%`
  }
  if (key.includes('days')) return `${v.toFixed(0)} days`
  if (key === 'revenue' || key === 'gross_profit' || key === 'ebitda' || key === 'ebit' || key === 'pat') {
    return formatValue(v, unit)
  }
  return `${v.toFixed(2)}x`
}

function blockPathForApi(block: FinancialsBlock): string {
  const path = block.json || ''
  return block.source_id === 'merged' && path.startsWith('merged/') ? path.slice('merged/'.length) : path
}

function firstProvenancePage(row: StatementRow): number | undefined {
  if (row.page) return row.page
  const first = Object.values(row.provenance || {}).find((p) => p?.page)
  return first?.page
}

function firstProvenanceSource(row: StatementRow): string | undefined {
  if (row.page && !row.provenance) return undefined
  const first = Object.values(row.provenance || {}).find((p) => p?.source_id)
  return first?.source_id
}

// ---- KPI strip ------------------------------------------------------------

type KpiSpec = {
  key: string
  label: string
  derive: (raw: Record<string, number | null>) => number | null
  yoyKey?: string
}

const KPI_SPECS: KpiSpec[] = [
  { key: 'revenue', label: 'Revenue', derive: (r) => r.revenue ?? null, yoyKey: 'revenue_growth_yoy' },
  { key: 'ebitda',  label: 'EBITDA',  derive: (r) => r.ebitda  ?? null, yoyKey: 'ebitda_growth_yoy' },
  { key: 'pat',     label: 'PAT',     derive: (r) => r.pat     ?? null, yoyKey: 'pat_growth_yoy' },
  { key: 'cash',    label: 'Cash',    derive: (r) => r.cash    ?? null },
  {
    key: 'net_debt',
    label: 'Net debt',
    derive: (r) => {
      const d = (r.short_term_debt ?? 0) + (r.long_term_debt ?? 0)
      const c = r.cash ?? 0
      const v = d - c
      return Number.isFinite(v) ? v : null
    },
  },
]

function KpiTile({
  label, latest, yoy, history, currency,
}: {
  label: string
  latest: number | null
  yoy: number | null | undefined
  history: { fy: string; value: number | null }[]
  currency: string
}) {
  const yoyClass =
    yoy === null || yoy === undefined ? 'kpi-yoy-flat'
    : yoy > 0.005 ? 'kpi-yoy-up'
    : yoy < -0.005 ? 'kpi-yoy-down'
    : 'kpi-yoy-flat'
  const yoyArrow = yoy === null || yoy === undefined ? '—' : yoy > 0.005 ? '↑' : yoy < -0.005 ? '↓' : '→'
  const yoyText = yoy === null || yoy === undefined ? '' : `${(yoy * 100).toFixed(1)}% YoY`
  return (
    <div className="kpi-tile">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{formatCompactValue(latest, currency)}</div>
      <div className={`kpi-yoy ${yoyClass}`}>
        <span className="kpi-yoy-arrow">{yoyArrow}</span> {yoyText}
      </div>
      <Sparkbar history={history} currency={currency} />
    </div>
  )
}

function Sparkbar({ history, currency }: { history: { fy: string; value: number | null }[]; currency: string }) {
  const values = history.map((h) => h.value).filter((v): v is number => v !== null && Number.isFinite(v))
  if (values.length === 0) return <div className="kpi-spark-empty" />
  const max = Math.max(...values.map(Math.abs))
  if (max === 0) return <div className="kpi-spark-empty" />
  return (
    <div className="kpi-spark">
      {history.map((h, i) => {
        const v = h.value
        if (v === null) {
          return <div key={i} className="kpi-spark-bar kpi-spark-bar-empty" title={`${h.fy} · no data`} />
        }
        const height = (Math.abs(v) / max) * 100
        const neg = v < 0
        return (
          <div
            key={i}
            className={`kpi-spark-bar ${neg ? 'neg' : ''}`}
            style={{ height: `${Math.max(6, height)}%` }}
            title={`${h.fy} · ${formatCompactValue(v, currency)}`}
          >
            <span className="kpi-spark-fy">{h.fy.replace('FY', '')}</span>
          </div>
        )
      })}
    </div>
  )
}

function KpiStrip({ analytics }: { analytics: FinancialAnalytics | null }) {
  if (!analytics || !analytics.fys.length) {
    return (
      <div className="kpi-strip kpi-strip-empty">
        <p>Run analysis to see KPI snapshots.</p>
      </div>
    )
  }
  const latestFy = analytics.fys[0]
  const latest = analytics.by_fy?.[latestFy]?.raw || {}
  const currency = (analytics.entity?.currency as string) || analytics.by_fy?.[latestFy]?.currency || 'SGD'
  const fysSorted = [...analytics.fys].reverse() // oldest → newest for sparkline left-to-right
  return (
    <div className="kpi-strip">
      {KPI_SPECS.map((spec) => {
        const value = spec.derive(latest)
        const history = fysSorted.map((fy) => ({ fy, value: spec.derive(analytics.by_fy?.[fy]?.raw || {}) }))
        const yoy = spec.yoyKey ? analytics.trends?.[spec.yoyKey] : null
        return (
          <KpiTile
            key={spec.key}
            label={spec.label}
            latest={value}
            yoy={yoy}
            history={history}
            currency={currency}
          />
        )
      })}
    </div>
  )
}

// ---- Statement table (re-used in Spreads tab + Source Evidence drawer) ----

type DisplayMode = 'value' | 'common_size'

function StatementTable({
  block, unit, mode = 'value', denominators, onRowClick, onNoteClick,
}: {
  block: StatementBlock
  unit: Unit
  mode?: DisplayMode
  denominators?: Record<string, number | null>
  onRowClick: (row: StatementRow) => void
  onNoteClick: (row: StatementRow) => void
}) {
  const renderCell = (rawVal: number | null | undefined, fy: string) => {
    if (mode === 'common_size') {
      const d = denominators?.[fy]
      if (rawVal === null || rawVal === undefined || !d || d === 0) return ''
      const pct = (rawVal / d) * 100
      const abs = Math.abs(pct)
      const formatted = abs.toFixed(abs >= 100 ? 0 : 1)
      return `${pct < 0 ? '(' + formatted : formatted}${pct < 0 ? ')' : ''}%`
    }
    return formatValue(rawVal, unit)
  }
  return (
    <table className="stmt-table">
      <thead>
        <tr>
          <th className="stmt-label">Particulars</th>
          {block.fys.map((fy) => <th key={fy} className="stmt-fy">{fy}</th>)}
          <th className="stmt-note">Note</th>
          <th className="stmt-page">Ref</th>
        </tr>
      </thead>
      <tbody>
        {block.rows.map((r, i) => {
          const indentPx = 12 + r.indent_level * 16
          let cls = `stmt-row stmt-${r.row_type}`
          const flags = r.flags || []
          if (flags.includes('ocr')) cls += ' stmt-ocr'
          if (flags.includes('unmapped_label')) cls += ' stmt-unmapped'
          if (r.row_type === 'section_header') {
            return (
              <tr key={i} className={cls}>
                <th colSpan={block.fys.length + 3} style={{ paddingLeft: indentPx }}>
                  {r.label}
                </th>
              </tr>
            )
          }
          return (
            <tr key={i} className={cls} onClick={() => onRowClick(r)} title="Click to open source evidence">
              <td style={{ paddingLeft: indentPx }}>{r.label}</td>
              {block.fys.map((fy) => (
                <td key={fy} className="stmt-num">{renderCell(r.values[fy], fy)}</td>
              ))}
              <td className="stmt-note">
                {r.note_ref ? (
                  <button
                    className="stmt-note-link"
                    onClick={(e) => { e.stopPropagation(); onNoteClick(r) }}
                    title="Open related note"
                  >
                    {r.note_ref}
                  </button>
                ) : ''}
              </td>
              <td className="stmt-page">{firstProvenancePage(r) ? `p.${firstProvenancePage(r)}` : ''}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// Compute the right denominator per FY column for common-size mode.
function commonSizeDenominators(
  block: StatementBlock,
  analytics: FinancialAnalytics | null,
): Record<string, number | null> {
  const out: Record<string, number | null> = {}
  let denomCode: string | null = null
  if (block.statement === 'sofp') denomCode = 'bs_total_assets'
  else if (block.statement === 'soci') denomCode = 'pl_revenue'
  // For socf we use revenue from analytics, so denomCode stays null and we fall through below.

  if (denomCode) {
    const row = block.rows.find((r) => r.canonical_code === denomCode)
    block.fys.forEach((fy) => { out[fy] = row?.values?.[fy] ?? null })
  } else {
    block.fys.forEach((fy) => { out[fy] = analytics?.by_fy?.[fy]?.raw?.revenue ?? null })
  }
  return out
}

// ---- Source Evidence drawer (opens on demand) -----------------------------

type EvidenceContext = {
  sourceId: string
  statement: StatementKind
  perimeter: string
  page?: number
  noteRef?: string
}

function SourceEvidenceDrawer({
  open, ctx, caseId, index, onClose,
}: {
  open: boolean
  ctx: EvidenceContext | null
  caseId: string
  index: FinancialsIndex
  onClose: () => void
}) {
  const [activeNote, setActiveNote] = useState<FinancialsBlock | null>(null)
  const [activeNoteMd, setActiveNoteMd] = useState('')
  const [statementBlock, setStatementBlock] = useState<StatementBlock | null>(null)
  const [pdfPage, setPdfPage] = useState<number | undefined>(undefined)
  const sourceId = ctx?.sourceId || ''
  const statement = ctx?.statement || 'sofp'
  const perimeter = ctx?.perimeter || 'company'

  const currentSource = useMemo(
    () => index.sources.find((s) => s.source_id === sourceId) || null,
    [index, sourceId],
  )

  const tableBlock = useMemo<FinancialsBlock | null>(() => {
    if (!sourceId) return null
    return index.blocks.find(
      (b) => b.source_id === sourceId && b.kind === 'table'
        && b.statement === statement && b.perimeter === perimeter,
    ) || null
  }, [index, sourceId, statement, perimeter])

  const narrativeBlocks = useMemo(
    () => index.blocks.filter((b) => b.source_id === sourceId && (b.kind === 'narrative' || b.kind === 'note')),
    [index, sourceId],
  )

  useEffect(() => {
    if (!open) return
    setPdfPage(ctx?.page)
    setActiveNote(null)
    if (ctx?.noteRef) {
      const local = narrativeBlocks.find(
        (nb) => String(nb.note_no || '').toLowerCase() === String(ctx.noteRef).toLowerCase(),
      )
      if (local) {
        setActiveNote(local)
        if (local.pages?.[0]) setPdfPage(local.pages[0])
      }
    }
  }, [open, ctx, narrativeBlocks])

  useEffect(() => {
    if (!open || !tableBlock?.json) { setStatementBlock(null); return }
    getStatementBlock(caseId, tableBlock.source_id, blockPathForApi(tableBlock))
      .then(setStatementBlock).catch(() => setStatementBlock(null))
  }, [open, caseId, tableBlock])

  useEffect(() => {
    if (!activeNote?.md) { setActiveNoteMd(''); return }
    getNarrativeMarkdown(caseId, activeNote.source_id, activeNote.md)
      .then(setActiveNoteMd)
      .catch((e) => setActiveNoteMd(`Failed to load: ${e}`))
  }, [caseId, activeNote])

  if (!open || !sourceId) return null

  return (
    <div className="evidence-drawer-wrap" onClick={onClose}>
      <div className="evidence-drawer" onClick={(e) => e.stopPropagation()}>
        <div className="evidence-head">
          <div>
            <strong>{currentSource?.entity || currentSource?.original_filename}</strong>
            <span className="evidence-head-meta">
              {' · '}{currentSource?.framework} · {currentSource?.audited ? 'Audited' : 'Unaudited'}
              {' · FY'}{currentSource?.fys[0]?.slice(2)}
              {(currentSource?.fys.length || 0) > 1 ? `–${currentSource?.fys[currentSource.fys.length - 1].slice(2)}` : ''}
            </span>
          </div>
          <button className="evidence-close" onClick={onClose} title="Close">×</button>
        </div>
        <div className="evidence-body">
          <div className="evidence-left">
            <iframe
              key={`${sourceId}-${pdfPage || 'all'}`}
              title="Source PDF"
              src={pdfUrl(caseId, sourceId, pdfPage)}
              className="evidence-pdf"
            />
            {pdfPage && <div className="evidence-pdf-meta">p.{pdfPage}</div>}
          </div>
          <div className="evidence-right">
            <div className="fin-tabs">
              {(['sofp', 'soci', 'socf'] as StatementKind[]).map((k) => (
                <button
                  key={k}
                  className={`fin-tab ${statement === k ? 'active' : ''}`}
                  onClick={() => {
                    setActiveNote(null)
                    if (ctx) ctx.statement = k
                  }}
                  // Read-only switching here would need props; for v1 the drawer follows the row click.
                  disabled
                >{STATEMENT_LABELS[k]}</button>
              ))}
              {narrativeBlocks.length > 0 && (
                <button
                  className={`fin-tab ${activeNote ? 'active' : ''}`}
                  onClick={() => setActiveNote(narrativeBlocks[0])}
                >Notes & Reports</button>
              )}
            </div>
            {activeNote ? (
              <div className="fin-narrative-wrap">
                <div className="fin-note-list">
                  {narrativeBlocks.map((nb, i) => (
                    <button
                      key={i}
                      className={`note-item ${activeNote?.md === nb.md ? 'active' : ''}`}
                      onClick={() => { setActiveNote(nb); if (nb.pages?.[0]) setPdfPage(nb.pages[0]) }}
                    >
                      <div className="note-title">{nb.note_no ? `Note ${nb.note_no}. ` : ''}{nb.title}</div>
                      <div className="note-meta">{nb.subkind} · p.{nb.pages?.[0] || '?'}</div>
                    </button>
                  ))}
                </div>
                <div className="fin-note-body markdown-body">
                  <ReactMarkdown>{activeNoteMd || '*Loading…*'}</ReactMarkdown>
                </div>
              </div>
            ) : statementBlock ? (
              <StatementTable
                block={statementBlock}
                unit="raw"
                onRowClick={(r) => setPdfPage(firstProvenancePage(r))}
                onNoteClick={(r) => {
                  if (!r.note_ref) return
                  const local = narrativeBlocks.find(
                    (nb) => String(nb.note_no || '').toLowerCase() === String(r.note_ref).toLowerCase(),
                  )
                  if (local) setActiveNote(local)
                }}
              />
            ) : (
              <p style={{ color: '#64748b' }}>No {STATEMENT_LABELS[statement]} for this source.</p>
            )}
            {tableBlock?.csv && (
              <a
                className="fin-download"
                href={csvUrl(caseId, tableBlock.source_id, tableBlock.csv)}
                download
              >⬇ Download CSV</a>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// ---- Tab content ----------------------------------------------------------

function SpreadsTab({
  mergedBlocks, mergedPerimeters, perimeter, statement, unit, mode,
  analytics, analysisBlock, onSetStatement, onSetPerimeter, onSetMode,
  onRowClick, onNoteClick,
}: {
  mergedBlocks: FinancialsBlock[]
  mergedPerimeters: string[]
  perimeter: string
  statement: StatementKind
  unit: Unit
  mode: DisplayMode
  analytics: FinancialAnalytics | null
  analysisBlock: StatementBlock | null
  onSetStatement: (k: StatementKind) => void
  onSetPerimeter: (p: string) => void
  onSetMode: (m: DisplayMode) => void
  onRowClick: (row: StatementRow) => void
  onNoteClick: (row: StatementRow) => void
}) {
  const denominators = useMemo(
    () => (mode === 'common_size' && analysisBlock ? commonSizeDenominators(analysisBlock, analytics) : undefined),
    [mode, analysisBlock, analytics],
  )
  const denomLabel = statement === 'sofp' ? 'Total Assets'
                     : statement === 'soci' ? 'Revenue'
                     : 'Revenue'
  return (
    <div className="tab-content">
      <div className="spreads-controls">
        <div className="fin-tabs sub-tabs">
          {(['sofp', 'soci', 'socf'] as StatementKind[]).map((k) => (
            <button
              key={k}
              className={`fin-tab ${statement === k ? 'active' : ''}`}
              onClick={() => onSetStatement(k)}
            >{STATEMENT_LABELS[k]}</button>
          ))}
        </div>
        <div className="spreads-right-controls">
          <div className="fin-segmented">
            <button
              className={`seg ${mode === 'value' ? 'active' : ''}`}
              onClick={() => onSetMode('value')}
            >S$ values</button>
            <button
              className={`seg ${mode === 'common_size' ? 'active' : ''}`}
              onClick={() => onSetMode('common_size')}
              title={`Common-size: each line as % of ${denomLabel}`}
            >Common-size %</button>
          </div>
          {mergedPerimeters.length > 1 && (
            <div className="fin-segmented">
              {mergedPerimeters.map((p) => (
                <button
                  key={p}
                  className={`seg ${perimeter === p ? 'active' : ''}`}
                  onClick={() => onSetPerimeter(p)}
                >{p === 'company' ? 'Standalone' : 'Consolidated'}</button>
              ))}
            </div>
          )}
        </div>
      </div>
      {mode === 'common_size' && (
        <div className="spreads-hint">
          Common-size: each line shown as % of <strong>{denomLabel}</strong> for that FY column.
        </div>
      )}
      {analysisBlock ? (
        <StatementTable
          block={analysisBlock}
          unit={unit}
          mode={mode}
          denominators={denominators}
          onRowClick={onRowClick}
          onNoteClick={onNoteClick}
        />
      ) : (
        <div className="analysis-empty">
          {mergedBlocks.length === 0
            ? 'Merged spread will appear once all uploaded statements are approved.'
            : `No merged ${STATEMENT_LABELS[statement]} for the ${perimeter} perimeter.`}
        </div>
      )}
    </div>
  )
}

function RatioSparkline({
  values, statusValues, height = 32,
}: {
  values: (number | null)[]
  statusValues?: (PolicyStatus | null)[]
  height?: number
}) {
  const finite = values.filter((v): v is number => v !== null && Number.isFinite(v))
  if (finite.length < 2) return <div className="ratio-spark-empty" style={{ height }} />
  const min = Math.min(...finite)
  const max = Math.max(...finite)
  const range = max - min || Math.abs(max) || 1
  const padded = range * 0.1
  const lo = min - padded
  const hi = max + padded
  const W = 100
  const H = height
  const step = W / (values.length - 1)
  const points = values.map((v, i) => {
    if (v === null || !Number.isFinite(v)) return null
    const y = H - ((v - lo) / (hi - lo)) * H
    return { x: i * step, y, v, status: statusValues?.[i] || null }
  })
  const path = points
    .map((p, i) => p ? `${i === 0 || !points[i - 1] ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}` : '')
    .filter(Boolean).join(' ')
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="ratio-spark-svg" preserveAspectRatio="none" width="100%" height={height}>
      <path d={path} fill="none" stroke="#1f3a5f" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      {points.map((p, i) => p && (
        <circle
          key={i}
          cx={p.x}
          cy={p.y}
          r="2.2"
          className={`ratio-spark-dot ratio-spark-dot-${p.status || 'neutral'}`}
        />
      ))}
    </svg>
  )
}

function RatioCard({ rkey, label, analytics }: { rkey: string; label: string; analytics: FinancialAnalytics }) {
  const fys = analytics.fys
  const fysAsc = [...fys].reverse()
  const valuesAsc = fysAsc.map((fy) => analytics.by_fy?.[fy]?.ratios?.[rkey] ?? null)
  const statusesAsc = valuesAsc.map((v) => policyStatus(rkey, v))
  const latest = analytics.by_fy?.[fys[0]]?.ratios?.[rkey] ?? null
  const prior = fys.length > 1 ? analytics.by_fy?.[fys[1]]?.ratios?.[rkey] ?? null : null
  const status = policyStatus(rkey, latest)
  const yoy = (latest !== null && latest !== undefined && prior !== null && prior !== undefined && prior !== 0)
    ? (latest - prior) / Math.abs(prior)
    : null
  const yoyArrow = yoy === null ? '' : yoy > 0.005 ? '↑' : yoy < -0.005 ? '↓' : '→'
  const yoyClass = yoy === null ? 'kpi-yoy-flat' : yoy > 0.005 ? 'kpi-yoy-up' : yoy < -0.005 ? 'kpi-yoy-down' : 'kpi-yoy-flat'
  const policy = RATIO_POLICY[rkey]

  return (
    <div className={`ratio-card ratio-card-${status || 'neutral'}`}>
      <div className="ratio-card-head">
        <div className="ratio-card-label">{label}</div>
        {status && (
          <div className={`ratio-card-pill ratio-pill-${status}`}>
            {status === 'pass' ? 'Pass' : status === 'watch' ? 'Watch' : 'Risk'}
          </div>
        )}
      </div>
      <div className="ratio-card-value">
        {formatMetric(rkey, latest)}
        {yoy !== null && (
          <span className={`ratio-card-yoy ${yoyClass}`}>
            {yoyArrow} {Math.abs(yoy * 100).toFixed(1)}%
          </span>
        )}
      </div>
      <RatioSparkline values={valuesAsc} statusValues={statusesAsc} />
      <div className="ratio-card-footer">
        <span className="ratio-card-fy-bar">
          {fysAsc.map((fy) => <span key={fy} className="ratio-card-fy">{fy.replace('FY', "'")}</span>)}
        </span>
        {policy && <span className="ratio-card-policy">Policy {policyLabel(rkey)}</span>}
      </div>
    </div>
  )
}

function RatiosTab({ analytics }: { analytics: FinancialAnalytics | null }) {
  if (!analytics || !analytics.fys.length) {
    return <div className="analysis-empty">Run analysis to see ratios.</div>
  }
  return (
    <div className="tab-content">
      {RATIO_GROUPS.map((group) => (
        <div className="ratio-group" key={group.title}>
          <h3 className="ratio-group-title">{group.title}</h3>
          <div className="ratio-group-grid">
            {group.keys.map((key) => (
              <RatioCard
                key={key}
                rkey={key}
                label={RATIO_LABELS[key] || key}
                analytics={analytics}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

// ---- Cash Flow & Runway tab -----------------------------------------------

type BridgeStep = { label: string; value: number; cumulative: number; kind: 'positive' | 'negative' | 'subtotal' | 'total' }

function buildCashBridge(raw: Record<string, number | null>): BridgeStep[] {
  const pbt = raw.pbt ?? 0
  const da = (raw.depreciation ?? 0) + (raw.amortisation ?? 0)
  const tax = raw.tax_paid ?? raw.tax ?? 0
  const cfo = raw.cfo
  // ΔWC = CFO - PBT - D&A + tax_paid  (Indirect-method reconciliation)
  const dWc = cfo !== null && cfo !== undefined ? cfo - pbt - da + tax : 0
  const capex = Math.abs(raw.capex ?? 0)
  const fcf = (cfo ?? 0) - capex

  let cum = 0
  const steps: BridgeStep[] = []
  const push = (label: string, v: number, kind: BridgeStep['kind']) => {
    cum = kind === 'subtotal' || kind === 'total' ? v : cum + v
    steps.push({ label, value: v, cumulative: cum, kind })
  }
  push('PBT', pbt, pbt >= 0 ? 'positive' : 'negative')
  push('+ D&A', da, 'positive')
  push('Δ WC', dWc, dWc >= 0 ? 'positive' : 'negative')
  push('− Tax paid', -Math.abs(tax), 'negative')
  if (cfo !== null && cfo !== undefined) push('= CFO', cfo, 'subtotal')
  push('− Capex', -capex, 'negative')
  push('= FCF', fcf, 'total')
  return steps
}

function CashBridge({ steps, currency }: { steps: BridgeStep[]; currency: string }) {
  if (steps.length === 0) return null
  const allVals = steps.flatMap((s) => [s.value, s.cumulative])
  const max = Math.max(...allVals.map(Math.abs)) || 1
  return (
    <div className="cash-bridge">
      {steps.map((s, i) => {
        const w = (Math.abs(s.value) / max) * 100
        return (
          <div key={i} className={`bridge-row bridge-${s.kind}`}>
            <div className="bridge-label">{s.label}</div>
            <div className="bridge-bar-wrap">
              <div className="bridge-bar" style={{ width: `${Math.max(2, w)}%` }} />
            </div>
            <div className="bridge-value">{formatCompactValue(s.value, currency)}</div>
          </div>
        )
      })}
    </div>
  )
}

function RunwayGauge({ months }: { months: number | null }) {
  let label = 'n/a'
  let band: 'pass' | 'watch' | 'risk' | 'neutral' = 'neutral'
  if (months === null || !Number.isFinite(months)) {
    label = 'n/a'
  } else if (months <= 0) {
    label = 'no runway'
    band = 'risk'
  } else if (months >= 24) {
    label = '24+ months'
    band = 'pass'
  } else {
    label = `${months.toFixed(1)} months`
    band = months >= 12 ? 'pass' : months >= 6 ? 'watch' : 'risk'
  }
  const pct = months === null ? 0 : Math.min(100, Math.max(0, (months / 24) * 100))
  return (
    <div className={`runway-gauge runway-${band}`}>
      <div className="runway-value">{label}</div>
      <div className="runway-bar">
        <div className="runway-bar-inner" style={{ width: `${pct}%` }} />
        <div className="runway-tick" style={{ left: '25%' }} title="6 months" />
        <div className="runway-tick" style={{ left: '50%' }} title="12 months" />
        <div className="runway-tick" style={{ left: '75%' }} title="18 months" />
      </div>
      <div className="runway-scale">
        <span>0</span><span>6m</span><span>12m</span><span>18m</span><span>24m+</span>
      </div>
    </div>
  )
}

type StressInputs = { revenueDeltaPct: number; fixedCostDeltaPct: number }

function computeStressedBurn(raw: Record<string, number | null>, s: StressInputs): { monthlyBurn: number; runwayMonths: number | null } {
  const revenue = raw.revenue ?? 0
  const pat = raw.pat ?? 0
  // Estimate of fixed costs proxied as revenue - gross_profit  (i.e. cost of sales),
  // plus PAT loss — kept intentionally simple for a v1 stress sketch.
  const cogs = (raw.cost_of_sales ?? (revenue - (raw.gross_profit ?? revenue)))
  const fixedCostBase = Math.abs(cogs) + Math.abs(raw.other_op_exp ?? 0)
  const stressedRev = revenue * (1 + s.revenueDeltaPct / 100)
  const stressedFixedCost = fixedCostBase * (1 + s.fixedCostDeltaPct / 100)
  const stressedPat = pat - (revenue - stressedRev) - (stressedFixedCost - fixedCostBase)
  const monthlyBurn = stressedPat < 0 ? -stressedPat / 12 : 0
  const cash = raw.cash ?? 0
  const runwayMonths = monthlyBurn > 0 ? cash / monthlyBurn : null
  return { monthlyBurn, runwayMonths }
}

function CashFlowTab({ analytics }: { analytics: FinancialAnalytics | null }) {
  const [stress, setStress] = useState<StressInputs>({ revenueDeltaPct: 0, fixedCostDeltaPct: 0 })
  if (!analytics || analytics.fys.length === 0) {
    return <div className="analysis-empty">Run analysis to see cash flow & runway view.</div>
  }
  const latestFy = analytics.fys[0]
  const raw = analytics.by_fy?.[latestFy]?.raw || {}
  const currency = (analytics.entity?.currency as string) || analytics.by_fy?.[latestFy]?.currency || 'SGD'
  const steps = buildCashBridge(raw)

  // Baseline burn / runway (no stress)
  const baseline = computeStressedBurn(raw, { revenueDeltaPct: 0, fixedCostDeltaPct: 0 })
  const stressed = computeStressedBurn(raw, stress)

  return (
    <div className="tab-content">
      <div className="cashflow-grid">
        <div className="analysis-card">
          <h3>Cash bridge — {latestFy}</h3>
          <p style={{ fontSize: '0.78rem', color: '#64748b', marginBottom: '0.5rem' }}>
            Indirect-method reconciliation. Each step shows the period contribution; CFO and FCF are running subtotals.
          </p>
          <CashBridge steps={steps} currency={currency} />
        </div>

        <div className="analysis-card">
          <h3>Cash runway</h3>
          <p style={{ fontSize: '0.78rem', color: '#64748b', marginBottom: '0.4rem' }}>
            Cash on hand divided by current monthly cash burn.
          </p>
          <RunwayGauge months={baseline.runwayMonths} />
          <div className="runway-meta">
            <div><span>Cash</span><strong>{formatCompactValue(raw.cash, currency)}</strong></div>
            <div><span>Monthly burn</span><strong>{baseline.monthlyBurn > 0 ? formatCompactValue(baseline.monthlyBurn, currency) : '—'}</strong></div>
            <div><span>CFO / yr</span><strong>{formatCompactValue(raw.cfo, currency)}</strong></div>
            <div><span>FCF / yr</span><strong>{formatCompactValue(raw.fcf, currency)}</strong></div>
          </div>
        </div>
      </div>

      <div className="analysis-card">
        <h3>Stress studio</h3>
        <p style={{ fontSize: '0.78rem', color: '#64748b', marginBottom: '0.6rem' }}>
          Move the sliders to model a downside scenario. Runway re-computes live.
        </p>
        <div className="stress-row">
          <label>Revenue change</label>
          <input
            type="range" min={-50} max={20} step={5}
            value={stress.revenueDeltaPct}
            onChange={(e) => setStress((s) => ({ ...s, revenueDeltaPct: Number(e.target.value) }))}
          />
          <span className="stress-value">{stress.revenueDeltaPct >= 0 ? '+' : ''}{stress.revenueDeltaPct}%</span>
        </div>
        <div className="stress-row">
          <label>Fixed-cost change</label>
          <input
            type="range" min={-20} max={50} step={5}
            value={stress.fixedCostDeltaPct}
            onChange={(e) => setStress((s) => ({ ...s, fixedCostDeltaPct: Number(e.target.value) }))}
          />
          <span className="stress-value">{stress.fixedCostDeltaPct >= 0 ? '+' : ''}{stress.fixedCostDeltaPct}%</span>
        </div>
        <div className="stress-result">
          <RunwayGauge months={stressed.runwayMonths} />
          <div className="runway-meta">
            <div><span>Stressed monthly burn</span><strong>{stressed.monthlyBurn > 0 ? formatCompactValue(stressed.monthlyBurn, currency) : '—'}</strong></div>
            <div><span>Stressed runway</span><strong>{stressed.runwayMonths === null ? '24+ months' : `${stressed.runwayMonths.toFixed(1)} months`}</strong></div>
          </div>
        </div>
      </div>
    </div>
  )
}

// ---- Risk View tab --------------------------------------------------------

type RiskSeverity = 'high' | 'medium' | 'low' | 'info'
type RiskFinding = {
  severity: RiskSeverity
  source: 'fs-agent' | 'qualitative' | 'auto' | 'extraction'
  title: string
  detail?: string
  evidence?: string
}

const SEVERITY_ORDER: Record<RiskSeverity, number> = { high: 0, medium: 1, low: 2, info: 3 }

function autoDetectFindings(analytics: FinancialAnalytics): RiskFinding[] {
  const out: RiskFinding[] = []
  const latestFy = analytics.fys[0]
  const raw = analytics.by_fy?.[latestFy]?.raw || {}
  const ratios = analytics.by_fy?.[latestFy]?.ratios || {}
  const trends = analytics.trends || {}

  if (raw.total_equity !== null && raw.total_equity !== undefined && raw.total_equity < 0) {
    out.push({ severity: 'high', source: 'auto', title: 'Negative equity (capital deficiency)',
      detail: 'Total equity is negative — the entity is technically insolvent on a book basis.',
      evidence: `Total equity (${latestFy}) = ${formatValue(raw.total_equity, 'raw')}` })
  }
  if (raw.cfo !== null && raw.cfo !== undefined && raw.cfo < 0) {
    out.push({ severity: 'high', source: 'auto', title: 'Negative operating cash flow',
      detail: 'Operations are consuming cash; cash burn must be funded externally.',
      evidence: `CFO (${latestFy}) = ${formatValue(raw.cfo, 'raw')}` })
  }
  if (ratios.interest_coverage !== null && ratios.interest_coverage !== undefined && ratios.interest_coverage < 1) {
    out.push({ severity: 'high', source: 'auto', title: 'Interest cover below 1x',
      detail: 'EBIT does not cover interest expense — debt service depends on existing cash or refinancing.',
      evidence: `Interest cover = ${formatMetric('interest_coverage', ratios.interest_coverage)}` })
  }
  if (ratios.current_ratio !== null && ratios.current_ratio !== undefined && ratios.current_ratio < 0.7) {
    out.push({ severity: 'high', source: 'auto', title: 'Liquidity strain (current ratio < 0.7)',
      detail: 'Short-term obligations may exceed liquid resources.',
      evidence: `Current ratio = ${formatMetric('current_ratio', ratios.current_ratio)}` })
  }
  if (ratios.debt_ebitda !== null && ratios.debt_ebitda !== undefined && ratios.debt_ebitda > 6) {
    out.push({ severity: 'high', source: 'auto', title: 'High leverage (Debt / EBITDA > 6x)',
      detail: 'Leverage is well above policy maximum.',
      evidence: `Debt / EBITDA = ${formatMetric('debt_ebitda', ratios.debt_ebitda)}` })
  }
  if (raw.pat !== null && raw.pat !== undefined && raw.pat < 0) {
    out.push({ severity: 'medium', source: 'auto', title: 'Net loss',
      detail: 'The entity posted a loss for the year.',
      evidence: `PAT (${latestFy}) = ${formatValue(raw.pat, 'raw')}` })
  }
  if (raw.fcf !== null && raw.fcf !== undefined && raw.fcf < 0 && raw.cfo !== undefined && raw.cfo !== null && raw.cfo >= 0) {
    out.push({ severity: 'medium', source: 'auto', title: 'Negative free cash flow despite positive CFO',
      detail: 'Capex is outstripping operating cash generation.',
      evidence: `CFO = ${formatValue(raw.cfo, 'raw')}, Capex = ${formatValue(raw.capex, 'raw')}, FCF = ${formatValue(raw.fcf, 'raw')}` })
  }
  if (trends.revenue_growth_yoy !== null && trends.revenue_growth_yoy !== undefined && Math.abs(trends.revenue_growth_yoy) >= 0.20) {
    const dir = trends.revenue_growth_yoy > 0 ? 'growth' : 'decline'
    const sev: RiskSeverity = trends.revenue_growth_yoy < -0.20 ? 'high' : 'medium'
    out.push({ severity: sev, source: 'auto', title: `Material revenue ${dir} (${(trends.revenue_growth_yoy * 100).toFixed(0)}% YoY)`,
      detail: 'Material top-line move warrants drivers analysis.',
      evidence: `Revenue YoY = ${(trends.revenue_growth_yoy * 100).toFixed(1)}%` })
  }
  // Runway < 3 months
  if (raw.cash !== null && raw.cash !== undefined && raw.pat !== null && raw.pat !== undefined && raw.pat < 0) {
    const monthlyBurn = Math.abs(raw.pat) / 12
    if (monthlyBurn > 0) {
      const runway = raw.cash / monthlyBurn
      if (runway < 3) {
        out.push({ severity: 'high', source: 'auto', title: 'Cash runway under 3 months',
          detail: 'Current cash will not cover the next quarter at the existing burn rate.',
          evidence: `Cash ${formatValue(raw.cash, 'raw')} / monthly burn ${formatValue(monthlyBurn, 'raw')} = ${runway.toFixed(1)} months` })
      }
    }
  }
  return out
}

function findingsFromAgents(assessment: any): RiskFinding[] {
  const out: RiskFinding[] = []
  const fs = assessment?.full_results?.fs?.memo || {}
  for (const rf of (fs.red_flags || [])) {
    out.push({ severity: 'high', source: 'fs-agent', title: rf.message || 'FS red flag',
      detail: rf.detail || rf.evidence?.message,
      evidence: Array.isArray(rf.evidence) ? JSON.stringify(rf.evidence) : '' })
  }
  for (const wo of (fs.watchouts || [])) {
    out.push({ severity: 'medium', source: 'fs-agent', title: wo.message || 'FS watchout',
      detail: wo.detail,
      evidence: Array.isArray(wo.evidence) ? JSON.stringify(wo.evidence) : '' })
  }
  const q = assessment?.full_results?.qualitative?.memo || {}
  for (const probe of (q.probes || q.underwriter_questions || [])) {
    const text = typeof probe === 'string' ? probe : (probe.question || probe.message || JSON.stringify(probe))
    out.push({ severity: 'info', source: 'qualitative', title: text, detail: probe.rationale })
  }
  return out
}

function findingsFromReviewFlags(analytics: FinancialAnalytics): RiskFinding[] {
  return (analytics.review_flags || []).map((flag) => ({
    severity: (flag.severity as RiskSeverity) || 'info',
    source: 'extraction',
    title: flag.message || flag.label || flag.code || 'Extraction note',
    detail: flag.source ? `Source: ${flag.source}` : undefined,
  }))
}

function RiskCard({ f }: { f: RiskFinding }) {
  const sevLabel = f.severity === 'high' ? 'High' : f.severity === 'medium' ? 'Watch' : f.severity === 'low' ? 'Low' : 'Info'
  const srcLabel = f.source === 'fs-agent' ? 'FS agent'
                  : f.source === 'qualitative' ? 'Qualitative'
                  : f.source === 'auto' ? 'Auto-detected'
                  : 'Extraction'
  return (
    <div className={`risk-card risk-${f.severity}`}>
      <div className="risk-card-head">
        <span className={`risk-pill risk-pill-${f.severity}`}>{sevLabel}</span>
        <span className="risk-source">{srcLabel}</span>
      </div>
      <div className="risk-title">{f.title}</div>
      {f.detail && <div className="risk-detail">{f.detail}</div>}
      {f.evidence && <div className="risk-evidence">{f.evidence}</div>}
    </div>
  )
}

function RiskTab({ analytics, assessment }: { analytics: FinancialAnalytics | null; assessment: any | null }) {
  if (!analytics) {
    return <div className="analysis-empty">Run analysis to see the risk view.</div>
  }
  const findings: RiskFinding[] = [
    ...autoDetectFindings(analytics),
    ...findingsFromAgents(assessment),
    ...findingsFromReviewFlags(analytics),
  ]
  findings.sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity])
  const counts = { high: 0, medium: 0, low: 0, info: 0 }
  findings.forEach((f) => { counts[f.severity] = (counts[f.severity] || 0) + 1 })

  return (
    <div className="tab-content">
      <div className="risk-summary">
        <span className="risk-summary-chip risk-pill-high">High {counts.high}</span>
        <span className="risk-summary-chip risk-pill-medium">Watch {counts.medium}</span>
        <span className="risk-summary-chip risk-pill-low">Low {counts.low}</span>
        <span className="risk-summary-chip risk-pill-info">Info {counts.info}</span>
        {!assessment && (
          <span className="risk-summary-help">
            ⓘ Run analysis to surface FS-agent + Qualitative-agent findings here as well.
          </span>
        )}
      </div>
      {findings.length === 0 ? (
        <div className="analysis-empty">No risk findings detected.</div>
      ) : (
        <div className="risk-list">
          {findings.map((f, i) => <RiskCard key={i} f={f} />)}
        </div>
      )}
    </div>
  )
}

// ---- Report buttons -------------------------------------------------------

function ReportButtons({
  caseId, analyticsLoaded, status, error, onGenerate,
}: {
  caseId: string
  analyticsLoaded: boolean
  status: ReportStatus | null
  error: string
  onGenerate: () => void
}) {
  const running = status?.status === 'queued' || status?.status === 'running'
  const completed = status?.status === 'completed' || status?.report_on_disk
  const failed = status?.status === 'failed'

  if (running) {
    return (
      <div className="report-controls">
        <button className="primary fin-generate" disabled>
          Generating report…
        </button>
        <span className="fin-help">~30–60s · sections fan out in parallel</span>
      </div>
    )
  }

  return (
    <div className="report-controls">
      {completed && (
        <a
          className="primary fin-generate fin-generate-download"
          href={reportDocxUrl(caseId)}
          download
        >
          ⬇ Download Report (.docx)
        </a>
      )}
      <button
        className="primary fin-generate"
        disabled={!analyticsLoaded}
        onClick={onGenerate}
        title={!analyticsLoaded ? 'Analytics not loaded — run /analyze first' : undefined}
        style={completed ? { background: '#e2e8f0', color: '#1f3a5f' } : undefined}
      >
        {completed ? 'Re-generate' : 'Generate Credit Report'}
      </button>
      {failed && status?.error && (
        <span className="fin-help" style={{ color: '#991b1b' }}>
          Failed: {status.error.slice(0, 120)}
        </span>
      )}
      {error && (
        <span className="fin-help" style={{ color: '#991b1b' }}>{error}</span>
      )}
    </div>
  )
}


// ---- Main page ------------------------------------------------------------

export default function Financials() {
  const { caseId } = useParams<{ caseId: string }>()
  const [index, setIndex] = useState<FinancialsIndex | null>(null)
  const [analytics, setAnalytics] = useState<FinancialAnalytics | null>(null)
  const [err, setErr] = useState<string>('')
  const [tab, setTab] = useState<Tab>('spreads')
  const [analysisStatement, setAnalysisStatement] = useState<StatementKind>('sofp')
  const [analysisPerimeter, setAnalysisPerimeter] = useState<string>('company')
  const [analysisBlock, setAnalysisBlock] = useState<StatementBlock | null>(null)
  const [unit, setUnit] = useState<Unit>('raw')
  const [spreadsMode, setSpreadsMode] = useState<DisplayMode>('value')
  const [assessment, setAssessment] = useState<any | null>(null)
  const [reportStatus, setReportStatus] = useState<ReportStatus | null>(null)
  const [reportError, setReportError] = useState<string>('')
  const [evidenceCtx, setEvidenceCtx] = useState<EvidenceContext | null>(null)
  const [evidenceOpen, setEvidenceOpen] = useState(false)

  useEffect(() => {
    if (!caseId) return
    setErr('')
    getFinancialsIndex(caseId).then((idx) => {
      setIndex(idx)
      const firstMerged = idx.blocks.find((b) => b.source_id === 'merged' && b.statement === 'sofp')
      if (firstMerged?.perimeter) setAnalysisPerimeter(firstMerged.perimeter)
    }).catch((e) => setErr(String(e)))
    getFinancialAnalytics(caseId).then(setAnalytics).catch(() => setAnalytics(null))
    getAssessment(caseId).then(setAssessment).catch(() => setAssessment(null))
    getReportStatus(caseId).then(setReportStatus).catch(() => setReportStatus(null))
  }, [caseId])

  // Poll report status while a job is in flight.
  useEffect(() => {
    if (!caseId) return
    const status = reportStatus?.status
    if (status !== 'queued' && status !== 'running') return
    const t = window.setInterval(() => {
      getReportStatus(caseId).then(setReportStatus).catch(() => { /* ignore */ })
    }, 4000)
    return () => window.clearInterval(t)
  }, [caseId, reportStatus?.status])

  const handleGenerateReport = async () => {
    if (!caseId) return
    setReportError('')
    try {
      await triggerReportGeneration(caseId)
      const next = await getReportStatus(caseId)
      setReportStatus(next)
    } catch (e) {
      setReportError(String(e))
    }
  }

  const mergedBlocks = useMemo(
    () => index?.blocks.filter((b) => b.source_id === 'merged' && b.kind === 'merged_table') || [],
    [index],
  )

  const mergedPerimeters = useMemo(
    () => Array.from(new Set(mergedBlocks.map((b) => b.perimeter).filter(Boolean))) as string[],
    [mergedBlocks],
  )

  const currentMergedBlock = useMemo<FinancialsBlock | null>(() => (
    mergedBlocks.find((b) => b.statement === analysisStatement && b.perimeter === analysisPerimeter) || null
  ), [mergedBlocks, analysisStatement, analysisPerimeter])

  useEffect(() => {
    if (!caseId || !currentMergedBlock?.json) { setAnalysisBlock(null); return }
    getStatementBlock(caseId, 'merged', blockPathForApi(currentMergedBlock))
      .then(setAnalysisBlock)
      .catch(() => setAnalysisBlock(null))
  }, [caseId, currentMergedBlock])

  const onRowClick = (row: StatementRow) => {
    if (!index) return
    const sourceId = firstProvenanceSource(row)
      || index.sources.find((s) => s.block_count > 0)?.source_id
    if (!sourceId) return
    setEvidenceCtx({
      sourceId,
      statement: analysisStatement,
      perimeter: analysisPerimeter,
      page: firstProvenancePage(row),
    })
    setEvidenceOpen(true)
  }

  const onNoteClick = async (row: StatementRow) => {
    if (!caseId || !row.note_ref || !index) return
    const sourceId = firstProvenanceSource(row)
      || index.sources.find((s) => s.block_count > 0)?.source_id
    if (!sourceId) return
    let page: number | undefined
    try {
      const linked = await getLinkedNote(caseId, sourceId, row.note_ref)
      page = linked.note.page_range?.[0]
    } catch { /* ignore */ }
    setEvidenceCtx({
      sourceId,
      statement: analysisStatement,
      perimeter: analysisPerimeter,
      page: page || firstProvenancePage(row),
      noteRef: row.note_ref,
    })
    setEvidenceOpen(true)
  }

  if (err) return <div className="card"><p>Error: {err}</p></div>
  if (!index) return <p>Loading financials…</p>
  if (index.source_count === 0) {
    return <div className="card"><p>No financial sources ingested for this case yet.</p></div>
  }

  return (
    <div className="financials">
      <div className="fin-header">
        <div>
          <p className="intake-crumb">
            <Link to="/">Cases</Link> · <Link to={`/cases/${caseId}`}>Case</Link> · Financial Analysis
          </p>
          <h2>Financial Analysis</h2>
          <p style={{ color: '#64748b', fontSize: '0.85rem' }}>
            Consolidated multi-year view across all approved sources, with drill-through to per-PDF evidence.
          </p>
        </div>
        <div className="fin-header-controls">
          <div className="fin-segmented">
            {(['raw', 'thousand', 'million'] as Unit[]).map((u) => (
              <button
                key={u}
                className={`seg ${unit === u ? 'active' : ''}`}
                onClick={() => setUnit(u)}
              >{UNIT_LABELS[u]}</button>
            ))}
          </div>
          <ReportButtons
            caseId={caseId!}
            analyticsLoaded={!!analytics}
            status={reportStatus}
            error={reportError}
            onGenerate={handleGenerateReport}
          />
        </div>
      </div>

      <KpiStrip analytics={analytics} />

      <div className="fin-tabs main-tabs">
        {(['spreads', 'ratios', 'cashflow', 'risk'] as Tab[]).map((t) => (
          <button
            key={t}
            className={`fin-tab ${tab === t ? 'active' : ''}`}
            onClick={() => setTab(t)}
          >{TAB_LABELS[t]}</button>
        ))}
      </div>

      {tab === 'spreads' && (
        <SpreadsTab
          mergedBlocks={mergedBlocks}
          mergedPerimeters={mergedPerimeters}
          perimeter={analysisPerimeter}
          statement={analysisStatement}
          unit={unit}
          mode={spreadsMode}
          analytics={analytics}
          analysisBlock={analysisBlock}
          onSetStatement={setAnalysisStatement}
          onSetPerimeter={setAnalysisPerimeter}
          onSetMode={setSpreadsMode}
          onRowClick={onRowClick}
          onNoteClick={onNoteClick}
        />
      )}
      {tab === 'ratios' && <RatiosTab analytics={analytics} />}
      {tab === 'cashflow' && <CashFlowTab analytics={analytics} />}
      {tab === 'risk' && <RiskTab analytics={analytics} assessment={assessment} />}

      <SourceEvidenceDrawer
        open={evidenceOpen}
        ctx={evidenceCtx}
        caseId={caseId!}
        index={index}
        onClose={() => setEvidenceOpen(false)}
      />
    </div>
  )
}

