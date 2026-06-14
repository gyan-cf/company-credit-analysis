import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import {
  getFinancialsIndex,
  getReviewStatus,
  getSourceDocument,
  patchDocument,
  approveSource,
  rejectSource,
  resetSourceStatus,
  runAnalysis,
  pdfUrl,
  type SgFsDocument,
  type DocumentBlock,
  type FinancialsIndex,
  type FinancialsSource,
  type ReviewStatusSummary,
  type StatementRowDoc,
} from '../api'

type StatementType = 'sofp' | 'soci' | 'soce' | 'socf'
const STATEMENT_LABELS: Record<StatementType, string> = {
  sofp: 'Balance Sheet',
  soci: 'Profit & Loss',
  soce: 'Changes in Equity',
  socf: 'Cash Flow',
}

type Tab = StatementType | 'notes' | 'narrative'

function fmtNumber(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return ''
  const abs = Math.abs(v)
  const formatted = abs.toLocaleString('en-US', { maximumFractionDigits: 2 })
  return v < 0 ? `(${formatted})` : formatted
}

function parseCellInput(raw: string): number | null {
  const s = raw.trim()
  if (!s) return null
  const neg = s.startsWith('(') && s.endsWith(')')
  const cleaned = (neg ? s.slice(1, -1) : s).replace(/,/g, '').replace(/\s/g, '')
  const n = Number(cleaned)
  if (Number.isNaN(n)) return null
  return neg ? -n : n
}

export default function Review() {
  const { caseId, sourceId } = useParams<{ caseId: string; sourceId?: string }>()
  const navigate = useNavigate()
  const [index, setIndex] = useState<FinancialsIndex | null>(null)
  const [doc, setDoc] = useState<SgFsDocument | null>(null)
  const [gateSummary, setGateSummary] = useState<ReviewStatusSummary | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [analyzing, setAnalyzing] = useState(false)
  const [analyzeError, setAnalyzeError] = useState('')
  const [tab, setTab] = useState<Tab>('sofp')
  const [pdfPage, setPdfPage] = useState<number | undefined>(undefined)

  const refreshSummary = useCallback(async () => {
    if (!caseId) return
    try {
      const [idx, summary] = await Promise.all([
        getFinancialsIndex(caseId),
        getReviewStatus(caseId),
      ])
      setIndex(idx)
      setGateSummary(summary)
    } catch (e) {
      setErr(String(e))
    }
  }, [caseId])

  // Load rollup + review summary once
  useEffect(() => {
    if (!caseId) return
    Promise.all([getFinancialsIndex(caseId), getReviewStatus(caseId)])
      .then(([idx, summary]) => {
        setIndex(idx)
        setGateSummary(summary)
        if (!sourceId && idx.sources.length > 0) {
          navigate(`/cases/${caseId}/review/${idx.sources[0].source_id}`, { replace: true })
        }
      })
      .catch((e) => setErr(String(e)))
  }, [caseId, sourceId, navigate])

  // Load document for the active source
  useEffect(() => {
    if (!caseId || !sourceId) return
    setDoc(null); setErr('')
    getSourceDocument(caseId, sourceId)
      .then(setDoc)
      .catch((e) => setErr(String(e)))
  }, [caseId, sourceId])

  const currentSource: FinancialsSource | undefined = useMemo(
    () => index?.sources.find((s) => s.source_id === sourceId),
    [index, sourceId],
  )

  // When source switches, reset to first statement tab + first page
  useEffect(() => {
    setTab('sofp')
    setPdfPage(undefined)
  }, [sourceId])

  const handleSwitchSource = (sid: string) => {
    if (!caseId) return
    navigate(`/cases/${caseId}/review/${sid}`)
  }

  const handleCellEdit = useCallback(async (
    blockIdx: number, rowIdx: number, columnId: string, newValue: number | null,
  ) => {
    if (!caseId || !sourceId || !doc) return
    const oldDoc = doc
    // Optimistic update
    const optimistic: SgFsDocument = JSON.parse(JSON.stringify(doc))
    const block = optimistic.blocks[blockIdx] as Extract<DocumentBlock, { kind: 'statement' }>
    const row = block.rows[rowIdx]
    if (!row.values) row.values = {}
    row.values[columnId] = newValue
    setDoc(optimistic)
    try {
      await patchDocument(
        caseId, sourceId,
        ['blocks', blockIdx, 'rows', rowIdx, 'values', columnId],
        newValue,
        { reason: 'Analyst correction' },
      )
    } catch (e) {
      setDoc(oldDoc)
      setErr(String(e))
    }
  }, [caseId, sourceId, doc])

  const handleApprove = async () => {
    if (!caseId || !sourceId) return
    const notes = prompt('Optional approval note:') || undefined
    setBusy(true); setErr('')
    try {
      await approveSource(caseId, sourceId, notes)
      await refreshSummary()
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }

  const handleReject = async () => {
    if (!caseId || !sourceId) return
    const notes = prompt('Reason for rejection:') || ''
    if (!notes) return
    setBusy(true); setErr('')
    try {
      await rejectSource(caseId, sourceId, notes)
      await refreshSummary()
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }

  const handleReset = async () => {
    if (!caseId || !sourceId) return
    if (!confirm('Reset approval status back to pending?')) return
    setBusy(true); setErr('')
    try {
      await resetSourceStatus(caseId, sourceId)
      await refreshSummary()
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }

  const handleRunAnalysis = async () => {
    if (!caseId) return
    setAnalyzing(true); setAnalyzeError('')
    try {
      await runAnalysis(caseId)
      navigate(`/cases/${caseId}/report`)
    } catch (e) {
      setAnalyzeError(String(e))
    } finally {
      setAnalyzing(false)
    }
  }

  if (!caseId) return null
  if (err) return <div className="card"><p>Error: {err}</p></div>
  if (!index) return <p>Loading review…</p>
  if (index.source_count === 0) {
    return (
      <div className="card">
        <p>No extracted sources to review yet.</p>
        <Link to={`/cases/${caseId}/intake`}>Go to Document Intake</Link>
      </div>
    )
  }
  if (!sourceId || !doc) return <p>Loading source…</p>

  const reviewStatus = (currentSource as any)?.review?.status as 'pending' | 'approved' | 'rejected' | undefined
  const meta = doc.document
  const blocks = doc.blocks
  const statementBlock = blocks.find(
    (b): b is Extract<DocumentBlock, { kind: 'statement' }> =>
      b.kind === 'statement' && tab !== 'notes' && tab !== 'narrative' && b.type === tab,
  )
  const notesBlock = blocks.find(
    (b): b is Extract<DocumentBlock, { kind: 'notes' }> => b.kind === 'notes',
  )
  const narrativeBlocks = blocks.filter(
    (b): b is Extract<DocumentBlock, { kind: 'cover' | 'corporate_info' | 'directors_statement' | 'auditor_report' }> =>
      ['cover', 'corporate_info', 'directors_statement', 'auditor_report'].includes(b.kind as any),
  )
  const statementBlockIdx = statementBlock ? blocks.indexOf(statementBlock) : -1

  return (
    <div className="review">
      <div className="review-header">
        <div>
          <p className="intake-crumb">
            <Link to="/">Cases</Link> · <Link to={`/cases/${caseId}`}>Case</Link> · <Link to={`/cases/${caseId}/intake`}>Intake</Link> · Review
          </p>
          <h2>{meta.entity.name || 'Review extracted data'}</h2>
          <div className="review-chips">
            <span className="chip">{meta.framework}</span>
            <span className={`chip ${meta.audited ? 'chip-pos' : 'chip-warn'}`}>
              {meta.audited ? 'Audited' : 'Unaudited'}
            </span>
            <span className="chip">{meta.consolidated ? 'Consolidated' : 'Standalone'}</span>
            <span className="chip">{meta.currency}{meta.currency_unit ? ` · ${meta.currency_unit}` : ''}</span>
            {meta.entity.uen && <span className="chip">UEN {meta.entity.uen}</span>}
          </div>
        </div>
        <div className={`review-status review-status-${reviewStatus || 'pending'}`}>
          {reviewStatus === 'approved' ? '✓ Approved' :
           reviewStatus === 'rejected' ? '✗ Rejected' : 'Pending review'}
        </div>
      </div>

      {/* Approval gate banner: drives the Run-Analysis CTA */}
      {gateSummary && (
        <AnalysisGateBanner
          summary={gateSummary}
          analyzing={analyzing}
          analyzeError={analyzeError}
          onRunAnalysis={handleRunAnalysis}
        />
      )}

      {/* Source switcher */}
      {index.sources.length > 1 && (
        <div className="review-source-bar">
          {index.sources.map((s) => {
            const status = (s as any).review?.status as string | undefined
            return (
              <button
                key={s.source_id}
                className={`review-source-chip ${s.source_id === sourceId ? 'active' : ''}`}
                onClick={() => handleSwitchSource(s.source_id)}
              >
                <span className="review-source-name">{s.entity || s.original_filename}</span>
                <span className="review-source-fy">
                  {s.fys.length > 0 ? `FY${s.fys[0].slice(2)}` : ''}
                </span>
                <span className={`review-source-status review-source-status-${status || 'pending'}`}>
                  {status === 'approved' ? '✓' : status === 'rejected' ? '✗' : '○'}
                </span>
              </button>
            )
          })}
        </div>
      )}

      <div className="review-body">
        {/* LEFT — PDF viewer */}
        <div className="review-left">
          <iframe
            key={`${sourceId}-${pdfPage || 'all'}`}
            title="Source PDF"
            src={pdfUrl(caseId, sourceId, pdfPage)}
            className="review-pdf"
          />
          {pdfPage && (
            <div className="review-pdf-meta">Jumped to p.{pdfPage}</div>
          )}
        </div>

        {/* RIGHT — extracted content + tabs */}
        <div className="review-right">
          <div className="review-tabs">
            {(['sofp', 'soci', 'soce', 'socf'] as StatementType[]).map((k) => (
              <button
                key={k}
                className={`review-tab ${tab === k ? 'active' : ''}`}
                onClick={() => setTab(k)}
              >{STATEMENT_LABELS[k]}</button>
            ))}
            {notesBlock && notesBlock.items.length > 0 && (
              <button
                className={`review-tab ${tab === 'notes' ? 'active' : ''}`}
                onClick={() => setTab('notes')}
              >Notes ({notesBlock.items.length})</button>
            )}
            {narrativeBlocks.length > 0 && (
              <button
                className={`review-tab ${tab === 'narrative' ? 'active' : ''}`}
                onClick={() => setTab('narrative')}
              >Narrative</button>
            )}
          </div>

          <div className="review-content">
            {(tab === 'sofp' || tab === 'soci' || tab === 'soce' || tab === 'socf') && (
              statementBlock ? (
                <StatementTable
                  block={statementBlock}
                  blockIdx={statementBlockIdx}
                  onRowClick={(row) => { if (row.page) setPdfPage(row.page) }}
                  onCellEdit={handleCellEdit}
                  approved={reviewStatus === 'approved'}
                />
              ) : (
                <p className="review-empty">No {STATEMENT_LABELS[tab]} block extracted for this source.</p>
              )
            )}

            {tab === 'notes' && notesBlock && (
              <NotesView block={notesBlock} onPageClick={setPdfPage} />
            )}

            {tab === 'narrative' && (
              <NarrativeView blocks={narrativeBlocks} onPageClick={setPdfPage} />
            )}
          </div>

          <div className="review-footer">
            {reviewStatus === 'approved' || reviewStatus === 'rejected' ? (
              <>
                <button onClick={handleReset} disabled={busy}>Reset to pending</button>
                <span className="review-footer-help">
                  Status: {reviewStatus} · This source is locked for review changes.
                </span>
              </>
            ) : (
              <>
                <button className="review-reject" onClick={handleReject} disabled={busy}>Reject</button>
                <button className="primary review-approve" onClick={handleApprove} disabled={busy}>
                  Approve source ✓
                </button>
                <span className="review-footer-help">
                  Approve locks this source. Review every column before approval.
                </span>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// ---- Analysis gate banner --------------------------------------------------

function AnalysisGateBanner({
  summary, analyzing, analyzeError, onRunAnalysis,
}: {
  summary: ReviewStatusSummary
  analyzing: boolean
  analyzeError: string
  onRunAnalysis: () => void
}) {
  if (summary.ready_to_analyze) {
    return (
      <div className="gate-banner gate-banner-ready">
        <div className="gate-banner-row">
          <div className="gate-banner-title">
            ✓ {summary.approved} of {summary.total} source{summary.total === 1 ? '' : 's'} approved — ready for analysis
          </div>
          <button
            className="primary gate-banner-cta"
            disabled={analyzing}
            onClick={onRunAnalysis}
          >
            {analyzing ? 'Analysing…' : 'Run analysis →'}
          </button>
        </div>
        {analyzing && (
          <div className="gate-banner-help">
            Computing FS analytics + running FS, industry, and qualitative agents.
            Typically 30–60 seconds. You'll be redirected to the case dashboard when done.
          </div>
        )}
        {analyzeError && (
          <div className="gate-banner-error">Analysis failed: {analyzeError}</div>
        )}
      </div>
    )
  }
  const notApproved = summary.sources.filter((s) => s.status !== 'approved')
  return (
    <div className="gate-banner gate-banner-blocked">
      <div className="gate-banner-row">
        <div className="gate-banner-title">
          Approval gate — {summary.approved} of {summary.total} approved
          {summary.rejected > 0 && <span style={{ color: '#991b1b' }}> · {summary.rejected} rejected</span>}
        </div>
        <button className="primary gate-banner-cta" disabled>
          Run analysis →
        </button>
      </div>
      <div className="gate-banner-help">
        Every uploaded source must be approved before analysis can run. Still needs attention:
      </div>
      <ul className="gate-banner-list">
        {notApproved.map((s) => (
          <li key={s.source_id}>
            <strong>{s.original_filename}</strong> — {s.status === 'rejected' ? 'rejected' : 'pending review'}
            {s.fys?.length ? ` · ${s.fys.join(', ')}` : ''}
          </li>
        ))}
      </ul>
    </div>
  )
}


// ---- Statement table -------------------------------------------------------

function StatementTable({
  block, blockIdx, onRowClick, onCellEdit, approved,
}: {
  block: Extract<DocumentBlock, { kind: 'statement' }>
  blockIdx: number
  onRowClick: (row: StatementRowDoc) => void
  onCellEdit: (blockIdx: number, rowIdx: number, columnId: string, newValue: number | null) => void
  approved: boolean
}) {
  return (
    <table className="review-table">
      <thead>
        <tr>
          <th className="review-th-label">Line item</th>
          {block.columns.map((c) => (
            <th key={c.id} className="review-th-num">
              <div>{c.fy}</div>
              <div className="review-th-sub">{c.perimeter === 'group' ? 'Group' : 'Company'}</div>
            </th>
          ))}
          <th className="review-th-meta">p.</th>
        </tr>
      </thead>
      <tbody>
        {block.rows.map((r, i) => {
          const indent = 12 + r.indent_level * 16
          const cls = `review-row review-row-${r.row_type}`
          if (r.row_type === 'section_header') {
            return (
              <tr key={i} className={cls}>
                <th colSpan={block.columns.length + 2} style={{ paddingLeft: indent }}>
                  {r.label}
                </th>
              </tr>
            )
          }
          return (
            <tr key={i} className={cls}>
              <td onClick={() => onRowClick(r)} style={{ paddingLeft: indent, cursor: 'pointer' }} title={r.canonical_code || ''}>
                {r.label}
                {r.note_ref && <span className="review-note-ref"> {r.note_ref}</span>}
              </td>
              {block.columns.map((c) => (
                <EditableCell
                  key={c.id}
                  value={r.values?.[c.id] ?? null}
                  readOnly={approved}
                  onChange={(v) => onCellEdit(blockIdx, i, c.id, v)}
                />
              ))}
              <td className="review-td-meta" onClick={() => onRowClick(r)} style={{ cursor: 'pointer' }}>
                {r.page ? `p.${r.page}` : ''}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function EditableCell({
  value, readOnly, onChange,
}: {
  value: number | null
  readOnly: boolean
  onChange: (next: number | null) => void
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(fmtNumber(value))
  useEffect(() => { setDraft(fmtNumber(value)) }, [value])
  if (readOnly) {
    return <td className="review-td-num">{fmtNumber(value)}</td>
  }
  return (
    <td className={`review-td-num ${editing ? 'editing' : ''}`}>
      {editing ? (
        <input
          autoFocus
          className="review-cell-input"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => {
            setEditing(false)
            const parsed = parseCellInput(draft)
            if (parsed !== value) onChange(parsed)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
            if (e.key === 'Escape') { setDraft(fmtNumber(value)); setEditing(false) }
          }}
        />
      ) : (
        <span onClick={() => setEditing(true)} title="Click to edit">{fmtNumber(value)}</span>
      )}
    </td>
  )
}

// ---- Notes view ------------------------------------------------------------

function NotesView({
  block, onPageClick,
}: {
  block: Extract<DocumentBlock, { kind: 'notes' }>
  onPageClick: (p: number) => void
}) {
  return (
    <div className="review-notes-list">
      {block.items.map((n, i) => (
        <div key={i} className="review-note">
          <div className="review-note-head">
            <span className="review-note-no">#{n.no}</span>
            <span className="review-note-title">{n.title}</span>
            {n.page_range?.[0] && (
              <button className="review-page-link" onClick={() => onPageClick(n.page_range![0])}>
                p.{n.page_range[0]}
              </button>
            )}
          </div>
          {n.markdown && (
            <div className="review-note-body markdown-body">
              <ReactMarkdown>{n.markdown}</ReactMarkdown>
            </div>
          )}
          {(n.tables || []).map((t, j) => (
            <div key={j} className="review-note-table-wrap">
              {t.caption && <div className="review-note-caption">{t.caption}</div>}
              <table className="review-note-table">
                <thead>
                  <tr>{t.columns.map((c) => <th key={c.id}>{c.label}</th>)}</tr>
                </thead>
                <tbody>
                  {t.rows.map((r, k) => (
                    <tr key={k}>
                      {t.columns.map((c) => {
                        const v = r[c.id]
                        return <td key={c.id}>{typeof v === 'number' ? fmtNumber(v) : (v ?? '')}</td>
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

// ---- Narrative view --------------------------------------------------------

function NarrativeView({
  blocks, onPageClick,
}: {
  blocks: Extract<DocumentBlock, { kind: 'cover' | 'corporate_info' | 'directors_statement' | 'auditor_report' }>[]
  onPageClick: (p: number) => void
}) {
  if (blocks.length === 0) {
    return <p className="review-empty">No narrative blocks extracted for this source.</p>
  }
  return (
    <div className="review-narrative-list">
      {blocks.map((b, i) => (
        <div key={i} className="review-note">
          <div className="review-note-head">
            <span className="review-note-title">{b.title || b.kind.replace('_', ' ')}</span>
            {b.page_range?.[0] && (
              <button className="review-page-link" onClick={() => onPageClick(b.page_range[0])}>
                p.{b.page_range[0]}
              </button>
            )}
          </div>
          <div className="review-note-body markdown-body">
            <ReactMarkdown>{b.markdown}</ReactMarkdown>
          </div>
        </div>
      ))}
    </div>
  )
}
