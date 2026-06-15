import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CoworkerEvent, getChatHistory, streamChat } from '../api'

const MD_PLUGINS = [remarkGfm]

function Markdown({ children }: { children: string }) {
  return <ReactMarkdown remarkPlugins={MD_PLUGINS}>{children}</ReactMarkdown>
}

type CoworkerRailProps = {
  caseId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

function SparkleIcon() {
  return (
    <svg className="coworker-svg sparkle" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 3l1.55 5.2L18 10l-4.45 1.8L12 17l-1.55-5.2L6 10l4.45-1.8L12 3z" />
      <path d="M19 4l.72 2.28L22 7l-2.28.72L19 10l-.72-2.28L16 7l2.28-.72L19 4z" />
      <path d="M5 14l.9 2.1L8 17l-2.1.9L5 20l-.9-2.1L2 17l2.1-.9L5 14z" />
    </svg>
  )
}

function ChevronRightIcon() {
  return (
    <svg className="coworker-svg" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M9 5l7 7-7 7" />
    </svg>
  )
}

function ChevronDownIcon() {
  return (
    <svg className="coworker-svg" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M6 9l6 6 6-6" />
    </svg>
  )
}

function SendIcon() {
  return (
    <svg className="coworker-svg" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M5 4l15 8-15 8 3-8-3-8z" />
    </svg>
  )
}

function ExpandIcon() {
  return (
    <svg className="coworker-svg" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 10V4h6M20 14v6h-6M4 4l7 7M20 20l-7-7" />
    </svg>
  )
}

function CollapseIcon() {
  return (
    <svg className="coworker-svg" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M10 4v6H4M14 20v-6h6M4 10l7-7M20 14l-7 7" />
    </svg>
  )
}

const suggestions = [
  {
    label: 'Do I need to report a ...',
    message: 'Do I need to report any material risks to credit committee?',
  },
  {
    label: 'What are the regulato...',
    message: 'What are the regulatory considerations I should review?',
  },
]

const extraSuggestions = [
  {
    label: 'Generate audit checklist',
    message: 'Generate an audit checklist for this case.',
  },
  {
    label: 'Summarize key risks',
    message: 'Summarize the key credit risks for this case.',
  },
  {
    label: 'Draft questions',
    message: 'Draft follow-up questions for the relationship manager.',
  },
  {
    label: 'Explain ratios',
    message: 'Explain the most important ratio movements with source references.',
  },
]

type ToolCall = {
  id: string
  name: string
  input: Record<string, unknown>
  status: 'running' | 'ok' | 'error'
  output?: unknown
  error?: string
}

type Turn =
  | { role: 'user'; content: string }
  | { role: 'assistant'; content: string; toolCalls: ToolCall[]; streaming?: boolean }

export default function CoworkerRail({ caseId, open, onOpenChange }: CoworkerRailProps) {
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [showAllSuggestions, setShowAllSuggestions] = useState(false)
  const [turns, setTurns] = useState<Turn[]>([])
  const [expanded, setExpanded] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const visibleSuggestions = showAllSuggestions ? [...suggestions, ...extraSuggestions] : suggestions

  // Escape to leave fullscreen; also lock background scroll while expanded.
  useEffect(() => {
    if (!expanded) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setExpanded(false)
    }
    document.addEventListener('keydown', onKey)
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = prevOverflow
    }
  }, [expanded])

  // Collapsing the rail also exits fullscreen so the two states don't desync.
  useEffect(() => {
    if (!open) setExpanded(false)
  }, [open])

  const loadHistory = () => {
    getChatHistory(caseId)
      .then((h) => {
        const hydrated: Turn[] = (h.history || []).map((m: { role: string; content: string }) =>
          m.role === 'user'
            ? { role: 'user', content: m.content }
            : { role: 'assistant', content: m.content, toolCalls: [] },
        )
        setTurns(hydrated)
      })
      .catch(() => setTurns([]))
  }

  useEffect(() => {
    loadHistory()
  }, [caseId])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns, open])

  useEffect(
    () => () => {
      abortRef.current?.abort()
    },
    [],
  )

  const mutateLastAssistant = (mut: (t: Extract<Turn, { role: 'assistant' }>) => Turn) => {
    setTurns((prev) => {
      const copy = [...prev]
      for (let i = copy.length - 1; i >= 0; i--) {
        const t = copy[i]
        if (t.role === 'assistant') {
          copy[i] = mut(t)
          break
        }
      }
      return copy
    })
  }

  const ask = async (message?: string) => {
    const text = (message || input).trim()
    if (!text || busy) return
    setInput('')
    setBusy(true)
    setTurns((prev) => [
      ...prev,
      { role: 'user', content: text },
      { role: 'assistant', content: '', toolCalls: [], streaming: true },
    ])

    const controller = new AbortController()
    abortRef.current = controller

    try {
      await streamChat(
        caseId,
        text,
        (event: CoworkerEvent) => {
          if (event.type === 'delta') {
            mutateLastAssistant((t) => ({ ...t, content: t.content + event.text }))
          } else if (event.type === 'tool_use') {
            // The model often narrates a plan ("Let me look up...") right
            // before calling a tool. That preamble is noise to the analyst —
            // drop the accumulated text so only the final-round answer is
            // visible. The status hint takes over until the next text streams.
            mutateLastAssistant((t) => ({
              ...t,
              content: '',
              toolCalls: [
                ...t.toolCalls,
                { id: event.id, name: event.name, input: event.input, status: 'running' },
              ],
            }))
          } else if (event.type === 'tool_result') {
            mutateLastAssistant((t) => ({
              ...t,
              toolCalls: t.toolCalls.map((tc) =>
                tc.id === event.id
                  ? {
                      ...tc,
                      status: event.is_error ? 'error' : 'ok',
                      output: event.output?.result,
                      error: event.is_error ? event.output?.error || 'tool error' : undefined,
                    }
                  : tc,
              ),
            }))
          } else if (event.type === 'done') {
            mutateLastAssistant((t) => ({
              ...t,
              content: event.text || t.content,
              streaming: false,
            }))
          } else if (event.type === 'error') {
            mutateLastAssistant((t) => ({
              ...t,
              content: (t.content ? t.content + '\n\n' : '') + `_Error: ${event.message}_`,
              streaming: false,
            }))
          }
        },
        controller.signal,
      )
    } catch (e) {
      mutateLastAssistant((t) => ({
        ...t,
        content: (t.content ? t.content + '\n\n' : '') + `_Error: ${String(e)}_`,
        streaming: false,
      }))
    } finally {
      mutateLastAssistant((t) => ({ ...t, streaming: false }))
      setBusy(false)
      abortRef.current = null
    }
  }

  if (!open) {
    return (
      <aside className="coworker-rail collapsed">
        <button
          className="coworker-icon-button"
          onClick={() => onOpenChange(true)}
          title="Open CrediSage co-worker"
          aria-label="Open CrediSage co-worker"
        >
          <SparkleIcon />
          <span>CrediSage</span>
        </button>
      </aside>
    )
  }

  const railNode = (
    <aside className={`coworker-rail ${expanded ? 'expanded' : ''}`}>
      <div className="coworker-head">
        <div className="coworker-brand-lockup">
          <SparkleIcon />
          <div className="coworker-brand">CrediSage</div>
        </div>
        <div className="coworker-actions">
          <button
            type="button"
            title={expanded ? 'Restore rail' : 'Expand to fullscreen'}
            aria-label={expanded ? 'Restore rail' : 'Expand to fullscreen'}
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded ? <CollapseIcon /> : <ExpandIcon />}
          </button>
          <button type="button" title="Collapse co-worker" aria-label="Collapse co-worker" onClick={() => onOpenChange(false)}>
            <ChevronRightIcon />
          </button>
        </div>
      </div>

      <div className="coworker-messages">
        {turns.map((m, i) =>
          m.role === 'user' ? (
            <div key={i} className="coworker-msg user">
              <Markdown>{m.content}</Markdown>
            </div>
          ) : (
            <div key={i}>
              <div className="coworker-msg assistant">
                {m.content ? (
                  <Markdown>{m.content}</Markdown>
                ) : m.streaming ? (
                  <span className="coworker-typing">
                    {assistantStatusText(m)}
                  </span>
                ) : null}
              </div>
            </div>
          ),
        )}
        <div ref={endRef} />
      </div>

      <div className="coworker-compose">
        <div className="coworker-input-shell">
          <textarea
            value={input}
            placeholder="Ask Your Question..."
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                ask()
              }
            }}
          />
          <button
            className="coworker-send"
            disabled={busy || !input.trim()}
            onClick={() => ask()}
            title="Send question"
            aria-label="Send question"
          >
            <SendIcon />
          </button>
        </div>
        <div className="coworker-suggestions">
          {visibleSuggestions.map((suggestion) => (
            <button key={suggestion.label} onClick={() => ask(suggestion.message)}>
              {suggestion.label}
            </button>
          ))}
          <button className="coworker-more" type="button" onClick={() => setShowAllSuggestions((v) => !v)}>
            {showAllSuggestions ? 'Show Less' : 'See More'}
            <ChevronDownIcon />
          </button>
        </div>
      </div>
    </aside>
  )

  if (expanded) {
    return (
      <div className="coworker-overlay" onClick={() => setExpanded(false)}>
        <div className="coworker-overlay-card" onClick={(e) => e.stopPropagation()}>
          {railNode}
        </div>
      </div>
    )
  }
  return railNode
}

function assistantStatusText(turn: Extract<Turn, { role: 'assistant' }>): string {
  // Generic, friendly hint while the model is still working but hasn't
  // emitted any user-facing text yet. We intentionally do NOT surface tool
  // names — analysts don't need to see the plumbing.
  if (!turn.streaming) return '…'
  const inFlight = turn.toolCalls.filter((tc) => tc.status === 'running').length
  if (inFlight > 0) return 'Looking up case data…'
  if (turn.toolCalls.length > 0) return 'Drafting answer…'
  return 'Thinking…'
}
