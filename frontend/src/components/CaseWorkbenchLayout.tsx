import { useState } from 'react'
import { NavLink, Outlet, useParams } from 'react-router-dom'
import CoworkerRail from './CoworkerRail'

const financialSteps = [
  { to: 'intake', label: 'Documents', detail: 'Intake' },
  { to: 'review', label: 'Extraction Review', detail: 'Validate FS' },
  { to: 'financials', label: 'FS Analysis', detail: 'Spreads & ratios' },
  { to: 'report', label: 'Credit Report', detail: 'Committee output' },
]

const creditPillars = [
  { key: 'industry', label: 'Industry', icon: 'IN', enabled: false },
  { key: 'financial', label: 'Financial', icon: 'FS', enabled: true },
  { key: 'business', label: 'Business', icon: 'BU', enabled: false },
  { key: 'management', label: 'Management', icon: 'MG', enabled: false },
]

export default function CaseWorkbenchLayout() {
  const { caseId } = useParams<{ caseId: string }>()
  const [coworkerOpen, setCoworkerOpen] = useState(false)
  if (!caseId) return null

  return (
    <div className={`case-shell ${coworkerOpen ? 'coworker-open' : ''}`}>
      <aside className="workflow-rail" aria-label="Case workflow">
        <div className="workflow-title">Pillars</div>
        {creditPillars.map((pillar) => (
          pillar.enabled ? (
            <NavLink
              key={pillar.key}
              to={`/cases/${caseId}/financials`}
              className="workflow-rail-link active"
              title={`${pillar.label} analysis`}
            >
              <span className="workflow-icon">{pillar.icon}</span>
              <span className="workflow-label">{pillar.label}</span>
            </NavLink>
          ) : (
            <button
              key={pillar.key}
              type="button"
              className="workflow-rail-link disabled"
              title={`${pillar.label} analysis coming in a later phase`}
              disabled
            >
              <span className="workflow-icon">{pillar.icon}</span>
              <span className="workflow-label">{pillar.label}</span>
            </button>
          )
        ))}
      </aside>
      <div className={`case-workbench ${coworkerOpen ? '' : 'coworker-collapsed'}`}>
        <div className="case-stepper">
          <div className="case-stepper-label">
            <strong>Financial Statement Analysis</strong>
            <span>Phase 1 workflow</span>
          </div>
          {financialSteps.map((s, i) => (
            <NavLink
              key={s.to}
              to={`/cases/${caseId}/${s.to}`}
              className={({ isActive }) => `case-step ${isActive ? 'active' : ''}`}
            >
              <span>{i + 1}</span>
              <em>
                {s.label}
                <small>{s.detail}</small>
              </em>
            </NavLink>
          ))}
        </div>
        <div className="case-content-row">
          <section className="case-workbench-main">
            <Outlet />
          </section>
          <CoworkerRail caseId={caseId} open={coworkerOpen} onOpenChange={setCoworkerOpen} />
        </div>
      </div>
    </div>
  )
}
