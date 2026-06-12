import { Routes, Route, Link } from 'react-router-dom'
import CaseList from './pages/CaseList'
import CaseDetail from './pages/CaseDetail'
import NewCase from './pages/NewCase'
import Financials from './pages/Financials'

export default function App() {
  return (
    <div className="app">
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
          <Route path="/cases/:caseId" element={<CaseDetail />} />
          <Route path="/cases/:caseId/financials" element={<Financials />} />
        </Routes>
      </main>
    </div>
  )
}
