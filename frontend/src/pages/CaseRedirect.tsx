import { useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { getCaseStatus } from '../api'

export default function CaseRedirect() {
  const { caseId } = useParams<{ caseId: string }>()
  const navigate = useNavigate()

  useEffect(() => {
    if (!caseId) return
    getCaseStatus(caseId)
      .then((status) => {
        const s = String(status.status || '').toLowerCase()
        if (s === 'completed') {
          navigate(`/cases/${caseId}/report`, { replace: true })
        } else if (s.includes('extract') || s.includes('review')) {
          navigate(`/cases/${caseId}/review`, { replace: true })
        } else {
          navigate(`/cases/${caseId}/intake`, { replace: true })
        }
      })
      .catch(() => navigate(`/cases/${caseId}/intake`, { replace: true }))
  }, [caseId, navigate])

  return <p>Opening case...</p>
}
