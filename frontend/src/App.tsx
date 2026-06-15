import { useState } from 'react'
import { Routes, Route, Link, NavLink } from 'react-router-dom'
import CaseList from './pages/CaseList'
import NewCase from './pages/NewCase'
import Financials from './pages/Financials'
import Intake from './pages/Intake'
import Review from './pages/Review'
import Report from './pages/Report'
import CaseRedirect from './pages/CaseRedirect'
import CaseWorkbenchLayout from './components/CaseWorkbenchLayout'

export default function App() {
  const [navOpen, setNavOpen] = useState(false)

  return (
    <div className="app">
      <header className="app-topbar">
        <button
          type="button"
          className="app-menu-button"
          onClick={() => setNavOpen((v) => !v)}
          aria-label={navOpen ? 'Close navigation' : 'Open navigation'}
          aria-expanded={navOpen}
        >
          ☰
        </button>
        <Link to="/" className="app-brand" onClick={() => setNavOpen(false)}>
          <span className="brand-mark" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          <span>CrediSage</span>
        </Link>
        <div className="app-user">
          <span className="app-user-avatar">CS</span>
          <span>
            <strong>Credit Analyst</strong>
            <small>Financial spreading workspace</small>
          </span>
        </div>
      </header>

      {navOpen && <button className="drawer-backdrop" aria-label="Close navigation" onClick={() => setNavOpen(false)} />}
      <aside className={`nav-drawer ${navOpen ? 'open' : ''}`} aria-hidden={!navOpen}>
        <div className="drawer-head">
          <div className="drawer-brand">
            <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
            <strong>CrediSage</strong>
          </div>
          <button type="button" className="drawer-close" onClick={() => setNavOpen(false)} aria-label="Close navigation">☰</button>
        </div>
        <nav className="drawer-nav">
          <div className="drawer-group-label">Platform</div>
          <NavLink to="/" onClick={() => setNavOpen(false)}>
            <span className="drawer-icon">▦</span>
            <span>Case Workspace</span>
          </NavLink>
          <NavLink to="/new" onClick={() => setNavOpen(false)}>
            <span className="drawer-icon">＋</span>
            <span>New Case</span>
          </NavLink>
          <a aria-disabled="true">
            <span className="drawer-icon">DB</span>
            <span>Company Database</span>
          </a>
          <a aria-disabled="true">
            <span className="drawer-icon">↔</span>
            <span>Data Connections</span>
          </a>
          <div className="drawer-group-label">Intelligence</div>
          <a aria-disabled="true">
            <span className="drawer-icon">!</span>
            <span>News & Alerts</span>
          </a>
          <a aria-disabled="true">
            <span className="drawer-icon">?</span>
            <span>Knowledge Base</span>
          </a>
          <a aria-disabled="true">
            <span className="drawer-icon">SI</span>
            <span>Sector Intelligence</span>
          </a>
          <div className="drawer-group-label">Analysis Modules</div>
          <a className="drawer-current" aria-disabled="true">
            <span className="drawer-icon">FS</span>
            <span>
              Financial Statement Analysis
              <small>Phase 1 active module</small>
            </span>
          </a>
          <a aria-disabled="true">
            <span className="drawer-icon">IN</span>
            <span>Industry Analysis</span>
          </a>
          <a aria-disabled="true">
            <span className="drawer-icon">BU</span>
            <span>Business Analysis</span>
          </a>
          <a aria-disabled="true">
            <span className="drawer-icon">MG</span>
            <span>Management Assessment</span>
          </a>
          <div className="drawer-group-label">Outputs</div>
          <a aria-disabled="true">
            <span className="drawer-icon">CR</span>
            <span>Credit Reports</span>
          </a>
        </nav>
        <div className="drawer-foot">
          <span className="status-dot" />
          <span>Local demo environment</span>
        </div>
      </aside>

      <main className="main">
        <Routes>
          <Route path="/" element={<CaseList />} />
          <Route path="/new" element={<NewCase />} />
          <Route path="/cases/:caseId" element={<CaseWorkbenchLayout />}>
            <Route index element={<CaseRedirect />} />
            <Route path="intake" element={<Intake />} />
            <Route path="review" element={<Review />} />
            <Route path="review/:sourceId" element={<Review />} />
            <Route path="financials" element={<Financials />} />
            <Route path="report" element={<Report />} />
          </Route>
        </Routes>
      </main>
    </div>
  )
}
