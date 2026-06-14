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

export default function ReportWorkspace() {
  const { caseId } = useParams<{ caseId: string }>()
  const [report, setReport] = useState<Report | null>(null)
  const [status, setStatus] = useState<ReportStatus | null>(null)
  const [err, setErr] = useState<string>('')
  const [busy, setBusy] = useState(false)
  const [sectionWorking, setSectionWorking] = useState<string | null>(null)
  const [activeSection, setActiveSection] = useState<string | null>(null)
  const contentRef = useRef<HTMLDivElement>(null)

  const loadAll = useCallback(async () => {
    if (!caseId) return
    setErr('')
    try {
      const st = await getReportStatus(caseId)
      setStatus(st)
      if (st.status === 'completed' || st.report_on_disk) {
        const r = await getReport(caseId)
        setReport(r)
        if (r.sections.length > 0) {
          setActiveSection((cur) => cur ?? slugify(r.sections[0].title))
        }
      } else if (st.status === 'not_run') {
        setReport(null)
      }
    } catch (e) {
      setErr(String(e))
    }
  }, [caseId])

  useEffect(() => { loadAll() }, [loadAll])

  // Poll during generation
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
          setReport(r)
        }
      } catch { /* ignore */ }
    }, 4000)
    return () => window.clearInterval(t)
  }, [caseId, status?.status])

  const handleGenerate = async () => {
    if (!caseId) return
    setBusy(true); setErr('')
    try {
      await triggerReportGeneration(caseId)
      const st = await getReportStatus(caseId)
      setStatus(st)
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
    } catch (e) {
      setErr(String(e))
    } finally {
      setSectionWorking(null)
    }
  }

  // Scroll spy
  useEffect(() => {
    if (!contentRef.current || !report) return
    const ids = report.sections.map((s) => slugify(s.title))
    const observer = new IntersectionObserver((entries) => {
      const visible = entries
        .filter((e) => e.isIntersecting)
        .sort((a, b) => b.intersectionRatio - a.intersectionRatio)
      if (visible[0]) setActiveSection(visible[0].target.id)
    }, { root: contentRef.current, threshold: [0, 0.3, 0.6, 1] })
    ids.forEach((id) => {
      const el = document.getElementById(id)
      if (el) observer.observe(el)
    })
    return () => observer.disconnect()
  }, [report])

  const handleJump = (slug: string) => {
    const el = document.getElementById(slug)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
      setActiveSection(slug)
    }
  }

  const isGenerating = status?.status === 'queued' || status?.status === 'running'
  const hasReport = !!report && report.sections.length > 0

  if (!caseId) return null

  return (
    <div className="report-workspace">
      <div className="report-topbar">
        <div>
          <p className="intake-crumb">
            <Link to="/">Cases</Link> · <Link to={`/cases/${caseId}`}>Case</Link> ·
            {' '}<Link to={`/cases/${caseId}/financials`}>Financial Analysis</Link> · Credit Report
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
            <a className="primary fin-generate fin-generate-download"
               href={reportDocxUrl(caseId)} download>
              ⬇ Download .docx
            </a>
          )}
          {(hasReport || !isGenerating) && (
            <button className="primary fin-generate"
                    disabled={busy || isGenerating}
                    onClick={handleGenerate}
                    style={hasReport ? { background: '#e2e8f0', color: '#1f3a5f' } : undefined}>
              {isGenerating ? 'Generating…' : hasReport ? 'Re-generate' : 'Generate Report'}
            </button>
          )}
          {hasReport && (
            <button className="primary fin-generate"
                    style={{ background: '#e2e8f0', color: '#1f3a5f' }}
                    onClick={() => window.print()}>
              🖨 Print
            </button>
          )}
        </div>
      </div>

      {err && <div className="intake-error">{err}</div>}

      {isGenerating && (
        <div className="report-progress">
          <div className="report-progress-bar"><div className="report-progress-bar-inner" /></div>
          <div className="report-progress-text">
            Generating credit report — 17 sections fan out in parallel, typically 30–60 seconds.
            {status?.started_at && (
              <span style={{ color: '#94a3b8' }}>
                {' · '}started {new Date(status.started_at).toLocaleTimeString()}
              </span>
            )}
          </div>
        </div>
      )}

      {!hasReport && !isGenerating && (
        <div className="card report-empty">
          <h3>No report yet</h3>
          <p>Generate the credit analysis report from the approved financial statements. Sections are produced by focused LLM calls in parallel and assembled into a Word-ready document.</p>
          <button className="primary fin-generate" onClick={handleGenerate} disabled={busy}>
            Generate Credit Report →
          </button>
        </div>
      )}

      {hasReport && (
        <div className="report-body">
          <aside className="report-nav">
            <div className="report-nav-title">Sections</div>
            <ol className="report-nav-list">
              {report!.sections.map((s) => {
                const slug = slugify(s.title)
                const active = slug === activeSection
                return (
                  <li key={s.code} className={active ? 'active' : ''}>
                    <button onClick={() => handleJump(slug)}>
                      <span className="report-nav-num">{s.number ?? '·'}</span>
                      <span className="report-nav-name">{s.title}</span>
                    </button>
                  </li>
                )
              })}
            </ol>
          </aside>

          <div className="report-content" ref={contentRef}>
            <div className="report-titlepage">
              <h1>Credit Analysis Report</h1>
              <div className="report-entity">{report!.entity_name}</div>
              <div className="report-titlemeta">
                <div><strong>Financial years reviewed:</strong> {report!.fys.join(', ')}</div>
                <div><strong>Prepared for:</strong> Senior Management / Credit Approval Committee</div>
                <div><strong>Generated:</strong> {report!.generated_at.slice(0, 10)}</div>
              </div>
            </div>

            {report!.sections.map((section) => {
              const slug = slugify(section.title)
              const working = sectionWorking === section.code
              return (
                <article key={section.code} id={slug}
                         className={`report-section ${working ? 'working' : ''}`}>
                  <header className="report-section-head">
                    <h2>
                      <span className="report-section-num">{section.number ?? '·'}.</span>
                      {section.title}
                    </h2>
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
                  </header>
                  {section.source === 'error' && (
                    <div className="section-error">⚠ {section.error || 'Generation failed for this section'}</div>
                  )}
                  {working ? (
                    <div className="section-working">Regenerating…</div>
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
                </article>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
