import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { getChatHistory, sendChat } from '../api'

type CoworkerRailProps = {
  caseId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

export default function CoworkerRail({ caseId, open, onOpenChange }: CoworkerRailProps) {
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [history, setHistory] = useState<{ role: string; content: string }[]>([])
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    getChatHistory(caseId)
      .then((h) => setHistory(h.history || []))
      .catch(() => setHistory([]))
  }, [caseId])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [history, open])

  const ask = async (message?: string) => {
    const text = (message || input).trim()
    if (!text || busy) return
    setInput('')
    setBusy(true)
    setHistory((h) => [...h, { role: 'user', content: text }])
    try {
      const r = await sendChat(caseId, text)
      setHistory((h) => [...h, { role: 'assistant', content: r.reply }])
    } catch (e) {
      setHistory((h) => [...h, { role: 'assistant', content: `Error: ${e}` }])
    } finally {
      setBusy(false)
    }
  }

  if (!open) {
    return (
      <aside className="coworker-rail collapsed">
        <button className="coworker-icon-button" onClick={() => onOpenChange(true)} title="Open Co-worker">
          ☰
        </button>
      </aside>
    )
  }

  return (
    <aside className="coworker-rail">
      <div className="coworker-head">
        <button className="coworker-menu" onClick={() => onOpenChange(false)} title="Collapse Co-worker">☰</button>
        <div>
          <div className="coworker-brand">CrediSage Co-worker</div>
          <div className="coworker-sub">Case-aware analyst assistant</div>
        </div>
      </div>

      <div className="coworker-messages">
        {history.length === 0 && (
          <div className="coworker-empty">
            Ask about extracted notes, ratios, source pages, or what to check before committee.
          </div>
        )}
        {history.map((m, i) => (
          <div key={i} className={`coworker-msg ${m.role}`}>
            <ReactMarkdown>{m.content}</ReactMarkdown>
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <div className="coworker-compose">
        <textarea
          value={input}
          placeholder="Ask your question..."
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              ask()
            }
          }}
        />
        <button className="primary coworker-send" disabled={busy || !input.trim()} onClick={() => ask()}>
          Send
        </button>
        <div className="coworker-suggestions">
          <button onClick={() => ask('What are the key risks from the financial statements?')}>Key FS risks</button>
          <button onClick={() => ask('Show me questions to ask management based on the notes.')}>Management questions</button>
          <button onClick={() => ask('Explain the liquidity position with source references.')}>Liquidity view</button>
        </div>
      </div>
    </aside>
  )
}
