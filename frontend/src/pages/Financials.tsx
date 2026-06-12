import { useEffect, useMemo, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import {
  getFinancialsIndex,
  getStatementBlock,
  getNarrativeMarkdown,
  csvUrl,
  pdfUrl,
  type FinancialsIndex,
  type FinancialsBlock,
  type StatementBlock,
  type StatementRow,
} from '../api'

type StatementKind = 'sofp' | 'soci' | 'socf'

const STATEMENT_LABELS: Record<StatementKind, string> = {
  sofp: 'Balance Sheet',
  soci: 'Profit & Loss',
  socf: 'Cash Flow',
}

type Unit = 'raw' | 'thousand' | 'million'
const UNIT_DIVISORS: Record<Unit, number> = { raw: 1, thousand: 1000, million: 1_000_000 }
const UNIT_LABELS: Record<Unit, string> = { raw: '1', thousand: "'000", million: 'Mn' }

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

function StatementTable({
  block, unit, onRowClick,
}: {
  block: StatementBlock
  unit: Unit
  onRowClick: (row: StatementRow) => void
}) {
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
          if (r.flags.includes('ocr')) cls += ' stmt-ocr'
          if (r.flags.includes('unmapped_label')) cls += ' stmt-unmapped'
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
            <tr key={i} className={cls} onClick={() => onRowClick(r)} title="Click to jump to source page">
              <td style={{ paddingLeft: indentPx }}>{r.label}</td>
              {block.fys.map((fy) => (
                <td key={fy} className="stmt-num">{formatValue(r.values[fy], unit)}</td>
              ))}
              <td className="stmt-note">{r.note_ref || ''}</td>
              <td className="stmt-page">{r.page ? `p.${r.page}` : ''}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

export default function Financials() {
  const { caseId } = useParams<{ caseId: string }>()
  const [index, setIndex] = useState<FinancialsIndex | null>(null)
  const [err, setErr] = useState<string>('')
  const [sourceId, setSourceId] = useState<string>('')
  const [statement, setStatement] = useState<StatementKind>('sofp')
  const [perimeter, setPerimeter] = useState<string>('')
  const [unit, setUnit] = useState<Unit>('raw')
  const [statementBlock, setStatementBlock] = useState<StatementBlock | null>(null)
  const [activeNote, setActiveNote] = useState<FinancialsBlock | null>(null)
  const [activeNoteMd, setActiveNoteMd] = useState<string>('')
  const [pdfPage, setPdfPage] = useState<number | undefined>(undefined)

  useEffect(() => {
    if (!caseId) return
    setErr('')
    getFinancialsIndex(caseId).then((idx) => {
      setIndex(idx)
      const firstSource = idx.sources.find((s) => s.block_count > 0)
      if (firstSource) {
        setSourceId(firstSource.source_id)
        setPerimeter(firstSource.perimeters[0] || 'company')
      }
    }).catch((e) => setErr(String(e)))
  }, [caseId])

  const currentSource = useMemo(
    () => index?.sources.find((s) => s.source_id === sourceId) || null,
    [index, sourceId],
  )

  const currentTableBlock = useMemo<FinancialsBlock | null>(() => {
    if (!index || !sourceId) return null
    return index.blocks.find(
      (b) => b.source_id === sourceId && b.kind === 'table'
        && b.statement === statement && b.perimeter === perimeter,
    ) || null
  }, [index, sourceId, statement, perimeter])

  const narrativeBlocks = useMemo(
    () => index?.blocks.filter((b) => b.source_id === sourceId && (b.kind === 'narrative' || b.kind === 'note')) || [],
    [index, sourceId],
  )

  useEffect(() => {
    if (!caseId || !currentTableBlock?.json) { setStatementBlock(null); return }
    getStatementBlock(caseId, currentTableBlock.source_id, currentTableBlock.json)
      .then(setStatementBlock)
      .catch((e) => setErr(String(e)))
  }, [caseId, currentTableBlock])

  useEffect(() => {
    setActiveNoteMd('')
    if (!caseId || !activeNote?.md) return
    getNarrativeMarkdown(caseId, activeNote.source_id, activeNote.md)
      .then(setActiveNoteMd)
      .catch((e) => setActiveNoteMd(`Failed to load: ${e}`))
  }, [caseId, activeNote])

  const onRowClick = (row: StatementRow) => {
    if (row.page) setPdfPage(row.page)
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
          <h2>Financials</h2>
          <p style={{ color: '#64748b', fontSize: '0.85rem' }}>
            {index.source_count} source(s), {index.block_count} block(s)
            {' · '}<Link to={`/cases/${caseId}`}>← back to case</Link>
          </p>
        </div>
      </div>

      {/* Source picker */}
      <div className="fin-source-bar">
        {index.sources.filter((s) => s.block_count > 0).map((s) => (
          <button
            key={s.source_id}
            className={`fin-chip ${s.source_id === sourceId ? 'active' : ''}`}
            onClick={() => {
              setSourceId(s.source_id)
              setPerimeter(s.perimeters[0] || 'company')
              setActiveNote(null)
              setPdfPage(undefined)
            }}
            title={s.original_filename}
          >
            <strong>{s.entity || s.original_filename}</strong>
            <span> · {s.framework} · {s.audited ? 'Audited' : 'Unaudited'}</span>
            <span> · FY{s.fys[0]?.slice(2)}{s.fys.length > 1 ? `–${s.fys[s.fys.length - 1].slice(2)}` : ''}</span>
            <span> · {s.extraction_method.toUpperCase()}</span>
          </button>
        ))}
      </div>

      {currentSource && (
        <div className="fin-body">
          <div className="fin-left">
            {/* Tab strip: statement type */}
            <div className="fin-tabs">
              {(['sofp', 'soci', 'socf'] as StatementKind[]).map((k) => (
                <button
                  key={k}
                  className={`fin-tab ${statement === k ? 'active' : ''}`}
                  onClick={() => { setStatement(k); setActiveNote(null) }}
                >{STATEMENT_LABELS[k]}</button>
              ))}
              {(narrativeBlocks.length > 0) && (
                <button
                  className={`fin-tab ${activeNote ? 'active' : ''}`}
                  onClick={() => setActiveNote(narrativeBlocks[0])}
                >Notes & Reports</button>
              )}
            </div>

            {/* Perimeter + currency controls */}
            <div className="fin-controls">
              {currentSource.perimeters.length > 1 && (
                <div className="fin-segmented">
                  {currentSource.perimeters.map((p) => (
                    <button
                      key={p}
                      className={`seg ${perimeter === p ? 'active' : ''}`}
                      onClick={() => setPerimeter(p)}
                    >{p === 'company' ? 'Standalone' : 'Consolidated'}</button>
                  ))}
                </div>
              )}
              <div className="fin-segmented">
                {(['raw', 'thousand', 'million'] as Unit[]).map((u) => (
                  <button
                    key={u}
                    className={`seg ${unit === u ? 'active' : ''}`}
                    onClick={() => setUnit(u)}
                  >{UNIT_LABELS[u]}</button>
                ))}
              </div>
              {currentTableBlock?.csv && (
                <a
                  className="fin-download"
                  href={csvUrl(caseId!, currentTableBlock.source_id, currentTableBlock.csv)}
                  download
                >⬇ CSV</a>
              )}
            </div>

            {/* Active block render: statement table OR a narrative note */}
            {activeNote ? (
              <div className="fin-narrative-wrap">
                <div className="fin-note-list">
                  {narrativeBlocks.map((nb, i) => (
                    <button
                      key={i}
                      className={`note-item ${activeNote?.md === nb.md ? 'active' : ''}`}
                      onClick={() => { setActiveNote(nb); if (nb.pages?.[0]) setPdfPage(nb.pages[0]) }}
                    >
                      <div className="note-title">
                        {nb.note_no ? `Note ${nb.note_no}. ` : ''}{nb.title}
                      </div>
                      <div className="note-meta">
                        {nb.subkind} · p.{nb.pages?.[0] || '?'}
                      </div>
                    </button>
                  ))}
                </div>
                <div className="fin-note-body markdown-body">
                  <ReactMarkdown>{activeNoteMd || '*Loading…*'}</ReactMarkdown>
                </div>
              </div>
            ) : statementBlock ? (
              <StatementTable block={statementBlock} unit={unit} onRowClick={onRowClick} />
            ) : currentTableBlock ? (
              <p>Loading statement…</p>
            ) : (
              <p style={{ color: '#64748b' }}>
                No {STATEMENT_LABELS[statement]} block for the {perimeter} perimeter in this source.
              </p>
            )}
          </div>

          {/* Right pane: inline PDF */}
          <div className="fin-right">
            <iframe
              key={`${sourceId}-${pdfPage || 'all'}`}
              title="Source PDF"
              src={pdfUrl(caseId!, sourceId, pdfPage)}
              className="fin-pdf"
            />
            {pdfPage && (
              <div className="fin-pdf-meta">Jumped to p.{pdfPage}</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
