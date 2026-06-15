const API = '/api';

export interface Case {
  case_id: string;
  company_name: string;
  status: string;
  progress: number;
  country?: string;
  jurisdiction?: string;
  uen?: string;
  cin?: string;
  entity_type?: string;
  company_status?: string;
  primary_ssic_code?: string;
  primary_ssic_desc?: string;
  currency?: string;
  facility_type?: string;
  requested_limit?: string;
  relationship_manager?: string;
  priority?: string;
  onboarding_stage?: string;
  industry_hint?: string;
  created_at?: string;
  updated_at?: string;
}

export async function listCases(): Promise<Case[]> {
  const r = await fetch(`${API}/cases`);
  const d = await r.json();
  return d.cases || [];
}

export async function createCase(data: {
  company_name: string;
  industry_code?: string;
  industry_hint?: string;
  country?: string;
  jurisdiction?: string;
  uen?: string;
  entity_type?: string;
  company_status?: string;
  incorporation_date?: string;
  fiscal_year_end?: string;
  primary_ssic_code?: string;
  primary_ssic_desc?: string;
  registered_address?: string;
  currency?: string;
  facility_type?: string;
  requested_limit?: string;
  relationship_manager?: string;
  priority?: string;
  onboarding_stage?: string;
  cin?: string;
  pan?: string;
  fy_range?: string[];
}): Promise<Case> {
  const r = await fetch(`${API}/cases`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function getCaseStatus(caseId: string) {
  const r = await fetch(`${API}/cases/${caseId}/status`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function uploadFile(caseId: string, sourceType: string, file: File, fy?: string) {
  const fd = new FormData();
  fd.append('source_type', sourceType);
  if (fy) fd.append('fy', fy);
  fd.append('file', file);
  const r = await fetch(`${API}/cases/${caseId}/upload`, { method: 'POST', body: fd });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

// ---- Document intake ----

export interface UploadEntry {
  filename: string;
  size_bytes: number;
  uploaded_at: string;
  fy: string | null;
  extraction_status: string;
  note: string | null;
}

export interface UploadList {
  case_id: string;
  source: string;
  count: number;
  max: number | null;
  files: UploadEntry[];
}

export async function listUploads(caseId: string, sourceType: string): Promise<UploadList> {
  const r = await fetch(`${API}/cases/${caseId}/uploads/${sourceType}`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function deleteUpload(caseId: string, sourceType: string, filename: string) {
  const r = await fetch(
    `${API}/cases/${caseId}/uploads/${sourceType}/${encodeURIComponent(filename)}`,
    { method: 'DELETE' },
  );
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function patchUpload(
  caseId: string, sourceType: string, filename: string,
  patch: { fy?: string | null; extraction_status?: string; note?: string },
): Promise<UploadEntry> {
  const r = await fetch(
    `${API}/cases/${caseId}/uploads/${sourceType}/${encodeURIComponent(filename)}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    },
  );
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export function uploadPreviewUrl(caseId: string, sourceType: string, filename: string): string {
  return `${API}/cases/${caseId}/uploads/${sourceType}/${encodeURIComponent(filename)}`;
}

export interface ExtractionTriggerResponse {
  case_id: string;
  files_queued: string[];
  estimate_seconds: number;
  status: string;
  poll_url: string;
}

export async function triggerExtraction(caseId: string): Promise<ExtractionTriggerResponse> {
  const r = await fetch(`${API}/cases/${caseId}/extract`, { method: 'POST' });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function triggerSourceExtraction(
  caseId: string,
  sourceId: string,
): Promise<ExtractionTriggerResponse & { source_id: string; filename: string }> {
  const r = await fetch(`${API}/cases/${caseId}/sources/${sourceId}/extract`, { method: 'POST' });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

// ---- Credit Report (Stage 2) ----

export interface ReportStatus {
  status: 'not_run' | 'queued' | 'running' | 'completed' | 'failed';
  started_at?: string;
  completed_at?: string;
  template?: string;
  section_count?: number;
  duration_s?: number;
  error?: string;
  report_on_disk?: boolean;
}

export async function triggerReportGeneration(
  caseId: string,
  template: string = 'credit_analysis',
): Promise<{ case_id: string; template: string; status: string; poll_url: string; download_url: string }> {
  const r = await fetch(
    `${API}/cases/${caseId}/report/generate?template=${encodeURIComponent(template)}`,
    { method: 'POST' },
  );
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function getReportStatus(caseId: string): Promise<ReportStatus> {
  const r = await fetch(`${API}/cases/${caseId}/report/status`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export function reportDocxUrl(caseId: string): string {
  return `${API}/cases/${caseId}/report.docx`;
}

export interface ReportSection {
  code: string;
  number: number | null;
  title: string;
  markdown: string;
  html?: string;
  source?: 'llm' | 'deterministic' | 'error';
  error?: string;
}

export interface Report {
  case_id: string;
  template: string;
  generated_at: string;
  duration_s?: number;
  entity_name: string;
  fys: string[];
  section_count: number;
  sections: ReportSection[];
}

export async function getReport(caseId: string): Promise<Report> {
  const r = await fetch(`${API}/cases/${caseId}/report`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function regenerateReportSection(
  caseId: string,
  sectionCode: string,
  instruction?: 'tighten' | 'expand' | string,
): Promise<{ section: ReportSection }> {
  const r = await fetch(
    `${API}/cases/${caseId}/report/sections/${encodeURIComponent(sectionCode)}/regenerate`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ instruction: instruction || null }),
    },
  );
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

// ---- Review / approval ----

export interface DocumentMeta {
  entity: { name: string; uen?: string; ssic_code?: string; ssic_description?: string };
  fye: string;
  fy: string;
  framework: string;
  audited: boolean;
  consolidated: boolean;
  currency: string;
  currency_unit?: string;
  source_pdf?: string;
  source_id: string;
  extraction_method?: string;
}

export interface StatementColumn {
  id: string;
  perimeter: 'company' | 'group';
  fy: string;
  period_end?: string | null;
  currency?: string;
}

export interface StatementRowDoc {
  row_type: 'section_header' | 'line' | 'subtotal' | 'total' | 'spacer';
  label: string;
  raw_label?: string;
  canonical_code?: string | null;
  values?: Record<string, number | null>;
  indent_level: number;
  section_path?: string[];
  note_ref?: string | null;
  display_order?: number;
  page?: number;
  confidence?: number;
  flags?: string[];
}

export interface NoteTableColumn { id: string; label: string; type?: 'text' | 'number' | 'date' }
export interface NoteTableRow { [k: string]: string | number | null }
export interface NoteTable {
  caption?: string;
  footnote?: string;
  columns: NoteTableColumn[];
  rows: NoteTableRow[];
}
export interface NoteItem {
  no: number | string;
  title: string;
  page_range?: [number, number];
  subkind?: 'corporate_info' | 'policies' | 'note';
  markdown?: string;
  tables?: NoteTable[];
}

export type DocumentBlock =
  | { kind: 'cover' | 'corporate_info' | 'directors_statement' | 'auditor_report';
      title?: string; page_range: [number, number]; markdown: string }
  | { kind: 'statement'; type: 'sofp' | 'soci' | 'soce' | 'socf';
      title: string; page_range: [number, number]; currency_note?: string;
      columns: StatementColumn[]; rows: StatementRowDoc[] }
  | { kind: 'notes'; page_range?: [number, number]; items: NoteItem[] };

export interface SgFsDocument {
  document: DocumentMeta;
  blocks: DocumentBlock[];
}

export async function getSourceDocument(caseId: string, sourceId: string): Promise<SgFsDocument> {
  const r = await fetch(`${API}/cases/${caseId}/sources/${sourceId}/blocks/document.json`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export interface AuditEntry {
  at: string;
  user: string;
  path: (string | number)[];
  old_value: unknown;
  new_value: unknown;
  reason: string | null;
}

export async function patchDocument(
  caseId: string, sourceId: string,
  path: (string | number)[], value: unknown,
  opts?: { reason?: string; user?: string },
): Promise<{ ok: true; audit: AuditEntry; document: SgFsDocument }> {
  const r = await fetch(`${API}/cases/${caseId}/sources/${sourceId}/document`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, value, ...(opts || {}) }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function getDocumentAudits(caseId: string, sourceId: string): Promise<{ audits: AuditEntry[]; count: number }> {
  const r = await fetch(`${API}/cases/${caseId}/sources/${sourceId}/audits`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export interface ReviewState {
  status: 'pending' | 'approved' | 'rejected';
  approved_at?: string;
  approved_by?: string;
  rejected_at?: string;
  rejected_by?: string;
  notes?: string | null;
}

export async function approveSource(caseId: string, sourceId: string, notes?: string): Promise<{ review: ReviewState }> {
  const r = await fetch(`${API}/cases/${caseId}/sources/${sourceId}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ notes: notes || null }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function rejectSource(caseId: string, sourceId: string, notes?: string): Promise<{ review: ReviewState }> {
  const r = await fetch(`${API}/cases/${caseId}/sources/${sourceId}/reject`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ notes: notes || null }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function resetSourceStatus(caseId: string, sourceId: string): Promise<{ review: { status: 'pending' } }> {
  const r = await fetch(`${API}/cases/${caseId}/sources/${sourceId}/reset-status`, { method: 'POST' });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export interface ReviewStatusSummary {
  total: number;
  approved: number;
  pending: number;
  rejected: number;
  ready_to_analyze: boolean;
  blocked_reason: string | null;
  sources: {
    source_id: string;
    original_filename: string;
    entity: string;
    fys: string[];
    status: 'pending' | 'approved' | 'rejected';
    notes: string | null;
    decided_at: string | null;
  }[];
}

export async function getReviewStatus(caseId: string): Promise<ReviewStatusSummary> {
  const r = await fetch(`${API}/cases/${caseId}/review-status`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function runAnalysis(caseId: string) {
  const r = await fetch(`${API}/cases/${caseId}/analyze`, { method: 'POST' });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function getAssessment(caseId: string) {
  const r = await fetch(`${API}/cases/${caseId}/assessment`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function getMemo(caseId: string) {
  const r = await fetch(`${API}/cases/${caseId}/memo`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function sendChat(caseId: string, message: string, skill?: string) {
  const r = await fetch(`${API}/cases/${caseId}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, skill }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export interface CoworkerCitation {
  // Discriminator — populated by each tool. Other fields are optional and
  // sparsely set depending on the citation source.
  kind: 'wiki' | 'note' | 'report' | 'report_section' | 'fs_analytics'
      | 'ratio' | 'statement' | 'assessment' | 'probes' | string;
  // Provenance
  source_id?: string;
  source_file?: string;
  page_range?: [number, number] | number[] | null;
  wiki_path?: string;
  // Wiki / notes
  title?: string;
  note_no?: string | number;
  note_title?: string;
  // Report sections
  section_code?: string;
  section_title?: string;
  // Other metadata kept loose
  path?: string;
  ratio?: string;
  statement?: string;
  perimeter?: string;
  fys?: string[];
  // Routing context (added by the agent loop)
  tool_id?: string;
  tool?: string;
}

export type CoworkerEvent =
  | { type: 'delta'; text: string }
  | { type: 'tool_use'; id: string; name: string; input: Record<string, unknown> }
  | { type: 'tool_result'; id: string; name: string; output: { result?: unknown; citations?: CoworkerCitation[]; is_error?: boolean; error?: string }; is_error: boolean }
  | { type: 'done'; text: string; tool_calls: unknown[]; citations: CoworkerCitation[]; usage: { input_tokens: number; output_tokens: number } }
  | { type: 'error'; message: string };

export async function streamChat(
  caseId: string,
  message: string,
  onEvent: (event: CoworkerEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const r = await fetch(`${API}/cases/${caseId}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
    signal,
  });
  if (!r.ok || !r.body) throw new Error(await r.text().catch(() => `HTTP ${r.status}`));

  const reader = r.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let nl: number;
      while ((nl = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, nl);
        buffer = buffer.slice(nl + 2);
        const dataLine = frame.split('\n').find((l) => l.startsWith('data:'));
        if (!dataLine) continue;
        try {
          onEvent(JSON.parse(dataLine.slice(5).trim()) as CoworkerEvent);
        } catch {
          /* ignore malformed frame */
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

export async function getChatHistory(caseId: string) {
  const r = await fetch(`${API}/cases/${caseId}/chat/history`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

// ---- Analyst notes (per-case persistent memory injected into co-worker) ----

export interface AnalystNotes {
  case_id: string;
  content: string;
  length: number;
  last_updated: string | null;
}

export async function getAnalystNotes(caseId: string): Promise<AnalystNotes> {
  const r = await fetch(`${API}/cases/${caseId}/notes`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function saveAnalystNotes(caseId: string, content: string): Promise<{
  case_id: string; length: number; last_updated: string;
}> {
  const r = await fetch(`${API}/cases/${caseId}/notes`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

// ---- Dynamic co-worker suggestions ----

export interface CoworkerSuggestion {
  label: string;
  message: string;
}

export async function getCoworkerSuggestions(caseId: string): Promise<CoworkerSuggestion[]> {
  const r = await fetch(`${API}/cases/${caseId}/coworker/suggestions`);
  if (!r.ok) throw new Error(await r.text());
  const d = await r.json();
  return d.suggestions || [];
}

// ---- Financials (per-source labelled blocks) ----

export interface FinancialsSource {
  source_id: string;
  source_type: string;
  original_filename: string;
  original_path: string;
  entity: string;
  uen: string;
  framework: string;
  audited: boolean;
  consolidated: boolean;
  extraction_method: string;
  fys: string[];
  perimeters: string[];
  block_count: number;
}

export interface FinancialsBlock {
  source_id: string;
  kind: 'table' | 'merged_table' | 'narrative' | 'note';
  statement?: string;
  statement_name?: string;
  perimeter?: string;
  fys?: string[];
  csv?: string;
  json?: string;
  md?: string;
  pages?: number[];
  row_count?: number;
  title?: string;
  subkind?: string;
  note_no?: number;
}

export interface FinancialsIndex {
  started_at: string;
  finished_at: string;
  source_count: number;
  block_count: number;
  sources: FinancialsSource[];
  blocks: FinancialsBlock[];
}

export interface StatementRow {
  display_order: number;
  row_type: 'line' | 'subtotal' | 'total' | 'section_header';
  indent_level: number;
  section_path: string[];
  label: string;
  raw_label?: string;
  canonical_code: string | null;
  note_ref?: string | null;
  values: Record<string, number | null>;
  page?: number;
  confidence?: number;
  flags?: string[];
  provenance?: Record<string, { source_id?: string; page?: number; confidence?: number }>;
}

export interface StatementBlock {
  statement: string;
  statement_name: string;
  perimeter: string;
  fys: string[];
  currency: string;
  rows: StatementRow[];
}

export async function getFinancialsIndex(caseId: string): Promise<FinancialsIndex> {
  const r = await fetch(`${API}/cases/${caseId}/financials`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export interface FinancialAnalytics {
  entity: Record<string, any>;
  perimeter: string;
  fys: string[];
  summary_ratios: Record<string, number | null>;
  by_fy: Record<string, {
    fy: string;
    period_end?: string | null;
    currency?: string;
    raw: Record<string, number | null>;
    ratios: Record<string, number | null>;
  }>;
  trends: Record<string, number | null>;
  review_flags: Array<Record<string, any>>;
}

export async function getFinancialAnalytics(caseId: string, perimeter?: string): Promise<FinancialAnalytics> {
  const qs = perimeter ? `?perimeter=${encodeURIComponent(perimeter)}` : '';
  const r = await fetch(`${API}/cases/${caseId}/financials/analytics${qs}`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export function analyticsXlsxUrl(caseId: string): string {
  return `${API}/cases/${caseId}/analytics.xlsx`;
}

export async function getStatementBlock(
  caseId: string, sourceId: string, jsonPath: string,
): Promise<StatementBlock> {
  const r = await fetch(`${API}/cases/${caseId}/sources/${sourceId}/blocks/${jsonPath}`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function getNarrativeMarkdown(
  caseId: string, sourceId: string, mdPath: string,
): Promise<string> {
  const r = await fetch(`${API}/cases/${caseId}/sources/${sourceId}/blocks/${mdPath}`);
  if (!r.ok) throw new Error(await r.text());
  return r.text();
}

export interface LinkedNoteResponse {
  source_id: string;
  note_ref: string;
  note_key: string;
  note: {
    note_no?: string | number | null;
    title?: string | null;
    page_range?: [number, number] | null;
    wiki_path?: string | null;
    markdown: string;
  };
  related_rows: Array<Record<string, any>>;
}

export async function getLinkedNote(
  caseId: string, sourceId: string, noteRef: string,
): Promise<LinkedNoteResponse> {
  const r = await fetch(`${API}/cases/${caseId}/knowledge/notes/${sourceId}/${encodeURIComponent(noteRef)}`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export function csvUrl(caseId: string, sourceId: string, csvPath: string): string {
  return `${API}/cases/${caseId}/sources/${sourceId}/blocks/${csvPath}`;
}

export function pdfUrl(caseId: string, sourceId: string, page?: number): string {
  const base = `${API}/cases/${caseId}/sources/${sourceId}/pdf`;
  return page ? `${base}#page=${page}` : base;
}
