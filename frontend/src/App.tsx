import { useState } from 'react'
import { Routes, Route, Link } from 'react-router-dom'
import CaseList from './pages/CaseList'
import NewCase from './pages/NewCase'
import Financials from './pages/Financials'
import Intake from './pages/Intake'
import Review from './pages/Review'
import Report from './pages/Report'
import CaseRedirect from './pages/CaseRedirect'
import CaseWorkbenchLayout from './components/CaseWorkbenchLayout'

export default function App() {
  const [navOpen, setNavOpen] = useState(true)

  return (
    <div className={`app ${navOpen ? '' : 'nav-collapsed'}`}>
      <button
        type="button"
        className="app-menu-button"
        onClick={() => setNavOpen((v) => !v)}
        aria-label={navOpen ? 'Collapse navigation' : 'Open navigation'}
        aria-expanded={navOpen}
      >
        ☰
      </button>
      <aside className="sidebar">
        <h1>CrediSage</h1>
        <p style={{ fontSize: '0.85rem', opacity: 0.8, marginBottom: '1rem' }}>
          Company Credit Analyst
        </p>
        <Link to="/">Cases</Link>
        <Link to="/new">New Case</Link>
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
