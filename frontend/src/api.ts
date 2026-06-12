const API = '/api';

export interface Case {
  case_id: string;
  company_name: string;
  status: string;
  progress: number;
  industry_hint?: string;
  created_at?: string;
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
  cin?: string;
  pan?: string;
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

export async function uploadFile(caseId: string, sourceType: string, file: File) {
  const fd = new FormData();
  fd.append('source_type', sourceType);
  fd.append('file', file);
  const r = await fetch(`${API}/cases/${caseId}/upload`, { method: 'POST', body: fd });
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

export async function getChatHistory(caseId: string) {
  const r = await fetch(`${API}/cases/${caseId}/chat/history`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
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
  kind: 'table' | 'narrative' | 'note';
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
  raw_label: string;
  canonical_code: string | null;
  note_ref: string | null;
  values: Record<string, number | null>;
  page: number;
  confidence: number;
  flags: string[];
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

export function csvUrl(caseId: string, sourceId: string, csvPath: string): string {
  return `${API}/cases/${caseId}/sources/${sourceId}/blocks/${csvPath}`;
}

export function pdfUrl(caseId: string, sourceId: string, page?: number): string {
  const base = `${API}/cases/${caseId}/sources/${sourceId}/pdf`;
  return page ? `${base}#page=${page}` : base;
}
