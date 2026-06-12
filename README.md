# Company Credit Analyst (CrediSage)

Corporate credit analysis platform for Singapore SMEs and large corporates.
Ingests ACRA filings + financial statements + bank/GST/bureau data, runs a
multi-agent LLM assessment, emits a committee-ready credit memo. React analyst
UI with an AI co-worker chat.

For architecture, data flow, conventions, and the dual-lineage map see
**[`CLAUDE.md`](CLAUDE.md)**. Long-form design lives at
[`docs/PLATFORM_BLUEPRINT.md`](docs/PLATFORM_BLUEPRINT.md).

## Quickstart

```powershell
# Backend (Python 3.14 venv at .venv/)
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn api.main:app --reload --port 8080
# → http://localhost:8080      Docs: http://localhost:8080/docs

# Frontend
cd frontend
npm install
npm run dev
# → http://localhost:5173 (proxies /api → :8080)
```

## Tests

```powershell
.venv\Scripts\python.exe -m pytest tests/ -v
cd frontend ; npm run build      # typecheck + production bundle
```

## Configuration

Edit `config/config.yaml` or set `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` in the
environment. Without keys, the analysis pipeline runs in deterministic mock
mode for demo/testing.

## OCR (optional, for image-only UFS PDFs)

Install the two system binaries on PATH:

- **Tesseract** — [UB Mannheim Windows installer](https://github.com/UB-Mannheim/tesseract/wiki)
- **Poppler** (`pdftoppm`) — [poppler-windows release](https://github.com/oschwartz10612/poppler-windows/releases)

Without them, every text-based ACRA filing (z124 XBRL renders, Excel) still ingests fine.

## Project layout (one-liner)

See [`CLAUDE.md`](CLAUDE.md) for the full annotated tree. Top-level: `api/`
(FastAPI), `core/` (domain logic), `agents/` (orchestrators), `frontend/`
(React+Vite), `prompts/` `schemas/` `config/` (resources), `scripts/` (CLI),
`tests/`, `docs/`, `input/` (sample data), `legacy/` (archived dead scripts).
