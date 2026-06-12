# CLAUDE.md

Project brief for Claude — **read this before any non-trivial change.**

## What this is

**CrediSage** — corporate credit analysis platform for Singapore SMEs and large
corporates. Current stage: **financial statements only.** Bank statement
analysis is queued for the next stage. GST and bureau ingestion are retired
from the live `/analyze` path; their old India-flavored implementations live
under `legacy/` for reference.

Long-form design lives at [`docs/PLATFORM_BLUEPRINT.md`](docs/PLATFORM_BLUEPRINT.md).

## Stack

- Backend: FastAPI on Python 3.14, venv at `.venv/`
- Frontend: React 18 + Vite + TypeScript (`frontend/`)
- LLM: Anthropic / OpenAI via `core/client/` (mock fallback when no key)
- PDF: `pdfplumber` (text) + optional `tesseract` + `pdftoppm` (OCR for image-only UFS)
- Persistence: file-based, every artifact lives under `cases/<case_id>/`

## Folder structure

```
.
├── CLAUDE.md                       # this file — single source of truth
├── README.md                       # 1-page quickstart, defers here
├── requirements.txt
├── .venv/                          # Python 3.14
│
├── api/                            # FastAPI HTTP layer
│   ├── main.py                     # app + route registration
│   ├── models.py                   # Pydantic request/response
│   ├── ingestion_sg.py             # /ingest/* + /cases/{id}/ingest/sg
│   ├── financials.py               # /cases/{id}/financials + /sources/{sid}/*
│   └── coworker.py                 # /cases/{id}/chat
│
├── core/                           # Domain logic — entire FS pipeline
│   ├── cases/case_store.py         # File-backed case lifecycle
│   ├── ingestion/                  # SG-aware FS ingestion
│   │   ├── classifier.py           # filename + first-page text → source_type
│   │   ├── canonical_map.py        # SFRS(I) taxonomy + label→code resolver
│   │   ├── acra_profile_extract.py # C223 + BM42A → CorporateProfile
│   │   ├── fs_text_extract.py      # z124 XBRL→PDF text extractor + flow classifier
│   │   ├── fs_ocr_extract.py       # UFS image-only PDF OCR fallback
│   │   ├── narrative_extract.py    # auditor / directors / notes → markdown
│   │   ├── block_writer.py         # FSExtraction → per-source blocks + merged blocks + GC
│   │   └── sg_pipeline.py          # end-to-end: classify → extract → blocks → index
│   ├── features/fs_analytics.py    # canonical periods → ratios + trends + agent payload
│   ├── agents/                     # LLM-call layer (slim, schema-validated)
│   │   ├── agent_runner.py         # AgentRunner + aggregate_cards (mock fallback)
│   │   └── fs_analysis.py          # run_fs_agent + run_industry_agent + run_qualitative_agent
│   ├── pipeline/analysis_pipeline.py  # FS-only: ingest → analytics → 3 agents → memo
│   ├── client/                     # LLM provider clients (anthropic, openai)
│   └── validation/                 # JSON-schema validators
│
├── prompts/                        # LIVE prompts (3 dimensions only)
│   ├── base_system_prompt.py
│   ├── fs_analysis_prompt.py
│   ├── industry_analysis_prompt.py
│   ├── qualitative_probe_prompt.py
│   └── context/                    # industry context markdown
│
├── schemas/                        # JSON schemas (cards, memos, inputs)
├── config/                         # YAML config + loader
│
├── frontend/                       # React + Vite + TypeScript
│   └── src/pages/                  # CaseList, CaseDetail, Financials, NewCase
│
├── scripts/                        # CLI utilities
│   ├── ingest_sg_demo.py           # Run SG pipeline against input/financials/
│   └── create_fs_template.py       # Generate the FS Excel template
│
├── tests/                          # pytest (live tests only — test_api_integration)
├── docs/PLATFORM_BLUEPRINT.md      # long-form design
├── reference/                      # Probe42 dashboard screenshots
├── input/                          # sample input data
├── templates/                      # FS Excel template
└── legacy/                         # retired stack — see legacy/README.md
    ├── features_pkg/               # was features/  (India FS/bank/GST/CIR)
    ├── agents_pkg/                 # was agents/   (legacy orchestrators)
    ├── core/{data,engine,output}/  # legacy LLM engine + transformers + memo writer
    ├── prompts/                    # ca/od/gst/bureau/credit_memo prompt builders
    ├── tests/                      # legacy test_cross_source + test_fs_ratios
    └── *.py                        # archived CLI scripts (main_cli, run_*, spike_*)
```

## Live data flow

```
1. UPLOAD
   Frontend POST /cases/{id}/upload (source_type=financials)
     → core/cases/case_store.save_upload
     → cases/<id>/raw/financials/<filename>

2. INGEST (new SG block layer)
   POST /cases/{id}/ingest/sg
     → core/ingestion/SGIngestionPipeline.ingest_path
       → classifier.discover_and_classify          (per file)
       → acra_profile_extract.extract_{c223,bm42a} (profile)
       → fs_text_extract.extract_fs_z124           (text path)
       → fs_ocr_extract.extract_fs_ufs             (OCR path)
       → narrative_extract.extract_narrative_sections
       → block_writer.write_source_blocks          (per source PDF)
       → block_writer.gc_stale_sources             (drop old <source_id>/ dirs)
       → block_writer.write_merged_blocks          (cross-source fused view)
     Outputs:
       cases/<id>/parsed/financials/<source_id>/
         manifest.json, raw.txt
         tables/{sofp,soci,socf}__{company,group}.{csv,json}
         narrative/{auditor_report,directors_statement}.md
         notes/note_NN_<slug>.md
       cases/<id>/parsed/financials/merged/
         {sofp,soci,socf}__{company,group}.{csv,json}  ← cross-source merge
       cases/<id>/parsed/financials/index.json         ← rollup
       cases/<id>/parsed/sg_ingestion.json
       cases/<id>/features/fs_periods_canonical.json

3. ANALYZE  (FS only this stage)
   POST /cases/{id}/analyze
     → core/pipeline/AnalysisPipeline.run
       → _ensure_financials_ingested            (runs step 2 if not done)
       → features/fs_analytics.build_fs_agent_data
       → agents/agent_runner.AgentRunner
         → fs_analysis.run_fs_agent
         → fs_analysis.run_industry_agent
         → fs_analysis.run_qualitative_agent
       → aggregate_cards
       → _generate_credit_memo
     Outputs:
       cases/<id>/features/fs_analytics.json
       cases/<id>/agents/{fs,industry,qualitative}.json
       cases/<id>/assessment.json
       cases/<id>/memo.md
```

## Setup

```powershell
# Backend
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn api.main:app --reload --port 8080
# → http://localhost:8080 — Docs: /docs

# Frontend
cd frontend
npm install
npm run dev   # http://localhost:5173 (proxies /api → :8080)
```

OCR (only for image-only UFS PDFs) requires two binaries on PATH:
`tesseract` (UB Mannheim installer) and `pdftoppm` (poppler-windows). Without
them, every z124 XBRL render and Excel template still ingests fine.

## Conventions

- **Case-scoped everything.** All persistent artifacts live under
  `cases/<case_id>/`. Never write to `input/`, `templates/`, or outside the
  case directory at runtime.
- **Block-writer pattern** for ingestion outputs: one source PDF → one
  `<source_id>/` directory with a `manifest.json` block index. CSVs are
  human-friendly, JSON sidecars carry the same data shaped for rendering
  (`display_order`, `section_path`, `indent_level`, `row_type`). Source IDs
  are the first 12 hex chars of the file's SHA-256 — idempotent re-ingest.
- **Canonical codes**: `bs_*` (balance sheet), `pl_*` (P&L / SoCI), `cf_*`
  (cash flow). New line items → extend `core/ingestion/canonical_map.py`
  `SYNONYMS` (keep `_FLAT` longest-first ordering).
- **LLM access** goes through `core/client/{claude,openai}_client.py` via
  `core/agents/agent_runner.AgentRunner`. Don't import `anthropic` or
  `openai` directly — the runner centralises retry, mock fallback, token
  logging, and provider selection.
- **Prompt files** in `prompts/` follow `<dimension>_analysis_prompt.py`
  exporting a `build_*_prompt(...) -> str`. There are exactly three live
  prompts (FS, industry, qualitative) plus the shared `base_system_prompt`.
- **Cross-source merge view**: when the analyst uploads multiple FS PDFs
  spanning different FYs, the pipeline emits a fused
  `parsed/financials/merged/<statement>__<perimeter>.json` per-cell-provenanced
  block. The per-source block view is for drill-down; the merged block is
  the default Probe42-style spread.

## Known issues / pending work

1. **Cross-source merge frontend rendering.** The merged blocks now exist on
   disk but the React `Financials.tsx` page still renders per-source only.
   Update it to switch between "Merged" (default) and per-source drill-down.
2. **Notes / narrative sections only appear for OCR'd UFS PDFs.** z124 XBRL
   renders are tables-only; no audit prose. Once OCR is installed, the
   `notes/` and `narrative/` blocks light up.
3. **Bank statement ingestion.** Queued for next stage; legacy implementation
   sits under `legacy/features_pkg/bank_*.py` for reference.
4. **Merge cell-conflict policy** is a heuristic (prefer source whose own
   most-recent FY equals the column FY, tiebreak by confidence). Watch for
   cases where it picks the wrong source and add explicit overrides if so.

## Don'ts

- **Don't add new `prompts/<dimension>_analysis_prompt.py`** without wiring
  it into `core/agents/fs_analysis.py` (or a sibling file) and the pipeline.
  Loose prompt files rot.
- **Don't import from `legacy/`** in any live module. If you need legacy
  functionality, port it cleanly into the new structure first.
- **Don't create files outside the case directory at runtime.** Tests can
  use `tempfile.TemporaryDirectory()`; the API must write only under
  `cases/<case_id>/`.
- **Don't introduce a new LLM library or provider.** Provider work goes
  through `core/client/` + `core/agents/agent_runner`.
- **Don't edit `templates/company_fs_template.xlsx` directly.** It's
  regenerated by `scripts/create_fs_template.py`.
