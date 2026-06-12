import { useEffect, useState, useRef } from 'react'
import { useParams, Link } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import {
  getCaseStatus,
  uploadFile,
  runAnalysis,
  getAssessment,
  getMemo,
  sendChat,
  getChatHistory,
} from '../api'

type Tab = 'upload' | 'dashboard' | 'memo' | 'coworker'

export default function CaseDetail() {
  const { caseId } = useParams<{ caseId: string }>()
  const [tab, setTab] = useState<Tab>('upload')
  const [status, setStatus] = useState({ status: '', progress: 0, company_name: '' })
  const [assessment, setAssessment] = useState<any>(null)
  const [memo, setMemo] = useState('')
  const [analyzing, setAnalyzing] = useState(false)
  const [chatInput, setChatInput] = useState('')
  const [chatHistory, setChatHistory] = useState<{ role: string; content: string }[]>([])
  const chatEnd = useRef<HTMLDivElement>(null)

  const refresh = async () => {
    if (!caseId) return
    const s = await getCaseStatus(caseId)
    setStatus(s)
    if (s.status === 'completed') {
      try {
        setAssessment(await getAssessment(caseId))
        const m = await getMemo(caseId)
        setMemo(m.memo || '')
      } catch { /* not ready */ }
    }
  }

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 3000)
    return () => clearInterval(t)
  }, [caseId])

  useEffect(() => {
    if (caseId && tab === 'coworker') {
      getChatHistory(caseId).then((h) => setChatHistory(h.history || []))
    }
  }, [caseId, tab])

  const handleUpload = async (sourceType: string, files: FileList | null) => {
    if (!caseId || !files) return
    for (const f of Array.from(files)) {
      await uploadFile(caseId, sourceType, f)
    }
    refresh()
  }

  const handleAnalyze = async () => {
    if (!caseId) return
    setAnalyzing(true)
    try {
      await runAnalysis(caseId)
      await refresh()
      setTab('dashboard')
    } catch (e) {
      alert(String(e))
    } finally {
      setAnalyzing(false)
    }
  }

  const handleChat = async () => {
    if (!caseId || !chatInput.trim()) return
    const msg = chatInput
    setChatInput('')
    setChatHistory((h) => [...h, { role: 'user', content: msg }])
    try {
      const r = await sendChat(caseId, msg)
      setChatHistory((h) => [...h, { role: 'assistant', content: r.reply }])
    } catch (e) {
      setChatHistory((h) => [...h, { role: 'assistant', content: `Error: ${e}` }])
    }
    chatEnd.current?.scrollIntoView({ behavior: 'smooth' })
  }

  if (!caseId) return null

  return (
    <div style={{ paddingRight: tab === 'coworker' ? 380 : 0 }}>
      <h2>{status.company_name || caseId}</h2>
      <p style={{ color: '#64748b', marginBottom: '0.5rem' }}>
        Status: <strong>{status.status}</strong>
      </p>
      <div className="progress-bar">
        <div style={{ width: `${status.progress}%` }} />
      </div>

      <div style={{ display: 'flex', gap: '0.5rem', margin: '1rem 0' }}>
        {(['upload', 'dashboard', 'memo', 'coworker'] as Tab[]).map((t) => (
          <button
            key={t}
            className={tab === t ? 'primary' : ''}
            onClick={() => setTab(t)}
            style={tab !== t ? { background: '#e2e8f0' } : undefined}
          >
            {t.charAt(0).toUpperCase() + t.slice(1)}
          </button>
        ))}
        <Link to={`/cases/${caseId}/financials`}>
          <button style={{ background: '#e2e8f0' }}>Financials</button>
        </Link>
      </div>

      {tab === 'upload' && (
        <div className="card">
          <h3>Upload Documents</h3>
          <div className="form-row">
            <label>Financial Statements (PDF / Excel)</label>
            <input type="file" multiple accept=".pdf,.xlsx,.xls" onChange={(e) => handleUpload('financials', e.target.files)} />
          </div>
          <div className="form-row">
            <label>Bank Statements (PDF)</label>
            <input type="file" multiple accept=".pdf" onChange={(e) => handleUpload('bank', e.target.files)} />
          </div>
          <div className="form-row">
            <label>GST (GSTR-3B PDF)</label>
            <input type="file" multiple accept=".pdf" onChange={(e) => handleUpload('gst', e.target.files)} />
          </div>
          <div className="form-row">
            <label>Bureau (Experian XML)</label>
            <input type="file" accept=".xml" onChange={(e) => handleUpload('bureau', e.target.files)} />
          </div>
          <button className="primary" onClick={handleAnalyze} disabled={analyzing}>
            {analyzing ? 'Analyzing...' : 'Run Analysis'}
          </button>
        </div>
      )}

      {tab === 'dashboard' && (
        <div className="grid">
          {assessment?.cards?.map((card: any, i: number) => (
            <div key={i} className="card">
              <span className="badge medium">{card.card_type}</span>
              <h3 style={{ marginTop: '0.5rem' }}>{card.summary_title}</h3>
              <p style={{ fontSize: '0.9rem', color: '#64748b' }}>{card.summary_subtitle}</p>
              {card.key_numbers?.slice(0, 4).map((kn: any, j: number) => (
                <p key={j} style={{ fontSize: '0.85rem' }}>
                  {kn.label}: <strong>{kn.value}</strong>
                </p>
              ))}
              {card.risks?.map((r: any, j: number) => (
                <p key={j}>
                  <span className={`badge ${r.severity}`}>{r.severity}</span> {r.message}
                </p>
              ))}
            </div>
          ))}
          {!assessment?.cards?.length && <p>Run analysis to see assessment cards.</p>}
        </div>
      )}

      {tab === 'memo' && (
        <div className="card memo-content">
          {memo ? <ReactMarkdown>{memo}</ReactMarkdown> : <p>Credit memo not generated yet.</p>}
        </div>
      )}

      {tab === 'coworker' && (
        <div className="chat-panel">
          <div style={{ padding: '1rem', borderBottom: '1px solid #e2e8f0' }}>
            <strong>AI Co-worker</strong>
          </div>
          <div className="chat-messages">
            {chatHistory.map((m, i) => (
              <div key={i} className={`msg ${m.role}`}>
                <ReactMarkdown>{m.content}</ReactMarkdown>
              </div>
            ))}
            <div ref={chatEnd} />
          </div>
          <div className="chat-input">
            <textarea
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              placeholder="Ask about metrics, gaps, or probe questions..."
              onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && (e.preventDefault(), handleChat())}
            />
            <button className="primary" onClick={handleChat}>Send</button>
          </div>
        </div>
      )}
    </div>
  )
}
