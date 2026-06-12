import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { listCases, Case } from '../api'

export default function CaseList() {
  const [cases, setCases] = useState<Case[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    listCases()
      .then(setCases)
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  return (
    <div>
      <h2 style={{ marginBottom: '1rem' }}>Credit Cases</h2>
      <Link to="/new" className="card" style={{ display: 'inline-block', marginBottom: '1rem' }}>
        + New Case
      </Link>
      {loading && <p>Loading...</p>}
      <div className="grid">
        {cases.map((c) => (
          <Link key={c.case_id} to={`/cases/${c.case_id}`} style={{ textDecoration: 'none', color: 'inherit' }}>
            <div className="card">
              <h3>{c.company_name}</h3>
              <p style={{ color: '#64748b', fontSize: '0.9rem' }}>
                {c.case_id} · {c.status} · {c.progress}%
              </p>
            </div>
          </Link>
        ))}
      </div>
      {!loading && cases.length === 0 && (
        <p style={{ color: '#64748b' }}>No cases yet. Create one to get started.</p>
      )}
    </div>
  )
}
