import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  CoworkerCitation, CoworkerEvent, CoworkerSuggestion,
  cancelPendingAction, confirmPendingAction,
  getAnalystNotes, getChatHistory, getCoworkerSuggestions,
  saveAnalystNotes, streamChat,
} from '../api'

type PendingAction = {
  token: string
  kind: string
  description: string
  payload: Record<string, unknown>
  status: 'pending' | 'approving' | 'approved' | 'cancelling' | 'cancelled' | 'failed'
  resultSummary?: string
  error?: string
}

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

// Fallback shown when the backend suggestions endpoint hasn't loaded yet
// (or returns an empty list). Keep it short and case-agnostic.
const FALLBACK_SUGGESTIONS: CoworkerSuggestion[] = [
  { label: 'Key risks', message: 'What are the most important credit risks?' },
  { label: 'Management probes', message: 'What questions should I ask management?' },
  { label: 'Liquidity view', message: 'Explain the liquidity position with source references.' },
]

function NotesIcon() {
  return (
    <svg className="coworker-svg" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M5 3h11l4 4v14H5z" />
      <path d="M16 3v5h4M8 12h8M8 16h8M8 8h4" />
    </svg>
  )
}

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
  | { role: 'assistant'; content: string; toolCalls: ToolCall[]; citations: CoworkerCitation[]; pendingActions: PendingAction[]; streaming?: boolean }

export default function CoworkerRail({ caseId, open, onOpenChange }: CoworkerRailProps) {
  const navigate = useNavigate()
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [showAllSuggestions, setShowAllSuggestions] = useState(false)
  const [turns, setTurns] = useState<Turn[]>([])
  const [expanded, setExpanded] = useState(false)
  const [suggestions, setSuggestions] = useState<CoworkerSuggestion[]>(FALLBACK_SUGGESTIONS)
  const [notesContent, setNotesContent] = useState<string>('')
  const [notesDraft, setNotesDraft] = useState<string>('')
  const [notesOpen, setNotesOpen] = useState(false)
  const [notesSaving, setNotesSaving] = useState(false)
  const [notesError, setNotesError] = useState<string>('')
  const endRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const visibleSuggestions = showAllSuggestions ? suggestions : suggestions.slice(0, 3)

  // Refresh suggestions from the backend — runs on case change and after
  // each turn completes (case state has shifted; the next-best question
  // probably has too).
  const refreshSuggestions = useCallback(() => {
    getCoworkerSuggestions(caseId)
      .then((s) => { if (s.length) setSuggestions(s) })
      .catch(() => { /* keep current pills */ })
  }, [caseId])

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
            : { role: 'assistant', content: m.content, toolCalls: [], citations: [], pendingActions: [] },
        )
        setTurns(hydrated)
      })
      .catch(() => setTurns([]))
  }

  useEffect(() => {
    loadHistory()
    refreshSuggestions()
    // Notes — load body once per case; the textarea draft tracks edits.
    getAnalystNotes(caseId)
      .then((n) => { setNotesContent(n.content || ''); setNotesDraft(n.content || '') })
      .catch(() => { setNotesContent(''); setNotesDraft('') })
  }, [caseId, refreshSuggestions])

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

  // Mutate one pending action by token, anywhere in the conversation.
  const updatePendingAction = (token: string, patch: Partial<PendingAction>) => {
    setTurns((prev) => prev.map((t) =>
      t.role !== 'assistant' ? t : {
        ...t,
        pendingActions: t.pendingActions.map((pa) =>
          pa.token === token ? { ...pa, ...patch } : pa,
        ),
      },
    ))
  }

  const approveAction = async (action: PendingAction) => {
    updatePendingAction(action.token, { status: 'approving', error: undefined })
    try {
      const r = await confirmPendingAction(caseId, action.token)
      if (!r.ok) {
        updatePendingAction(action.token, { status: 'failed', error: r.error || 'Action failed' })
        return
      }
      updatePendingAction(action.token, {
        status: 'approved',
        resultSummary: r.result?.summary || `Applied ${action.kind}`,
      })
      // Case state has shifted (notes/annotations/report); refresh suggestions.
      refreshSuggestions()
    } catch (e) {
      updatePendingAction(action.token, { status: 'failed', error: String(e) })
    }
  }

  const dismissAction = async (action: PendingAction) => {
    updatePendingAction(action.token, { status: 'cancelling' })
    try {
      await cancelPendingAction(caseId, action.token)
      updatePendingAction(action.token, { status: 'cancelled' })
    } catch (e) {
      updatePendingAction(action.token, { status: 'failed', error: String(e) })
    }
  }

  const ask = async (message?: string) => {
    const text = (message || input).trim()
    if (!text || busy) return
    setInput('')
    setBusy(true)
    setTurns((prev) => [
      ...prev,
      { role: 'user', content: text },
      { role: 'assistant', content: '', toolCalls: [], citations: [], pendingActions: [], streaming: true },
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
            // Detect preview tool results — these are write-side tools that
            // staged a pending action. Surface as a confirmation card.
            const preview = !event.is_error ? extractPreview(event.output?.result) : null
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
              pendingActions: preview
                ? [...t.pendingActions, { ...preview, status: 'pending' as const }]
                : t.pendingActions,
            }))
          } else if (event.type === 'done') {
            mutateLastAssistant((t) => ({
              ...t,
              content: event.text || t.content,
              citations: dedupeCitations(event.citations || []),
              streaming: false,
            }))
            // Case state has likely shifted (e.g. analyst-notes pickup
            // or new findings touched). Refresh suggestion pills so the
            // next-question prompts reflect the latest context.
            refreshSuggestions()
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
            className={notesContent.trim() ? 'has-notes' : ''}
            title={notesContent.trim() ? 'Edit analyst notes (active)' : 'Add analyst notes'}
            aria-label="Open analyst notes"
            onClick={() => { setNotesDraft(notesContent); setNotesError(''); setNotesOpen(true) }}
          >
            <NotesIcon />
            {notesContent.trim() && <span className="coworker-notes-dot" aria-hidden="true" />}
          </button>
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

      {notesOpen && (
        <NotesEditor
          draft={notesDraft}
          onDraftChange={setNotesDraft}
          saving={notesSaving}
          error={notesError}
          onCancel={() => { setNotesOpen(false); setNotesError(''); setNotesDraft(notesContent) }}
          onSave={async () => {
            setNotesSaving(true)
            setNotesError('')
            try {
              await saveAnalystNotes(caseId, notesDraft)
              setNotesContent(notesDraft)
              setNotesOpen(false)
            } catch (e) {
              setNotesError(String(e))
            } finally {
              setNotesSaving(false)
            }
          }}
        />
      )}

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
              {!m.streaming && m.pendingActions.length > 0 && (
                <div className="coworker-actions-stack">
                  {m.pendingActions.map((pa) => (
                    <PendingActionCard
                      key={pa.token}
                      action={pa}
                      onApprove={() => approveAction(pa)}
                      onDismiss={() => dismissAction(pa)}
                    />
                  ))}
                </div>
              )}
              {!m.streaming && m.citations.length > 0 && (
                <CitationChips
                  citations={m.citations}
                  onOpen={(c) => openCitation(c, caseId, navigate)}
                />
              )}
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

// ---- Pending actions (preview-then-confirm) ------------------------------

function extractPreview(result: unknown): {
  token: string; kind: string; description: string; payload: Record<string, unknown>;
} | null {
  if (!result || typeof result !== 'object') return null
  const r = result as Record<string, unknown>
  if (r.preview !== true) return null
  const token = typeof r.token === 'string' ? r.token : ''
  const kind = typeof r.kind === 'string' ? r.kind : ''
  if (!token || !kind) return null
  return {
    token,
    kind,
    description: typeof r.description === 'string' ? r.description : kind,
    payload: (r.payload as Record<string, unknown>) || {},
  }
}

const ACTION_KIND_LABELS: Record<string, { label: string; verb: string }> = {
  flag_for_committee: { label: 'Committee note', verb: 'Flag for committee' },
  annotate_finding: { label: 'Finding annotation', verb: 'Add annotation' },
  regenerate_report_section: { label: 'Report regenerate', verb: 'Regenerate section' },
}

function PendingActionCard({
  action, onApprove, onDismiss,
}: {
  action: PendingAction
  onApprove: () => void
  onDismiss: () => void
}) {
  const kindMeta = ACTION_KIND_LABELS[action.kind] || { label: action.kind, verb: 'Apply' }
  const settled = action.status === 'approved' || action.status === 'cancelled'
  const working = action.status === 'approving' || action.status === 'cancelling'

  return (
    <div className={`coworker-action-card status-${action.status}`}>
      <div className="coworker-action-head">
        <span className="coworker-action-kind">{kindMeta.label}</span>
        <span className={`coworker-action-status status-${action.status}`}>
          {statusLabel(action.status)}
        </span>
      </div>
      <div className="coworker-action-desc">{action.description}</div>
      {action.payload && Object.keys(action.payload).length > 0 && (
        <ActionPayloadPreview kind={action.kind} payload={action.payload} />
      )}
      {action.error && <div className="coworker-action-error">{action.error}</div>}
      {action.status === 'approved' && action.resultSummary && (
        <div className="coworker-action-summary">✓ {action.resultSummary}</div>
      )}
      {!settled && (
        <div className="coworker-action-buttons">
          <button
            type="button"
            className="coworker-action-cancel"
            disabled={working}
            onClick={onDismiss}
          >Cancel</button>
          <button
            type="button"
            className="coworker-action-approve"
            disabled={working}
            onClick={onApprove}
          >
            {action.status === 'approving' ? 'Applying…' : `Approve · ${kindMeta.verb}`}
          </button>
        </div>
      )}
    </div>
  )
}

function statusLabel(s: PendingAction['status']): string {
  switch (s) {
    case 'pending': return 'Awaiting approval'
    case 'approving': return 'Applying…'
    case 'approved': return 'Applied'
    case 'cancelling': return 'Cancelling…'
    case 'cancelled': return 'Cancelled'
    case 'failed': return 'Failed'
  }
}

function ActionPayloadPreview({
  kind, payload,
}: { kind: string; payload: Record<string, unknown> }) {
  // Render a kind-specific preview when we know the shape; fall back to a
  // generic key/value list otherwise.
  if (kind === 'flag_for_committee' && typeof payload.message === 'string') {
    return <blockquote className="coworker-action-preview">{payload.message}</blockquote>
  }
  if (kind === 'annotate_finding') {
    const card = payload.card_id as string | undefined
    const risk = payload.risk_id as string | undefined
    const comment = payload.comment as string | undefined
    return (
      <div className="coworker-action-preview">
        <div className="coworker-action-preview-meta">
          <strong>{card || '—'}</strong>{risk ? ` · ${risk}` : ''}
        </div>
        {comment && <blockquote>{comment}</blockquote>}
      </div>
    )
  }
  if (kind === 'regenerate_report_section') {
    const code = payload.section_code as string | undefined
    const instruction = payload.instruction as string | undefined
    return (
      <div className="coworker-action-preview">
        <div className="coworker-action-preview-meta">
          Section: <code>{code}</code>
        </div>
        {instruction && <div>Instruction: <em>{instruction}</em></div>}
      </div>
    )
  }
  return null
}

// ---- Citations -------------------------------------------------------------

function CitationChips({
  citations, onOpen,
}: { citations: CoworkerCitation[]; onOpen: (c: CoworkerCitation) => void }) {
  // Only render citations the analyst can actually click into; muting the
  // non-actionable ones (raw ratio paths, fs_analytics) keeps the row tight.
  const actionable = citations.filter(isActionable)
  if (actionable.length === 0) return null
  return (
    <div className="coworker-citations">
      <span className="coworker-citations-label">Sources</span>
      {actionable.map((c, idx) => (
        <button
          key={`${c.kind}-${idx}`}
          type="button"
          className={`coworker-citation coworker-citation-${c.kind}`}
          onClick={() => onOpen(c)}
          title={citationTooltip(c)}
        >
          <span className="coworker-citation-icon">{citationIcon(c)}</span>
          <span className="coworker-citation-label">{citationLabel(c)}</span>
        </button>
      ))}
    </div>
  )
}

function isActionable(c: CoworkerCitation): boolean {
  if (c.kind === 'wiki' || c.kind === 'note') {
    return !!(c.source_id && firstPage(c))
  }
  if (c.kind === 'report_section') return !!c.section_code
  return false
}

function firstPage(c: CoworkerCitation): number | undefined {
  if (!c.page_range) return undefined
  const arr = c.page_range as number[]
  if (!Array.isArray(arr) || arr.length === 0) return undefined
  return arr[0]
}

function citationIcon(c: CoworkerCitation): string {
  if (c.kind === 'note') return '📝'
  if (c.kind === 'wiki') return '📄'
  if (c.kind === 'report_section') return '📋'
  return '🔗'
}

function citationLabel(c: CoworkerCitation): string {
  if (c.kind === 'note') {
    const n = c.note_no != null ? `Note ${c.note_no}` : (c.note_title || 'Note')
    const p = firstPage(c)
    return p ? `${n} · p.${p}` : n
  }
  if (c.kind === 'wiki') {
    const file = c.source_file || c.title || 'Source'
    const p = firstPage(c)
    return p ? `${shorten(file, 18)} · p.${p}` : shorten(file, 22)
  }
  if (c.kind === 'report_section') {
    return c.section_title || c.section_code || 'Report section'
  }
  return c.kind
}

function citationTooltip(c: CoworkerCitation): string {
  if (c.kind === 'note') return `Open Note ${c.note_no ?? ''} in source ${c.source_file ?? ''}`
  if (c.kind === 'wiki') return `Open ${c.source_file ?? 'source'} at page ${firstPage(c) ?? ''}`
  if (c.kind === 'report_section') return `Jump to section "${c.section_title ?? c.section_code}" in the credit report`
  return ''
}

function shorten(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + '…' : s
}

function openCitation(
  c: CoworkerCitation,
  caseId: string,
  navigate: (to: string) => void,
): void {
  if ((c.kind === 'wiki' || c.kind === 'note') && c.source_id) {
    const page = firstPage(c)
    const qs = page ? `?page=${page}` : ''
    navigate(`/cases/${caseId}/review/${c.source_id}${qs}`)
    return
  }
  if (c.kind === 'report_section' && c.section_code) {
    const slug = c.section_title
      ? c.section_title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
      : c.section_code
    navigate(`/cases/${caseId}/report#${slug}`)
  }
}

function dedupeCitations(cs: CoworkerCitation[]): CoworkerCitation[] {
  const seen = new Set<string>()
  const out: CoworkerCitation[] = []
  for (const c of cs) {
    const k = citationDedupeKey(c)
    if (seen.has(k)) continue
    seen.add(k)
    out.push(c)
  }
  return out
}

function citationDedupeKey(c: CoworkerCitation): string {
  if (c.kind === 'wiki' || c.kind === 'note') {
    return `${c.kind}:${c.source_id ?? ''}:${firstPage(c) ?? ''}:${c.note_no ?? c.wiki_path ?? c.title ?? ''}`
  }
  if (c.kind === 'report_section') return `report_section:${c.section_code ?? ''}`
  return `${c.kind}:${c.path ?? ''}:${c.ratio ?? c.statement ?? ''}`
}

function NotesEditor({
  draft, onDraftChange, onSave, onCancel, saving, error,
}: {
  draft: string
  onDraftChange: (v: string) => void
  onSave: () => void
  onCancel: () => void
  saving: boolean
  error: string
}) {
  return (
    <div className="coworker-notes-overlay" onClick={onCancel}>
      <div className="coworker-notes-card" onClick={(e) => e.stopPropagation()}>
        <div className="coworker-notes-head">
          <strong>Analyst notes</strong>
          <span>Persistent memory the co-worker reads on every turn.</span>
        </div>
        <textarea
          className="coworker-notes-textarea"
          value={draft}
          onChange={(e) => onDraftChange(e.target.value)}
          placeholder={
            'Examples:\n' +
            '  • FY22 receivables: confirmed 272,994 (override extraction).\n' +
            '  • Sponsor confirmed FX is hedged — do not flag FX as risk.\n' +
            '  • Skip Note 18 — relates to a divested entity.'
          }
          autoFocus
          rows={10}
        />
        {error && <div className="coworker-notes-error">{error}</div>}
        <div className="coworker-notes-actions">
          <button type="button" className="secondary" onClick={onCancel} disabled={saving}>Cancel</button>
          <button type="button" className="primary" onClick={onSave} disabled={saving}>
            {saving ? 'Saving…' : 'Save notes'}
          </button>
        </div>
      </div>
    </div>
  )
}
