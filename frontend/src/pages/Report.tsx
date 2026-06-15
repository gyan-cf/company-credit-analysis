import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
  getReport,
  getReportStatus,
  reportDocxUrl,
  triggerReportGeneration,
  regenerateReportSection,
  type Report,
  type ReportSection,
  type ReportStatus,
} from '../api'

type SectionAction = 'tighten' | 'expand' | 'regenerate'

function slugify(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
}

function defaultCollapsedSections(report: Report): Set<string> {
  return new Set(report.sections.slice(1).map((s) => slugify(s.title)))
}

export default function ReportWorkspace() {
  const { caseId } = useParams<{ caseId: string }>()
  const [report, setReport] = useState<Report | null>(null)
  const [status, setStatus] = useState<ReportStatus | null>(null)
  const [err, setErr] = useState<string>('')
  const [busy, setBusy] = useState(false)
  const [sectionWorking, setSectionWorking] = useState<string | null>(null)
  const [collapsedSections, setCollapsedSections] = useState<Set<string>>(new Set())
  const contentRef = useRef<HTMLDivElement>(null)

  const loadReportIntoState = (r: Report) => {
    setReport(r)
    if (r.sections.length > 0) {
      setCollapsedSections((cur) => (cur.size > 0 ? cur : defaultCollapsedSections(r)))
    }
  }

  const loadAll = useCallback(async () => {
    if (!caseId) return
    setErr('')
    try {
      const st = await getReportStatus(caseId)
      setStatus(st)
      if (st.status === 'completed' || st.report_on_disk) {
        const r = await getReport(caseId)
        loadReportIntoState(r)
      } else if (st.status === 'not_run') {
        setReport(null)
        setCollapsedSections(new Set())
      }
    } catch (e) {
      setErr(String(e))
    }
  }, [caseId])

  useEffect(() => { loadAll() }, [loadAll])

  useEffect(() => {
    if (!caseId) return
    const s = status?.status
    if (s !== 'queued' && s !== 'running') return
    const t = window.setInterval(async () => {
      try {
        const st = await getReportStatus(caseId)
        setStatus(st)
        if (st.status === 'completed') {
          const r = await getReport(caseId)
          loadReportIntoState(r)
        }
      } catch {
        // Polling is best-effort; the explicit error path is handled by loadAll/generate.
      }
    }, 4000)
    return () => window.clearInterval(t)
  }, [caseId, status?.status])

  const handleGenerate = async () => {
    if (!caseId) return
    setBusy(true)
    setErr('')
    try {
      await triggerReportGeneration(caseId)
      const st = await getReportStatus(caseId)
      setStatus(st)
      setCollapsedSections(new Set())
    } catch (e) {
      setErr(String(e))
    } finally {
      setBusy(false)
    }
  }

  const handleSectionAction = async (section: ReportSection, action: SectionAction) => {
    if (!caseId) return
    setSectionWorking(section.code)
    setErr('')
    try {
      const instruction = action === 'regenerate' ? undefined : action
      const updated = await regenerateReportSection(caseId, section.code, instruction)
      setReport((cur) => cur ? {
        ...cur,
        sections: cur.sections.map((s) => s.code === section.code ? updated.section : s),
      } : cur)
      setCollapsedSections((cur) => {
        const next = new Set(cur)
        next.delete(slugify(section.title))
        return next
      })
    } catch (e) {
      setErr(String(e))
    } finally {
      setSectionWorking(null)
    }
  }

  const toggleSection = (slug: string) => {
    setCollapsedSections((cur) => {
      const next = new Set(cur)
      if (next.has(slug)) next.delete(slug)
      else next.add(slug)
      return next
    })
  }

  const collapseAllSections = () => {
    if (!report) return
    setCollapsedSections(new Set(report.sections.map((s) => slugify(s.title))))
  }

  const expandAllSections = () => {
    setCollapsedSections(new Set())
  }

  const isGenerating = status?.status === 'queued' || status?.status === 'running'
  const hasReport = !!report && report.sections.length > 0

  if (!caseId) return null

  return (
    <div className="report-workspace">
      <div className="report-topbar">
        <div>
          <p className="intake-crumb">
            <Link to="/">Cases</Link> / <Link to={`/cases/${caseId}`}>Case</Link> /
            {' '}<Link to={`/cases/${caseId}/financials`}>Financial Analysis</Link> / Credit Report
          </p>
          <h2>Credit Analysis Report</h2>
          {report && (
            <div className="report-chips">
              <span className="chip">{report.entity_name}</span>
              <span className="chip">FYs {report.fys.join(', ')}</span>
              <span className="chip">{report.section_count} sections</span>
              <span className="chip">Generated {report.generated_at.slice(0, 10)}</span>
            </div>
          )}
        </div>
        <div className="report-controls">
          {hasReport && (
            <div className="report-primary-actions">
              <a className="workflow-next-action" href={reportDocxUrl(caseId)} download>
                Download Report
              </a>
              <details className="report-action-menu secondary">
                <summary>More</summary>
                <div className="report-action-menu-list">
                  <button type="button" disabled={busy || isGenerating} onClick={handleGenerate}>
                    {isGenerating ? 'Generating...' : 'Re-generate Report'}
                  </button>
                  <button type="button" onClick={() => window.print()}>Print</button>
                </div>
              </details>
            </div>
          )}
          {!hasReport && !isGenerating && (
            <button className="primary fin-generate workflow-next-action" disabled={busy} onClick={handleGenerate}>
              Generate Report
            </button>
          )}
        </div>
      </div>

      {err && <div className="intake-error">{err}</div>}

      {isGenerating && (
        <div className="report-progress">
          <div className="report-progress-bar"><div className="report-progress-bar-inner" /></div>
          <div className="report-progress-text">
            Generating credit report. 17 sections fan out in parallel, typically 30-60 seconds.
            {status?.started_at && (
              <span style={{ color: '#94a3b8' }}>
                {' / '}started {new Date(status.started_at).toLocaleTimeString()}
              </span>
            )}
          </div>
        </div>
      )}

      {!hasReport && !isGenerating && (
        <div className="card report-empty">
          <h3>No report yet</h3>
          <p>Generate the credit analysis report from the approved financial statements. Sections are produced by focused LLM calls in parallel and assembled into a Word-ready document.</p>
          <button className="primary fin-generate workflow-next-action" onClick={handleGenerate} disabled={busy}>
            Generate Report
          </button>
        </div>
      )}

      {hasReport && (
        <div className="report-body">
          <div className="report-section-toolbar">
            <button type="button" className="secondary" onClick={expandAllSections}>Expand all</button>
            <button type="button" className="secondary" onClick={collapseAllSections}>Collapse all</button>
          </div>

          <div className="report-content" ref={contentRef}>
            {report!.sections.map((section, idx) => {
              const slug = slugify(section.title)
              const working = sectionWorking === section.code
              const collapsed = collapsedSections.has(slug)
              return (
                <article
                  key={section.code}
                  id={slug}
                  className={`report-section ${working ? 'working' : ''} ${collapsed ? 'collapsed' : ''}`}
                >
                  <header className="report-section-head">
                    <button
                      className="report-section-toggle"
                      type="button"
                      aria-expanded={!collapsed}
                      aria-controls={`${slug}-body`}
                      onClick={() => toggleSection(slug)}
                    >
                      <span className="report-section-num">{idx + 1}.</span>
                      <span className="report-section-title">{section.title}</span>
                      <span className="report-section-state">{collapsed ? '+' : '-'}</span>
                    </button>
                  </header>
                  {!collapsed && (
                    <div className="report-section-body" id={`${slug}-body`}>
                      <details className="report-section-refine">
                        <summary>Refine</summary>
                        <div className="report-section-actions">
                          <button
                            className="section-btn"
                            onClick={() => handleSectionAction(section, 'tighten')}
                            disabled={!!sectionWorking}
                            title="Re-run with instruction to tighten the prose"
                          >Tighten</button>
                          <button
                            className="section-btn"
                            onClick={() => handleSectionAction(section, 'expand')}
                            disabled={!!sectionWorking}
                            title="Re-run with instruction to expand with more detail"
                          >Expand</button>
                          <button
                            className="section-btn"
                            onClick={() => handleSectionAction(section, 'regenerate')}
                            disabled={!!sectionWorking}
                            title="Re-run the section from scratch"
                          >Regenerate</button>
                        </div>
                      </details>
                      {section.source === 'error' && (
                        <div className="section-error">Warning: {section.error || 'Generation failed for this section'}</div>
                      )}
                      {working ? (
                        <div className="section-working">Regenerating...</div>
                      ) : section.html ? (
                        <div
                          className="markdown-body report-prose"
                          dangerouslySetInnerHTML={{ __html: section.html }}
                        />
                      ) : (
                        <div className="markdown-body report-prose">
                          <pre style={{ whiteSpace: 'pre-wrap' }}>{section.markdown || 'No content.'}</pre>
                        </div>
                      )}
                    </div>
                  )}
                </article>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
