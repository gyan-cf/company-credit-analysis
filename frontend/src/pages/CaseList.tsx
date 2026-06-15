import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { listCases, Case } from '../api'

function formatDate(value?: string): string {
  if (!value) return 'Not updated'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Not updated'
  return new Intl.DateTimeFormat('en-SG', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  }).format(date)
}

function formatMoney(c: Case): string {
  if (!c.requested_limit) return 'Limit pending'
  const numeric = Number(String(c.requested_limit).replace(/[^\d.]/g, ''))
  if (!Number.isFinite(numeric) || numeric <= 0) return c.requested_limit
  return `${c.currency || 'SGD'} ${numeric.toLocaleString('en-SG', { maximumFractionDigits: 0 })}`
}

function statusLabel(status: string): string {
  return status ? status.replace(/_/g, ' ') : 'created'
}

export default function CaseList() {
  const [cases, setCases] = useState<Case[]>([])
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')

  useEffect(() => {
    listCases()
      .then(setCases)
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  const filteredCases = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return cases
    return cases.filter((c) => {
      const haystack = [
        c.company_name,
        c.uen,
        c.case_id,
        c.industry_hint,
        c.primary_ssic_desc,
        c.facility_type,
        c.relationship_manager,
      ].filter(Boolean).join(' ').toLowerCase()
      return haystack.includes(needle)
    })
  }, [cases, query])

  const completed = cases.filter((c) => c.status === 'completed').length
  const active = cases.filter((c) => c.status !== 'completed').length
  const avgProgress = cases.length
    ? Math.round(cases.reduce((sum, c) => sum + (c.progress || 0), 0) / cases.length)
    : 0
  const withUen = cases.filter((c) => c.uen || c.cin).length
  const latest = cases[0]

  return (
    <div className="case-list-page">
      <div className="case-list-hero">
        <div>
          <p className="eyebrow">Singapore credit workspace</p>
          <h2>Credit Cases</h2>
          <p className="case-list-lede">
            Track borrower onboarding, financial statement intake and memo readiness across the local SME portfolio.
          </p>
        </div>
        <Link to="/new" className="primary-link-button">New Case</Link>
      </div>

      <section className="portfolio-strip" aria-label="Portfolio summary">
        <div className="portfolio-tile">
          <span>Total cases</span>
          <strong>{cases.length}</strong>
        </div>
        <div className="portfolio-tile">
          <span>Active reviews</span>
          <strong>{active}</strong>
        </div>
        <div className="portfolio-tile">
          <span>Completed</span>
          <strong>{completed}</strong>
        </div>
        <div className="portfolio-tile">
          <span>Avg progress</span>
          <strong>{avgProgress}%</strong>
        </div>
      </section>

      <div className="case-list-body">
        <section className="case-list-main">
          <div className="case-toolbar">
            <div>
              <h3>Portfolio queue</h3>
              <p>{withUen} case{withUen === 1 ? '' : 's'} have UEN captured</p>
            </div>
            <input
              className="case-search"
              value={query}
              placeholder="Search company, UEN, SSIC, RM"
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>

          {loading && <div className="case-empty">Loading cases...</div>}

          {!loading && filteredCases.length > 0 && (
            <div className="case-card-grid">
              {filteredCases.map((c) => {
                const status = c.status || 'created'
                return (
                  <Link key={c.case_id} to={`/cases/${c.case_id}/intake`} className="case-card-link">
                    <article className="case-card">
                      <div className="case-card-top">
                        <span className={`status-pill status-${status.replace(/[^a-z0-9]/gi, '-').toLowerCase()}`}>
                          {statusLabel(status)}
                        </span>
                        <span className="case-id">{c.case_id}</span>
                      </div>
                      <h3>{c.company_name}</h3>
                      <div className="case-meta-grid">
                        <div>
                          <span>UEN</span>
                          <strong>{c.uen || c.cin || 'Pending'}</strong>
                        </div>
                        <div>
                          <span>Facility</span>
                          <strong>{c.facility_type || 'Not set'}</strong>
                        </div>
                        <div>
                          <span>Requested</span>
                          <strong>{formatMoney(c)}</strong>
                        </div>
                        <div>
                          <span>Sector</span>
                          <strong>{c.primary_ssic_desc || c.industry_hint || 'Not set'}</strong>
                        </div>
                      </div>
                      <div className="case-progress-row">
                        <span>Progress</span>
                        <strong>{c.progress || 0}%</strong>
                      </div>
                      <div className="progress-bar slim">
                        <div style={{ width: `${Math.max(0, Math.min(100, c.progress || 0))}%` }} />
                      </div>
                      <div className="case-card-footer">
                        <span>Updated {formatDate(c.updated_at || c.created_at)}</span>
                        <span>{c.relationship_manager || 'Unassigned'}</span>
                      </div>
                    </article>
                  </Link>
                )
              })}
            </div>
          )}

          {!loading && filteredCases.length === 0 && (
            <div className="case-empty">
              <h3>{cases.length ? 'No matching cases' : 'No cases yet'}</h3>
              <p>{cases.length ? 'Adjust the search query to return to the full queue.' : 'Create a Singapore borrower case to start document intake.'}</p>
              {!cases.length && <Link to="/new" className="primary-link-button compact">New Case</Link>}
            </div>
          )}
        </section>

        <aside className="case-list-aside">
          <div className="aside-panel">
            <p className="eyebrow">Next case</p>
            {latest ? (
              <>
                <h3>{latest.company_name}</h3>
                <div className="aside-progress">
                  <span>{latest.progress || 0}% complete</span>
                  <div className="progress-bar slim">
                    <div style={{ width: `${Math.max(0, Math.min(100, latest.progress || 0))}%` }} />
                  </div>
                </div>
                <Link to={`/cases/${latest.case_id}/intake`} className="secondary-button full-width">Open Case</Link>
              </>
            ) : (
              <p className="aside-muted">No active case in the queue.</p>
            )}
          </div>

          <div className="aside-panel">
            <p className="eyebrow">SG onboarding</p>
            <h3>Profile checks</h3>
            <ul className="onboarding-checks">
              <li><span>01</span><strong>UEN and ACRA entity status</strong></li>
              <li><span>02</span><strong>SSIC activity and sector fit</strong></li>
              <li><span>03</span><strong>FYE, audit status and filing currency</strong></li>
              <li><span>04</span><strong>Directors, shareholders and charges</strong></li>
            </ul>
          </div>

          <div className="aside-panel muted-panel">
            <p className="eyebrow">Local pack</p>
            <h3>Common source set</h3>
            <div className="source-chip-row">
              <span>ACRA profile</span>
              <span>Annual return</span>
              <span>Financial statements</span>
              <span>GST / bank data</span>
            </div>
          </div>
        </aside>
      </div>
    </div>
  )
}
