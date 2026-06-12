# CrediSage — Agentic Company Credit Analysis Platform
### Functional + Technical Blueprint (v1.1 — Singapore)

> A working plan to evolve the current `comany-credit-analysis` repo into a production-grade, agentic credit underwriting platform serving both **SMEs** and **Large Corporates** in **Singapore**. The document is split into two halves: (1) what a Singapore credit analyst actually does, and (2) how we build a platform that does it with them.

### Regulatory & Data Context — Singapore at a glance

| Domain | Authority / Source | Relevance |
|---|---|---|
| Banking & credit supervision | **MAS** (Monetary Authority of Singapore) | MAS Notice 612 (Credit Files, Grading & Provisioning), 626 (AML/CFT), 637 (Basel III capital), 656 (exposures), ICAAP |
| Company registry | **ACRA** (Accounting & Corporate Regulatory Authority) via **BizFile+** | UEN, Constitution, directors, charges, financial filings (XBRL) |
| Tax & GST | **IRAS** (Inland Revenue Authority of Singapore) | Income Tax Form C / C-S, GST F5 (quarterly) / F7 (correction) / F8 (final) |
| Statutory contributions | **CPF Board** | CPF contributions (employee headcount + payroll signal) |
| Credit bureau | **Credit Bureau Singapore (CBS)**, **Experian / DP SME Commercial Credit Bureau (DP Bureau)** | Consumer (directors) + commercial credit, bureau scores, defaults |
| Financial data exchange | **SGFinDex** | Consented retrieval of bank + CPF + IRAS data (analogue of India's AA) |
| Accounting framework | **SFRS** / **SFRS(I)** (IFRS-aligned), audited under **SSA** | Financial spreads, ECL Stage classification (SFRS(I) 9) |
| Industry classification | **SSIC 2020** | Industry overlay, peer benchmarking |
| SME definition (Enterprise SG) | Group annual sales ≤ **S$100M** **or** employment ≤ **200** | Eligibility for EFS, TBLP, scorecard routing |
| Govt-backed schemes | **Enterprise Singapore — EFS** (Working Capital Loan, Trade Loan, Fixed Assets, Mergers & Acquisitions), **TBLP** (where active) | Co-share risk on SME facilities |
| Listed corporates | **SGX** disclosures, **SGRS** for sustainability | Public-name due diligence |
| Data protection | **PDPA** | Consent capture, retention, cross-border transfer |
| Sanctions / AML | **MAS 626**, **UN/OFAC/EU** lists, **PEP** screening | Onboarding + ongoing |

---

## 0. Where We Stand Today

The repo already has the right skeleton:

| Layer | Today | Gap |
|---|---|---|
| Ingestion | FS Excel + PDF, HSBC bank PDF, GST return PDF, bureau XML | OCR fallback, multi-bank parsers (DBS/OCBC/UOB/SCB/HSBC/Citi/Maybank), ACRA filings, auditor's report, AR/AP ageing, management projections |
| Normalization | `fs_canonical`, `fs_ratios`, monthly bank features | Common Size statements, cash-flow reconstruction, schedule-level (DSCR, repayment), peer benchmarks |
| Agents | FS, CA/OD, GST, Bureau, Industry, Qualitative + mock mode | Working Capital, Cash Flow, Covenant, Stress-Test, Compliance, Fraud/Forensic, Memo-Writer |
| Cards | `card_schema.json`, aggregated `assessment_summary` | Drill-down evidence, citations to page/cell, analyst overrides |
| Co-worker | Skill router with 6 skills, history persisted | Tool-calling agent (read FS, run ratio, draft probe, modify memo), evidence pane, multi-turn planning |
| Frontend | 3 pages, single-grid card view | Financial spread workbench, document viewer with highlights, agent activity feed, side-by-side co-worker, memo editor |
| Persistence | File-based case store | Postgres + object store, audit trail, role-based access |

The blueprint below is designed to extend this skeleton — not replace it.

---

# Part A — Functional Document
## How a Credit Analyst Underwrites a Company

### A1. The Two Borrower Archetypes (Singapore)

| Dimension | **SME** (Enterprise SG definition) | **Large Corporate** |
|---|---|---|
| Size band | Group sales ≤ **S$100M** OR ≤ **200** employees | Above the SME threshold; often SGX-listed, regional HQ, group co. |
| Typical facilities | Overdraft, Working Capital Loan (incl. **EFS-WCL**), Trade Loan / **EFS-Trade**, LC/BG, Invoice/Receivables Financing, Equipment Financing, Property Loan | Bilateral / syndicated TL, **MTN / SGX-listed bonds / sukuk**, structured WC, project & acquisition finance, ESG-linked, cross-border (SGD/USD/RMB) |
| Information depth | Audited FS (often XBRL-filed to ACRA), GST F5, bank statements, CBS/DP Bureau, director KYC, CPF contribution history | Audited + consolidated FS, MD&A / Annual Report, external rating reports (S&P / Moody's / Fitch), debt schedule, board resolutions, ESG report (SGRS), regulatory filings |
| Key risks | Director/owner dependence, customer concentration, WC stress, thin equity, cross-border SEA exposure | Group exposure & contagion, holding-co cash flow, refinancing wall, contingent liabilities, regulatory & FX |
| Speed expectation | Same-day pre-screen, sanction within 5–10 business days | 4–8 weeks DD + tiered committee cycle |
| Decisioning | Scorecard + analyst review; EFS risk-share where applicable | Full credit memo, internal rating, committee approval, covenant pack |
| Models | PD scorecard (SME), cash-flow DSCR, bank-conduct behavioural | Internal rating model (PD / LGD / EAD aligned to MAS 637), scenario & stress, covenant model |
| Statutory grading | MAS Notice 612 classification (Pass / Special Mention / Substandard / Doubtful / Loss) | Same, with ECL Stage 1 / 2 / 3 under SFRS(I) 9 |

The platform must handle **both** with a shared engine and divergent workflows.

### A2. Document Inventory the Analyst Collects (Singapore)

1. **Identity & Constitution** — **UEN**, ACRA BizFile profile (incl. shareholders, directors, charges), **Constitution** (formerly MoA/AoA), partnership / LLP agreement, GST registration certificate, **MAS 626** KYC of directors/UBOs, PEP & sanctions screening, work-pass details where relevant.
2. **Financials** — audited FS for the last 3 FYs under **SFRS** / **SFRS(I)** (Balance Sheet, Income Statement, Statement of Cash Flows, Notes, **Independent Auditor's Report** under SSA, related-party schedule); **ACRA XBRL filing** where available; management accounts for the current period; 3–5 year projections (large corp); consolidation pack for groups.
3. **Tax & Statutory** — **IRAS** income tax (Form **C / C-S**), Notice of Assessment, **GST F5** quarterly returns (and F7 corrections / F8 final), **CPF** contribution statements (12 months — confirms employee count + payroll), IR8A, IR21 where relevant.
4. **Bank** — 12–18 month statements for all operating, OD, WC, and term-loan accounts across **DBS, OCBC, UOB, SCB, HSBC, Citi, Maybank** etc.; trade finance utilisation (LC/BG/TR/IF), repayment schedules, existing facility letters.
5. **Bureau & Ratings** — **CBS** consumer reports for directors / personal guarantors, **DP Bureau (SME Commercial Credit Bureau)** / **Experian Business** commercial reports, external ratings (S&P / Moody's / Fitch / domestic), ACRA charges, statutory demand / winding-up petition checks.
6. **Operational** — sales register, AR / AP ageing, inventory ageing, top-10 debtor / creditor list, sanctioned facilities letter, repayment schedule, collateral documents (property title with **SLA** check, valuation, charge filing), insurance.
7. **Qualitative** — management profiles, group chart, group exposure, **SGRS / ESG** disclosures, litigation & adverse-media, site-visit report, **EFS** eligibility & participating-FI risk-share details where applicable.

### A3. The Analytical Workflow (12 Steps)

1. **Case setup & KYC** — borrower master (UEN, ACRA pull), group structure, **UBO** identification, **MAS 626** AML/CFT screening (UN / OFAC / EU / PEP / adverse media).
2. **Document inventory check** — what's received vs what's required (gating).
3. **Financial spreading** — normalise BS / P&L / CF into a canonical schema under **SFRS(I)**, reclassify (split short-term vs long-term debt, lease liabilities under SFRS(I) 16, unusual items, prior-period restatements), produce Common-Size and Trend statements. Reconcile standalone vs consolidated.
4. **Ratio analysis & trends** — Liquidity (CR, QR, WC days), Leverage (D/E, TOL/TNW, Net Debt/EBITDA), Coverage (ICR, DSCR), Profitability (GM, OPM, NPM, ROCE, ROE), Efficiency (debtor/creditor/inventory days, cash conversion cycle), DuPont decomposition.
5. **Cash-flow & debt-service analysis** — operating CF quality, free cash flow, build a forward DSCR schedule using projections + existing debt amortisation; cross-check IRAS Form C taxable income for plausibility.
6. **Working-capital diagnosis** — WC gap, drawing-power calc using bank-policy haircuts on AR / inventory, holding-period reasonableness vs SSIC peer medians, FX exposure on AR / payables.
7. **Bank conduct review** — monthly turnover, ADB, EOD utilisation, returns / bounce (IDD/cheque), cash withdrawal share, ungranted overdrawings, classification triggers under **MAS Notice 612** (Special Mention indicators), GIRO failure patterns.
8. **GST / revenue triangulation** — **GST F5** taxable supplies vs FS revenue vs bank credits — reconcile gaps; CPF headcount vs payroll in FS as sanity check.
9. **Bureau & ratings review** — CBS director scores & defaults, DP Bureau / Experian Business commercial report (PD score, trade payment behaviour, derogatory events), external ratings, ACRA charges & directorship overlaps, statutory-demand / winding-up petition checks.
10. **Qualitative & industry overlay** — management background, succession, customer / supplier concentration (incl. SEA cross-border), SSIC industry cycle, regulatory headwinds, **SGRS / ESG** materiality, peer benchmarking.
11. **Stress testing & sensitivity** — −10/20/30% revenue, +200/400 bps SORA, SGD/USD FX shock, single-counterparty loss → impact on DSCR / ICR / leverage; map to MAS Notice 612 grade migration and SFRS(I) 9 ECL Stage.
12. **Recommendation & structuring** — facility mix (incl. **EFS** risk-share eligibility for SME), pricing reference (SORA + spread), tenor, security (incl. SLA-registered property charge, ACRA charge filing), covenants (financial + information + negative pledge), conditions precedent / subsequent, monitoring triggers.

### A4. The Deliverables

The analyst ultimately produces:

- **Credit Application / Credit Memo (CA / CIM)** — Executive summary, borrower & group profile (UEN, ACRA snapshot), industry view (SSIC), financial analysis, cash flow & debt service, security, qualitative, risks & mitigants, recommended structure & covenants, exceptions, MAS 612 grade.
- **Internal Risk Rating** — model output (SME scorecard or PD/LGD/EAD) + analyst override with justification; **SFRS(I) 9 ECL Stage** assignment.
- **Term-Sheet / Facility Letter** — facilities, SORA-linked pricing, security (incl. EFS risk-share where applicable), covenants, CP / CS, repayment.
- **Exception log** — deviations from credit policy and rationale.
- **Monitoring plan** — periodic covenant tests, post-disbursement triggers, watchlist criteria aligned to MAS 612 Special Mention.

These are exactly what the platform must produce — **with citation back to source documents** and an audit trail acceptable to MAS inspection.

### A5. What "Agentic" Adds

Agentic analysis means the platform is not a passive viewer; it actively:

- Detects missing/inconsistent data and asks the analyst (or the borrower's portal) for it.
- Spreads financials, computes ratios, runs reconciliations without human prompting.
- Drafts each section of the memo and flags evidence gaps with citations.
- Responds to analyst conversation ("what if revenue drops 15%", "compare to industry peer median", "draft 5 questions for the CFO call") by reading the case data, calling tools, and rendering an answer with sources.
- Learns analyst overrides (which thresholds to soften, which red flags to ignore by industry) and refines next-case behaviour.

The human analyst remains the **decision-maker** — the platform compresses 3–5 days of grunt work into 30–60 minutes of review and judgement.

---

# Part B — Platform Architecture

### B1. Guiding Principles

1. **Evidence-first.** Every number, every claim links back to a source page/cell. No floating LLM output.
2. **Schema-typed everything.** Ingestion → canonical schema → ratios → cards → memo, with JSON-schema validation at every hop.
3. **Deterministic core + LLM frosting.** Ratios, reconciliations, DSCR, stress tests run in code. LLM narrates and probes; LLM does not compute.
4. **Two surfaces, one engine.** SME and Corporate share the same data plane, differ only in workflow templates and rating models.
5. **Analyst-in-the-loop.** Every agent output is reviewable, editable, and overridable; overrides are persisted as the source of truth.
6. **Tool-using co-worker.** The chat is an agent with tools (`read_fs`, `run_stress`, `draft_probe`, `lookup_peer`, `edit_memo_section`), not a text-only chatbot.

### B2. High-Level Component Map

```
┌──────────────────────────────────────────────────────────────────┐
│                     ANALYST WORKBENCH (React)                    │
│  Case List │ Workspace │ Spreads │ Dashboard │ Memo │ Co-worker  │
└──────────────────────────────────────────────────────────────────┘
                              │ REST/WS
┌──────────────────────────────┴──────────────────────────────────┐
│                       API GATEWAY (FastAPI)                      │
│   /cases  /upload  /spreads  /assessment  /memo  /chat  /export  │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────┴──────────────────────────────────┐
│                       ORCHESTRATION LAYER                        │
│   Case FSM • Job Queue (Celery/RQ) • Agent Router • Tool Bus     │
└────┬───────────┬───────────┬───────────┬───────────┬────────────┘
     │           │           │           │           │
┌────┴───┐  ┌────┴────┐ ┌────┴────┐ ┌────┴────┐ ┌────┴─────┐
│Ingest  │  │Normalize│ │Analytics│ │Agents   │ │Co-worker │
│& Parse │  │& Canon. │ │& Models │ │(LLM)    │ │(Tool LLM)│
└────┬───┘  └────┬────┘ └────┬────┘ └────┬────┘ └────┬─────┘
     │           │           │           │           │
┌────┴───────────┴───────────┴───────────┴───────────┴─────────┐
│ DATA PLANE: Postgres (cases, manifests, audit) + Object store │
│ (raw + parsed) + Vector store (doc chunks) + Redis (jobs/cache)│
└──────────────────────────────────────────────────────────────┘
     │
┌────┴────────────────────────────────────────────────────────┐
│ EXTERNAL: Bureaus • GST • MCA • Bank APIs • Rating agencies │
└─────────────────────────────────────────────────────────────┘
```

### B3. Backend Modules

**1. Ingestion Service** (`features/`, extend)
- Multi-format parsers: PDF (pdfplumber + OCR fallback via `tesseract` / `paddleocr` for scans), Excel, XML, **XBRL** (ACRA filings), CSV, JSON. Bank statement parsers per bank (**DBS, OCBC, UOB, SCB, HSBC, Citi, Maybank**) registered through a plugin interface. **GST F5** and IRAS forms have dedicated extractors.
- File classifier: given an upload, predict source_type and period (FY for SFRS filings, calendar quarter for GST F5).
- Output: raw bytes in object store + extraction artefacts in `parsed/` with page-level coordinates for highlighting.

**2. Document Intelligence**
- Layout-aware extraction (LayoutLM or Azure DI / AWS Textract) to lift tables out of audited PDFs accurately.
- Schedule-level extractor for debt, related-party, contingent liabilities, ageing reports.
- Confidence score per extracted cell → drives review flags.

**3. Normalization & Canonicalization** (`core/data/`, extend)
- Map disparate FS line items → canonical chart of accounts aligned to **SFRS / SFRS(I)** taxonomy (already started in `fs_canonical`). Extend to: Common-Size statements, Indirect Cash Flow reconstruction when CF not provided, multi-year aligned spreads, lease reclassification under SFRS(I) 16.
- Period alignment: handle differing FY ends (common: 31 Dec, 31 Mar, 30 Jun), prior-period restatements, consolidated vs standalone, multi-currency (SGD reporting + USD/RMB subsidiaries).

**4. Analytics & Models** (new `core/analytics/`)
- Ratio engine (extend `fs_ratios`).
- Working capital engine — drawing-power calc using bank-policy haircuts on AR / inventory / payables, holding-period reasonableness vs peer median.
- DSCR / debt-service projector — combines existing debt schedule + projections; handles SORA-linked floating tranches.
- Stress engine — parametrised shocks (revenue, margin, **SORA**, **SGD/USD/RMB FX**, single-counterparty loss); returns delta on DSCR/ICR/leverage and projected **MAS 612 grade** & **SFRS(I) 9 ECL Stage** migration.
- Peer benchmark service — industry medians keyed by **SSIC 2020** code (ratios percentile table).
- Rating models — separate **SME scorecard** (consistent with Enterprise SG schemes) and **Corporate PD / LGD / EAD** aligned to MAS 637; both produce a grade + key drivers; mapping table to MAS 612 buckets.

**5. Agent Orchestration** (`agents/`, extend)
- Existing agents stay; add: **Working Capital agent**, **Cash Flow & DSCR agent**, **Covenant agent**, **Stress-Test agent**, **Forensic / Anomaly agent** (round-number bank txns, vendor-customer overlap, related-party flags), **Memo-Writer agent**.
- Replace the single `_run_agent` with an agent that can call deterministic tools (ratios, stress) instead of being asked to compute. Prompts ask for narrative + citations to features.
- Outputs validated against `card_schema.json` and the new `evidence_schema.json` (page#, table#, cell coords).

**6. Memo Service** (extend `_generate_credit_memo`)
- Section-by-section template (Borrower, Group, Industry, Financials, Cash Flow, Bank, GST, Bureau, Qualitative, Risks, Structure, Covenants, Recommendation).
- Each section produced by a sub-agent + citation block; assembled by Memo-Writer.
- Versioned drafts, editable by analyst, change tracking.

**7. Co-worker Service** (extend `api/coworker.py`)
- Move from regex skill detection to a tool-using LLM with this tool surface:
  - `get_ratio(metric, period)` · `compare_periods(metric)` · `run_stress(scenario)` · `get_cross_findings()` · `lookup_peer(industry, metric)` · `draft_probe(theme)` · `edit_memo(section, instruction)` · `get_evidence(claim_id)` · `flag_review(item, note)`.
- Multi-turn planner, session memory, citation-aware replies.
- Streaming responses to the UI.

**8. Case Lifecycle & Persistence**
- Move from file-only to **Postgres** for manifests, status, audit log, comments, overrides, rating decisions; **S3/Blob** for raw + parsed artefacts; **Redis** for job queue + cache; **pgvector** (or Qdrant) for doc chunks (so co-worker can do retrieval over the case corpus).
- Maintain backward-compatible `CaseStore` interface so the existing `/cases/...` API surface keeps working.

**9. Identity, Access, Audit**
- Roles: Analyst, Senior Analyst, Underwriter, Credit Officer, Admin.
- Every edit + agent output + override stamped with user + timestamp + reason → audit trail surfaces in UI and is exportable.

**10. Integrations (phase-gated, Singapore)**
- **Credit Bureau Singapore (CBS)** — director / personal-guarantor reports on consent.
- **DP Bureau (SME Commercial Credit Bureau)** / **Experian Business** — commercial credit, payment behaviour, derogatory events.
- **ACRA BizFile+** — auto-fetch corporate profile (UEN, Constitution, directors, shareholders, charges, ACRA-filed XBRL FS).
- **IRAS** APIs — Notice of Assessment, GST-registered status, GST F5 history (on consent).
- **SGFinDex** — consented retrieval of bank statements, CPF, IRAS — analogue of India's Account Aggregator.
- **CPF Board** data feed — contributions / employee count.
- **MAS Financial Institutions Directory** + sanctions / PEP screening providers (e.g. Dow Jones, Refinitiv World-Check).
- **SGX** for listed-corporate disclosures and announcements.
- Rating-agency APIs (S&P / Moody's / Fitch) for large corporates.

### B4. Frontend Modules (React + Vite, extend existing)

Replace the current single-page tab structure with a workspace shell:

```
┌────────────────────────────────────────────────────────────────────┐
│ Top bar: Case · Status · Risk Grade · Actions (Run · Export · ...) │
├──────────┬─────────────────────────────────────────┬───────────────┤
│ Side nav │ Main canvas                              │ Co-worker pane │
│ • Intake │ ┌─Document viewer ──┬─Workbench───────┐ │ Streaming chat │
│ • Spread │ │ PDF + highlights  │ Spread / Ratios │ │ Tool calls     │
│ • Cash   │ │                   │ Cards / Stress  │ │ Citations      │
│ • Bank   │ └───────────────────┴────────────────┘  │ Suggested probes│
│ • GST    │                                          │                │
│ • Bureau │                                          │                │
│ • Memo   │                                          │                │
│ • Risk   │                                          │                │
└──────────┴─────────────────────────────────────────┴───────────────┘
```

Key views to add:

- **Intake / Document checklist** — required-vs-received with status pills; drag-drop upload; per-file parser status.
- **Document Viewer** — PDF on the left, extracted tables on the right; click a number in any spread → it jumps to the source page with the cell highlighted.
- **Financial Spread Workbench** — editable canonical BS/P&L/CF grid, multi-year columns, override flags, audit trail.
- **Ratio & Trend Dashboard** — KPI tiles, sparklines, peer median overlay, policy thresholds, drill-down to formula and inputs.
- **Cash Flow & DSCR Studio** — debt schedule, projection inputs, DSCR curve, stress sliders (revenue, margin, rate, FX) with live re-compute.
- **Bank Conduct View** — monthly bars (credits/debits/ADB/EOD), bounces, RBI out-of-order flags, drill into transactions.
- **Cross-Source Reconciliation** — FS-vs-GST-vs-Bank revenue triangulation table with reconciling items.
- **Agent Activity Feed** — chronological stream of each agent's output, success/failure, time, tokens.
- **Memo Editor** — section navigator, rich-text per section, "regenerate" / "tighten" actions, citation pane, version history.
- **Risk & Recommendation** — score-card breakdown, override panel with justification, recommended structure & covenants.
- **Co-worker Panel** — persistent right-rail; tool-call trace visible; suggested actions; one-click "insert into memo".

### B5. Data Model (Postgres, key tables)

```
case(id, company_name, uen, gst_reg_no, segment {sme|corporate}, ssic_code, status, created_at, updated_at, owner_id)
case_member(case_id, user_id, role)
borrower(id, case_id, kind {entity|director|guarantor|ubo}, name, identifiers jsonb)  -- identifiers: {uen, nric/fin, passport, lei}
document(id, case_id, source_type, filename, object_uri, sha256, uploaded_at, classifier_conf, period, currency)
extraction(id, document_id, page, table_no, cell_path, value, confidence, reviewed_by)
fs_period(id, case_id, fy, type {standalone|consolidated}, currency, framework {sfrs|sfrs_i|other})
fs_line(id, fs_period_id, statement {bs|pl|cf}, canonical_code, raw_label, amount, source_extraction_id)
ratio(id, case_id, period, name, value, formula_id)
bank_account(id, case_id, bank {dbs|ocbc|uob|scb|hsbc|citi|maybank|other}, account_no, currency, type {ca|od|wcl|tl|tr|if})
bank_monthly_feature(id, bank_account_id, month, credits, debits, adb, eod, returns, mas612_signal, …)
gst_f5(id, case_id, gst_reg_no, period_quarter, standard_rated_supplies, zero_rated_supplies, exempt_supplies, total_supplies, output_tax, input_tax, source_extraction_id)
cpf_contribution(id, case_id, month, employee_count, total_contribution)
acra_profile(id, case_id, pulled_at, status_acra, paid_up_capital, directors jsonb, shareholders jsonb, charges jsonb)
bureau_report(id, case_id, source {cbs|dp_bureau|experian_business}, pulled_at, score, payload jsonb)
external_rating(id, case_id, agency {sp|moody|fitch|domestic}, grade, outlook, pulled_at, payload jsonb)
agent_run(id, case_id, agent, status, started_at, finished_at, tokens, cost, error)
finding(id, agent_run_id, severity, message, evidence jsonb, override jsonb)
memo_section(id, case_id, section_code, version, content_md, author {agent|user}, citations jsonb)
rating(id, case_id, model, score, grade, mas612_class, ecl_stage, drivers jsonb, override jsonb, decided_by)
facility(id, case_id, kind {od|wcl|tl|tr|lc|bg|if|ef|pl}, ccy, limit_sgd, pricing_ref {sora|sibor_legacy|fixed}, spread_bps, tenor_months, efs_scheme)
covenant(id, case_id, kind, threshold, source {policy|negotiated})
audit_event(id, case_id, user_id, action, target, payload jsonb, at)
chat_message(id, case_id, role, content, tool_calls jsonb, citations jsonb, at)
```

### B6. API Surface (delta on top of current routes)

```
# Cases
GET    /cases                          list (filter by status, segment, owner)
POST   /cases                          create (incl. segment, industry NIC)
GET    /cases/{id}                     manifest + summary
PATCH  /cases/{id}                     edit metadata, ownership

# Documents & ingestion
POST   /cases/{id}/documents           multipart upload (returns classifier guess)
GET    /cases/{id}/documents           list with parse status
GET    /documents/{id}/preview         PDF stream + bbox overlays
PATCH  /extractions/{id}               analyst correction

# Spreads & analytics
GET    /cases/{id}/spreads             canonical BS/PL/CF multi-year
PATCH  /cases/{id}/spreads             override cell, with reason
GET    /cases/{id}/ratios              full ratio set + thresholds + peer
POST   /cases/{id}/stress              run scenario { revenue:-0.15, rate:+0.02 } → impact

# Cross-source
GET    /cases/{id}/reconciliation/{fs_gst|fs_bank|gst_bank}

# Agents
POST   /cases/{id}/agents/{name}/run   re-run a specific agent
GET    /cases/{id}/agents              status + last outputs
GET    /cases/{id}/findings            aggregated findings (severity-sorted)
PATCH  /findings/{id}                  override / accept / dismiss with reason

# Memo
GET    /cases/{id}/memo                latest memo with sections
PATCH  /cases/{id}/memo/sections/{code}  edit / regenerate (mode = ai|manual)
POST   /cases/{id}/memo/export         docx/pdf

# Rating & decision
GET    /cases/{id}/rating              model + override
PATCH  /cases/{id}/rating              underwriter override + reason
POST   /cases/{id}/decision            approve / reject / refer-back

# Co-worker (tool-using)
POST   /cases/{id}/chat                streaming SSE; supports tool calls
GET    /cases/{id}/chat/history
```

### B7. Agent Inventory (target state)

| Agent | Inputs | Tools | Output card |
|---|---|---|---|
| FS | canonical FS (SFRS/SFRS(I)) + ratios | `peer_lookup`, `policy_lookup` | Financial health card + memo section |
| Working Capital | FS + bank + AR/AP ageing | `drawing_power_calc`, `holding_period_check` | WC card |
| Cash Flow & DSCR | FS + debt schedule + projections | `dscr_project`, `stress` (SORA, FX) | DSCR card |
| Bank Conduct | bank monthly features | `outliers`, `mas612_signal_check` | Bank card |
| GST | GST F5 + FS | `triangulate` (F5 supplies ↔ FS revenue ↔ bank credits) | GST card |
| Bureau | CBS + DP Bureau / Experian Business | `score_band`, `derogatory_check`, `directorship_overlap` | Bureau card |
| ACRA / Corporate Profile | BizFile pull | `charges_check`, `winding_up_check`, `directorship_pattern` | Corporate-profile card |
| Industry | manifest + SSIC macro snapshot | `industry_outlook` | Industry card |
| Forensic | FS + bank + ledger | `round_amounts`, `related_party_overlap`, `cpf_vs_payroll` | Anomaly card |
| Qualitative | aggregate of above | `probe_templates` | Probe card |
| Covenant | structure + thresholds | `policy_lookup`, `efs_eligibility` | Covenant block |
| Memo-Writer | all of the above | `cite`, `format` (MAS-inspectable) | Memo |

### B8. Co-worker Tool Schema (illustrative)

```python
TOOLS = [
  {"name": "get_ratio",        "params": {"metric": "str", "period": "str?"}},
  {"name": "compare_periods",  "params": {"metric": "str", "n": "int=3"}},
  {"name": "run_stress",       "params": {"scenario": "dict"}},
  {"name": "lookup_peer",      "params": {"industry_nic": "str", "metric": "str"}},
  {"name": "get_evidence",     "params": {"claim_id": "str"}},
  {"name": "get_findings",     "params": {"severity": "low|med|high?"}},
  {"name": "draft_probe",      "params": {"theme": "str", "n": "int=5"}},
  {"name": "edit_memo",        "params": {"section_code": "str", "instruction": "str"}},
  {"name": "flag_review",      "params": {"target": "str", "note": "str"}},
]
```

Every assistant reply carries `{reply, tool_trace[], citations[]}` so the UI can render the reasoning trail.

### B9. Security, Compliance, Audit (Singapore)

- **PDPA** compliance: lawful basis, purpose limitation, consent capture for CBS / DP Bureau / IRAS / SGFinDex / ACRA pulls; explicit retention schedule.
- PII at rest encryption (AES-256), KMS-managed keys (AWS KMS / Azure Key Vault — Singapore region), per-case access control.
- Full audit trail (every override, every agent run, every memo edit) exportable to PDF for **MAS inspection**; covers MAS Notice 612 grade decisions.
- **MAS Technology Risk Management (TRM)** guidelines: secure SDLC, vulnerability management, third-party risk for LLM providers.
- **AML/CFT** (MAS Notice 626): screening logs preserved, periodic re-screening, STR escalation hooks.
- Model governance: every agent has a model card (provider, version, prompt hash, thresholds, last validated); aligned with **MAS FEAT** principles (Fairness, Ethics, Accountability, Transparency) and **Veritas** toolkit guidance.
- Data residency: Singapore-region default; cross-border transfer flag per PDPA Section 26 (DPTR).
- Deterministic mock mode preserved for demos and offline regulator walkthroughs.

---

# Part C — Build Plan (Phased)

### Phase 0 — Stabilise (1 week)
- Postgres migration of `CaseStore` (interface preserved).
- Object store for raw uploads.
- Auth + role scaffolding (analyst/senior/credit officer).
- Restructure repo: `core/analytics/`, `core/agents/`, `core/ingestion/`, `core/persistence/`, `core/co_worker/`.

### Phase 1 — Functional MVP (3–4 weeks)
- Document classifier + checklist UI.
- Financial spread workbench (editable, multi-year).
- DSCR + stress + peer benchmark services with API.
- Replace `_generate_credit_memo` with section-agent + Memo-Writer.
- Tool-using co-worker (LLM with the tool schema above), streaming.
- New frontend shell: workspace + document viewer with highlights + memo editor.

### Phase 2 — Agentic Depth (4–6 weeks)
- Working Capital, Cash Flow, Forensic, Covenant agents.
- Cross-source reconciliation UI (FS-GST-Bank triangle).
- Override + audit trail end-to-end.
- SME score-card vs Corporate PD/LGD/EAD models.
- Export pack (memo.docx + appendices.zip with citations resolved).

### Phase 3 — Integrations + Scale (6–8 weeks, Singapore)
- **CBS**, **DP Bureau / Experian Business**, **ACRA BizFile+**, **IRAS**, **SGFinDex**, **CPF** integrations.
- Multi-bank parsers (**DBS, OCBC, UOB, SCB, HSBC, Citi, Maybank**).
- OCR fallback for scanned PDFs; **XBRL** parser for ACRA-filed FS.
- Vector index for doc retrieval; co-worker RAG over the case corpus.
- Committee workflow: refer-back, multi-approver, decision logs (MAS-inspectable).

### Phase 4 — Productisation
- Tenant isolation, SSO, SCIM.
- Policy editor (thresholds per segment/industry).
- Model governance dashboard.
- Borrower self-service portal (KYC, document collection, status).

---

# Part D — Demo Path on Current Inputs

Existing inputs in the repo (`input/financials/FY2022..FY2024` PDFs, `input/bankstatements/` **HSBC Singapore** PDFs, `GSTR3B_*` / Experian XML) are placeholders carried over from earlier work — for the Singapore demo we re-cast the same company as a **Singapore-incorporated Pte Ltd** (e.g. `GOIMPACT CAPITAL PARTNERS (SG) PL`, already referenced in `README.md`).

For the Singapore happy-path slice, swap the placeholders for one Singapore-native sample (an audited SFRS PDF, an HSBC SG statement set already present, a few GST F5 returns, a CBS / DP Bureau extract) and:

1. Create case "GOIMPACT CAPITAL PARTNERS (SG) PL" via the new shell — capture UEN, SSIC code, segment (SME).
2. Drop all input files; classifier auto-routes (FS / bank / GST F5 / bureau / ACRA); checklist shows green.
3. Run pipeline → agents produce cards (FS, Bank, GST, Bureau, ACRA-profile, Industry, Qualitative).
4. **Financial Spread workbench** shows canonical SFRS(I) BS/PL/CF — analyst can override and audit.
5. **DSCR Studio** with stress sliders (revenue −15%, **SORA +200 bps**, **SGD/USD −5%**) shows live DSCR / ICR impact and projected **MAS 612 grade** migration.
6. Open **Memo Editor**; each section has a citation pane that opens the source PDF page (e.g. F5 box-1 supplies, FS Note 22).
7. **Co-worker** answers "why is interest coverage flagged" by calling `get_ratio` + `get_evidence` and rendering both number and source.
8. Analyst overrides one ratio threshold → audit log entry → memo updates → final export to docx with MAS 612 classification and ECL Stage filled in.

That single happy-path slice is enough to validate the architecture and become the seed for everything in Part C.

> Cleanup note: rename / remove `experian_report_full_ph.xml` (Philippines-labelled file from earlier work) and replace `GSTR3B_*.pdf` (India GST forms) with Singapore **GST F5** samples once available. Until then they remain as parser-stress placeholders only.

---

# Part E — Open Questions for Stakeholders

1. Master grading scale — pure **MAS Notice 612** (Pass / Special Mention / Substandard / Doubtful / Loss), an internal 10/15-bucket PD scale mapped to 612, or both side-by-side?
2. Required regulatory artefacts at output — bank-specific Credit Memo template, ECL Stage tag (SFRS(I) 9), MAS 637 IRB inputs (PD / LGD / EAD)?
3. SLA expectations — SME same-day pre-screen + T+5 sanction, Corporate T+20? Drives async / queue design.
4. Hosting — Singapore region (AWS ap-southeast-1 / Azure SEA) only, or also private cloud / on-prem? Cross-border transfer needs under PDPA?
5. LLM provider preference — OpenAI, Anthropic, in-VPC open-weights (Llama / Qwen); per-tenant model selection; PDPA + MAS TRM third-party assessment.
6. Initial integrations for Phase 3 — pick the first two: **CBS**, **DP Bureau / Experian Business**, **ACRA BizFile+**, **IRAS**, **SGFinDex**, **CPF**.
7. **EFS** scheme support scope — which schemes are in-scope at launch (WCL, Trade Loan, Fixed Assets, M&A), and how risk-share is reflected in pricing / memo?
8. Listed-corporate scope — SGX disclosures, external ratings ingest, ESG (SGRS) materiality at launch or Phase 4?
9. **MAS FEAT / Veritas** assessment depth required for go-live (fairness, bias, explainability evidence)?

---

*This document is the living blueprint. Update each section as decisions land; the section codes are mirrored in the repo (`core/analytics`, `core/agents`, etc.) so changes here translate directly to engineering tickets.*
