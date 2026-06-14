import { useState } from 'react'
import { NavLink, Outlet, useParams } from 'react-router-dom'
import CoworkerRail from './CoworkerRail'

const steps = [
  { to: 'intake', label: 'Document Intake' },
  { to: 'review', label: 'Review & Finalise' },
  { to: 'financials', label: 'Financial Analysis' },
  { to: 'report', label: 'Credit Report' },
]

export default function CaseWorkbenchLayout() {
  const { caseId } = useParams<{ caseId: string }>()
  const [coworkerOpen, setCoworkerOpen] = useState(false)
  if (!caseId) return null

  return (
    <div className={`case-workbench ${coworkerOpen ? '' : 'coworker-collapsed'}`}>
      <section className="case-workbench-main">
        <div className="case-stepper">
          {steps.map((s, i) => (
            <NavLink
              key={s.to}
              to={`/cases/${caseId}/${s.to}`}
              className={({ isActive }) => `case-step ${isActive ? 'active' : ''}`}
            >
              <span>{i + 1}</span>
              {s.label}
            </NavLink>
          ))}
        </div>
        <Outlet />
      </section>
      <CoworkerRail caseId={caseId} open={coworkerOpen} onOpenChange={setCoworkerOpen} />
    </div>
  )
}
