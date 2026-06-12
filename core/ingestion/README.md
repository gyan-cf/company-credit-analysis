# SG Ingestion Module — `core/ingestion/`

Singapore-aware ingestion pipeline for ACRA and SFRS(I) financial-statement
documents. Designed for the demo path: drop the contents of `input/financials/`
in, get back a clean canonical FS spread + corporate profile + review flags.

## Files

| File | Responsibility |
|---|---|
| `classifier.py`            | Route a file to its ACRA / FS type by filename pattern + first-page text. Handles zip recursion. |
| `canonical_map.py`         | SFRS(I) canonical line-item taxonomy (SoFP / SoCI / SoCF) + label→code resolver with header-phrase rejection. |
| `acra_profile_extract.py`  | Parse C223 (Annual Return) + BM42A (Annual Filing cover sheet). Pulls UEN, SSIC, directors, shareholders, secretaries, registered charges, paid-up capital, audit-exemption, AGM, accounting standards. |
| `fs_text_extract.py`       | z124 XBRL→PDF financial-statement spreader. Detects Group/Company perimeter columns, multi-year layouts, period-end date headers; maps each line to a canonical code. |
| `fs_ocr_extract.py`        | OCR fallback (pdftoppm + tesseract) for image-only UFS PDFs. Reuses the same parser as the text path. Supports `page_range` for fast demos. |
| `sg_pipeline.py`           | Orchestrator: discover → classify → extract → merge profiles + FS periods → emit `IngestionResult`. |

## CLI demo

```bash
# Text-only path (fast, no OCR)
python scripts/ingest_sg_demo.py --no-ocr

# Full path including OCR of image-only UFS PDFs
python scripts/ingest_sg_demo.py

# Persist the full IngestionResult JSON
python scripts/ingest_sg_demo.py -o cases/demo/parsed/sg_ingestion.json
```

## API endpoints

Wired into `api/main.py`:

```
POST /ingest/classify                    # classify a single uploaded file
POST /ingest/run                         # run the pipeline over a server-side folder
POST /cases/{case_id}/ingest/sg          # case-scoped run, persists to case store
GET  /cases/{case_id}/ingest/sg          # fetch last persisted result
```

## Result shape

```jsonc
{
  "started_at": "...",
  "finished_at": "...",
  "summary": {
    "entity": "GOIMPACT CAPITAL PARTNERS (SINGAPORE)",
    "uen": "202037175R",
    "ssic": "85409 — Training courses n.e.c",
    "audited": false,
    "small_co_exempt": true,
    "paid_up_capital_sgd": 4750,
    "charges_count": 1,
    "files": {"total": 8, "by_type": {...}},
    "fs_periods_captured": 4,
    "fy_metrics": [{"perimeter":"company","fy":"FY2023","revenue":569675,"pat":-1010471, ...}]
  },
  "classifications":   [...],     // per-file routing
  "profile":           {...},     // merged ACRA corporate profile
  "fs_extractions":    [...],     // raw per-file FSExtraction.to_dict()
  "periods":           [...],     // canonical multi-year spread
  "review_flags":      [...],
  "errors":            [...]
}
```

## Demo output (against `input/financials/`)

Entity: **GOIMPACT CAPITAL PARTNERS (SINGAPORE) PTE. LTD.** — UEN `202037175R`, SSIC `85409` (Training courses n.e.c.), paid-up S$4,750, audit-exempt small company, 1 registered charge (HSBC, 2010).

| Perim | FY | Revenue | PBT / PAT | Total Assets | Equity |
|---|---|---:|---:|---:|---:|
| company | FY2023 | 569,675 | (1,010,471) | 2,117,763 | 1,731,449 |
| company | FY2022 | 414,943 | (1,404,517) | 1,516,012 | 1,287,241 |
| company | FY2021 |  45,201 | (128,321) | 2,613,797 | (128,320) |
| group   | FY2023 | 583,626 | (1,264,399) | 1,867,663 | 1,472,592 |
| group   | FY2024 (OCR) | — | (983,684) | — | — |

FY2024 numbers are recovered via the OCR path from the image-only UFS PDF in `_Archives/`. OCR and text extracts cross-validate (e.g. FY2023 PBT = -1,264,399 from both routes).

## Known limitations / next iterations

- **Multi-line labels.** When the audited / unaudited template wraps the PAT label across 3-4 physical lines (e.g. `Loss for the financial year, … the financial year (1,264,399) …`), the current parser maps the numbers to the last line's fragment. PBT is captured cleanly; PAT falls back to PBT in the summary. Fix: stitch consecutive label-only lines until a numeric line is reached.
- **OCR speed.** Tesseract at 300 DPI averages ~3–4s/page; a full 22-page UFS takes ~80s. CLI exposes `--ocr-dpi` and the pipeline accepts `page_range` for spot-OCR.
- **Spurious notes-section matches.** The same statement title can reappear in Notes (e.g. "Statement of Comprehensive Income — extracts"). The parser logs `Could not detect columns for …` review flags but doesn't pollute the spread.
- **Excel placeholder.** `company_fs_sample.xlsx` is a dummy template with non-credible numbers — captured but flagged.
- **SoCE not spread.** Statement of Changes in Equity is detected as a boundary (so SoCI doesn't bleed into it) but not yet parsed for movements.

## Integration points

- `core/cases/case_store.py` — pipeline result is persisted under `parsed/sg_ingestion.json`, the merged profile under `parsed/acra_profile.json`, and the canonical periods under `features/fs_periods_canonical.json`.
- `core/pipeline/analysis_pipeline.py` — to be updated to consume `fs_periods_canonical` instead of the legacy `fs_normalized` shape.
- `agents/company_orchestrator.py` — the `FS` agent can use the new periods directly; new ACRA-profile agent can be wired off the merged `profile` payload.
