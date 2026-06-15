import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import {
  listUploads,
  uploadFile,
  deleteUpload,
  patchUpload,
  uploadPreviewUrl,
  triggerExtraction,
  getCaseStatus,
  type UploadEntry,
} from '../api'

const PENDING_STATUS = 'pending'
const ACTIVE_STATUSES = new Set(['queued', 'extracting'])
const TERMINAL_STATUSES = new Set(['ready', 'failed'])
const POLL_INTERVAL_MS = 3000

const SOURCE = 'financials'
const FY_OPTIONS = ['FY2020', 'FY2021', 'FY2022', 'FY2023', 'FY2024', 'FY2025']

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

function relativeTime(iso: string): string {
  if (!iso) return ''
  const dt = new Date(iso)
  const secs = (Date.now() - dt.getTime()) / 1000
  if (secs < 60) return 'just now'
  if (secs < 3600) return `${Math.floor(secs / 60)} min ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)} hr ago`
  return dt.toLocaleDateString()
}

export default function Intake() {
  const { caseId } = useParams<{ caseId: string }>()
  const navigate = useNavigate()

  const [companyName, setCompanyName] = useState('')
  const [files, setFiles] = useState<UploadEntry[]>([])
  const [maxFiles, setMaxFiles] = useState<number>(5)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [activeFilename, setActiveFilename] = useState<string | null>(null)
  const [extracting, setExtracting] = useState(false)
  const [startedAt, setStartedAt] = useState<number | null>(null)
  const pollTimerRef = useRef<number | null>(null)

  const refresh = useCallback(async () => {
    if (!caseId) return
    try {
      const [list, status] = await Promise.all([
        listUploads(caseId, SOURCE),
        getCaseStatus(caseId).catch(() => null),
      ])
      setFiles(list.files)
      setMaxFiles(list.max ?? 5)
      if (status?.company_name) setCompanyName(status.company_name)
      // Default the preview to the first file once we have one
      setActiveFilename((cur) => {
        if (cur && list.files.some((f) => f.filename === cur)) return cur
        return list.files[0]?.filename ?? null
      })
    } catch (e) {
      setError(String(e))
    }
  }, [caseId])

  useEffect(() => {
    refresh()
  }, [refresh])

  const remaining = useMemo(() => Math.max(0, maxFiles - files.length), [files, maxFiles])
  const pendingFiles = files.filter((f) => f.extraction_status === PENDING_STATUS)
  const activeFiles = files.filter((f) => ACTIVE_STATUSES.has(f.extraction_status))
  const ready = files.filter((f) => f.extraction_status === 'ready')
  const failed = files.filter((f) => f.extraction_status === 'failed')
  // "All done" only after extraction has produced terminal statuses — pending
  // files alone don't count as done.
  const allDone = files.length > 0 && activeFiles.length === 0 && pendingFiles.length === 0
  // Submit is allowed whenever there's a file to extract and nothing is
  // currently being extracted. Pending files are the very thing we want to
  // submit.
  const canSubmit = files.length > 0 && !busy && !extracting && activeFiles.length === 0
  const inFlight = extracting || activeFiles.length > 0

  // Poll while extraction is actually in flight. Pending files alone never
  // trigger polling — nothing to update until Submit fires.
  useEffect(() => {
    if (!inFlight) {
      if (pollTimerRef.current) {
        window.clearInterval(pollTimerRef.current)
        pollTimerRef.current = null
      }
      if (extracting && allDone) setExtracting(false)
      return
    }
    if (pollTimerRef.current) return
    pollTimerRef.current = window.setInterval(refresh, POLL_INTERVAL_MS)
    return () => {
      if (pollTimerRef.current) {
        window.clearInterval(pollTimerRef.current)
        pollTimerRef.current = null
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inFlight, allDone])

  const handleUpload = async (incoming: FileList | null) => {
    if (!caseId || !incoming || incoming.length === 0) return
    const list = Array.from(incoming).slice(0, remaining)
    if (list.length === 0) {
      setError(`Maximum of ${maxFiles} financial statements per case.`)
      return
    }
    setBusy(true); setError('')
    try {
      for (const f of list) {
        await uploadFile(caseId, SOURCE, f)
      }
      await refresh()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  const handleRemove = async (filename: string) => {
    if (!caseId) return
    if (!confirm(`Remove ${filename}?`)) return
    setBusy(true); setError('')
    try {
      await deleteUpload(caseId, SOURCE, filename)
      await refresh()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  const handleFyChange = async (filename: string, fy: string) => {
    if (!caseId) return
    setFiles((cur) => cur.map((f) => (f.filename === filename ? { ...f, fy: fy || null } : f)))
    try {
      await patchUpload(caseId, SOURCE, filename, { fy: fy || null })
    } catch (e) {
      setError(String(e))
      await refresh()
    }
  }

  const handleSubmit = async () => {
    // Phase-1 demo step: extraction wiring comes in the next iteration.
    // For now, surface the intent + nav forward.
    if (!caseId) return
    const untagged = files.filter((f) => !f.fy)
    if (untagged.length > 0) {
      if (!confirm(
        `${untagged.length} file(s) have no FY tag. Submit anyway? You can correct them in the review step.`,
      )) return
    }
    setBusy(true); setError('')
    try {
      await triggerExtraction(caseId)
      setExtracting(true)
      setStartedAt(Date.now())
      await refresh()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  if (!caseId) return null

  const activeFile = files.find((f) => f.filename === activeFilename) || files[0]

  return (
    <div className="intake">
      <div className="intake-header">
        <div>
          <p className="intake-crumb">
            <Link to="/">Cases</Link> · <Link to={`/cases/${caseId}`}>{companyName || caseId}</Link>
          </p>
          <h2>Document Intake</h2>
          <p className="intake-sub">
            Upload up to {maxFiles} financial statements — one per FY. Files render
            inline so you can verify them before sending for extraction.
          </p>
        </div>
        <div className="intake-progress">
          <div className="intake-counter">{files.length} <span>/ {maxFiles}</span></div>
          <div className="intake-counter-label">uploaded</div>
        </div>
      </div>

      {error && <div className="intake-error">{error}</div>}

      {(inFlight || allDone) && (
        <ExtractionBanner
          extracting={inFlight}
          allDone={allDone}
          inProgressCount={activeFiles.length}
          readyCount={ready.length}
          failedCount={failed.length}
          totalCount={files.length}
          startedAt={startedAt}
          onViewExtracted={() => navigate(`/cases/${caseId}/review`)}
        />
      )}

      <div className="intake-body">
        <div className="intake-left">
          <UploadZone
            disabled={busy || inFlight || remaining === 0}
            remaining={remaining}
            onFiles={handleUpload}
          />

          {files.length === 0 && (
            <div className="intake-empty">
              <p><strong>No documents uploaded yet.</strong></p>
              <p>Drop PDFs (z124 / UFS / C223 / BM42A) or click the zone above.</p>
            </div>
          )}

          <div className="intake-tiles">
            {files.map((f) => (
              <FileTile
                key={f.filename}
                file={f}
                active={f.filename === activeFile?.filename}
                onSelect={() => setActiveFilename(f.filename)}
                onRemove={() => handleRemove(f.filename)}
                onFyChange={(fy) => handleFyChange(f.filename, fy)}
              />
            ))}
          </div>

          <div className="intake-actions">
            <button
              className="primary intake-submit"
              disabled={!canSubmit}
              onClick={handleSubmit}
            >
              {busy
                ? 'Queuing…'
                : inFlight
                ? `Extracting ${ready.length}/${files.length}…`
                : allDone
                ? 'Re-run extraction'
                : `Submit ${files.length || ''} file${files.length === 1 ? '' : 's'} for extraction →`}
            </button>
            <span className="intake-help">
              {files.length === 0
                ? 'Upload at least one file to enable extraction.'
                : inFlight
                ? `Agentic extraction runs sequentially (~90s per file). ${ready.length} of ${files.length} ready.`
                : allDone
                ? failed.length > 0
                  ? `${failed.length} file${failed.length === 1 ? '' : 's'} failed — see tile note for details.`
                  : 'All files extracted. Continue to review.'
                : `Ready: ${files.length} file${files.length === 1 ? '' : 's'} pending. Cost ≈ $0.16 per filing.`}
            </span>
          </div>
        </div>

        <div className="intake-right">
          <div className="intake-preview-head">
            <strong>{activeFile?.filename || 'Preview'}</strong>
            {activeFile && (
              <span className="intake-preview-meta">
                {formatBytes(activeFile.size_bytes)} · {activeFile.fy || 'FY untagged'}
              </span>
            )}
          </div>
          {activeFile ? (
            <iframe
              key={activeFile.filename}
              title={`Preview ${activeFile.filename}`}
              className="intake-pdf"
              src={uploadPreviewUrl(caseId, SOURCE, activeFile.filename)}
            />
          ) : (
            <div className="intake-preview-empty">
              Select a file on the left to preview here.
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function ExtractionBanner({
  extracting, allDone, inProgressCount, readyCount, failedCount, totalCount, startedAt, onViewExtracted,
}: {
  extracting: boolean
  allDone: boolean
  inProgressCount: number
  readyCount: number
  failedCount: number
  totalCount: number
  startedAt: number | null
  onViewExtracted: () => void
}) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!extracting) return
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [extracting])

  const pct = totalCount === 0 ? 0 : Math.round(((readyCount + failedCount) / totalCount) * 100)
  const elapsedSec = startedAt ? Math.floor((now - startedAt) / 1000) : null
  const elapsed = elapsedSec === null
    ? null
    : `${Math.floor(elapsedSec / 60)}m ${String(elapsedSec % 60).padStart(2, '0')}s`

  if (allDone) {
    return (
      <div className="intake-banner success">
        <div className="intake-banner-title">
          ✓ Extraction complete — {readyCount} of {totalCount} ready
          {failedCount > 0 && <span style={{ color: '#991b1b' }}> · {failedCount} failed</span>}
        </div>
        <div className="intake-banner-actions">
          <button className="primary workflow-next-action" onClick={onViewExtracted}>View Data</button>
        </div>
      </div>
    )
  }
  return (
    <div className="intake-banner running">
      <div className="intake-banner-row">
        <div className="intake-banner-title">
          Extracting financial statements — {readyCount} / {totalCount} ready
          {failedCount > 0 && <span style={{ color: '#991b1b' }}> · {failedCount} failed</span>}
        </div>
        {elapsed && <div className="intake-banner-elapsed">{elapsed} elapsed</div>}
      </div>
      <div className="intake-banner-bar">
        <div className="intake-banner-bar-inner" style={{ width: `${pct}%` }} />
      </div>
      <div className="intake-banner-help">
        Agentic extraction takes ~90s per filing. {inProgressCount} in flight.
      </div>
    </div>
  )
}


function UploadZone({
  disabled, remaining, onFiles,
}: {
  disabled: boolean
  remaining: number
  onFiles: (f: FileList | null) => void
}) {
  const [over, setOver] = useState(false)
  return (
    <label
      className={`intake-drop ${over ? 'over' : ''} ${disabled ? 'disabled' : ''}`}
      onDragOver={(e) => { e.preventDefault(); if (!disabled) setOver(true) }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault(); setOver(false)
        if (!disabled) onFiles(e.dataTransfer.files)
      }}
    >
      <input
        type="file"
        multiple
        accept=".pdf,.xlsx,.xls"
        disabled={disabled}
        style={{ display: 'none' }}
        onChange={(e) => { onFiles(e.target.files); e.target.value = '' }}
      />
      <div className="intake-drop-text">
        <div className="intake-drop-title">
          {disabled && remaining === 0
            ? 'Maximum reached — remove a file to upload more'
            : 'Drop PDFs here or click to pick files'}
        </div>
        <div className="intake-drop-sub">
          {remaining > 0
            ? `${remaining} more slot${remaining === 1 ? '' : 's'} available`
            : 'Up to 5 files (one per FY)'}
        </div>
      </div>
    </label>
  )
}

function FileTile({
  file, active, onSelect, onRemove, onFyChange,
}: {
  file: UploadEntry
  active: boolean
  onSelect: () => void
  onRemove: () => void
  onFyChange: (fy: string) => void
}) {
  return (
    <div className={`intake-tile ${active ? 'active' : ''}`} onClick={onSelect}>
      <div className="intake-tile-header">
        <span className="intake-tile-name" title={file.filename}>{file.filename}</span>
        <button
          className="intake-tile-x"
          onClick={(e) => { e.stopPropagation(); onRemove() }}
          title="Remove"
        >×</button>
      </div>
      <div className="intake-tile-meta">
        <select
          className="intake-fy"
          value={file.fy || ''}
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => onFyChange(e.target.value)}
        >
          <option value="">— FY —</option>
          {FY_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
        <span className="intake-tile-size">{formatBytes(file.size_bytes)}</span>
        <span className="intake-tile-time">{relativeTime(file.uploaded_at)}</span>
      </div>
      <div className={`intake-tile-status status-${file.extraction_status}`}>
        {file.extraction_status === 'pending'
          ? 'Awaiting extraction'
          : file.extraction_status === 'queued'
          ? 'Queued…'
          : file.extraction_status === 'extracting'
          ? 'Extracting…'
          : file.extraction_status === 'ready'
          ? '✓ Extracted'
          : file.extraction_status === 'failed'
          ? '✗ Failed'
          : file.extraction_status}
      </div>
      {file.extraction_status === 'failed' && file.note && (
        <div className="intake-tile-error" title={file.note}>{file.note}</div>
      )}
    </div>
  )
}
