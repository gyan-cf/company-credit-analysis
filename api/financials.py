"""
FastAPI routes that serve the per-source labelled blocks produced by the
SG ingestion pipeline. The frontend Financials page drives entirely off
these endpoints:

    GET /cases/{case_id}/financials
        → rollup index (parsed/financials/index.json)

    GET /cases/{case_id}/sources/{source_id}/manifest
        → source manifest with the full block catalog

    GET /cases/{case_id}/sources/{source_id}/pdf
        → stream the original PDF inline for the embedded viewer

    GET /cases/{case_id}/sources/{source_id}/blocks/{path:path}
        → fetch any single block file (CSV / JSON sidecar / Markdown)

All filesystem reads resolve through `_resolve` which rejects path-traversal
attempts and any path escaping the case directory.
"""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from core.cases.case_store import CaseStore


router = APIRouter(tags=["financials"])
_store = CaseStore()


def _case_path(case_id: str) -> Path:
    try:
        _store.get_manifest(case_id)
    except FileNotFoundError:
        raise HTTPException(404, "Case not found")
    return _store._case_path(case_id)


def _source_dir(case_id: str, source_id: str) -> Path:
    src = _case_path(case_id) / "parsed" / "financials" / source_id
    if not src.exists() or not src.is_dir():
        raise HTTPException(404, "Source not found for this case")
    return src


def _resolve(base: Path, rel: str) -> Path:
    """Resolve `rel` against `base` and reject any escape."""
    target = (base / rel).resolve()
    try:
        target.relative_to(base.resolve())
    except ValueError:
        raise HTTPException(400, "Invalid path")
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "File not found")
    return target


@router.get("/cases/{case_id}/financials")
def get_financials_index(case_id: str):
    """Return the rollup index for the case's financial sources."""
    idx_path = _case_path(case_id) / "parsed" / "financials" / "index.json"
    if not idx_path.exists():
        raise HTTPException(404, "Financials not ingested yet — POST /cases/{id}/ingest/sg first")
    return JSONResponse(json.loads(idx_path.read_text(encoding="utf-8")))


@router.get("/cases/{case_id}/sources/{source_id}/manifest")
def get_source_manifest(case_id: str, source_id: str):
    manifest_path = _source_dir(case_id, source_id) / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(404, "Source manifest missing")
    return JSONResponse(json.loads(manifest_path.read_text(encoding="utf-8")))


@router.get("/cases/{case_id}/sources/{source_id}/pdf")
def get_source_pdf(case_id: str, source_id: str):
    """Stream the original PDF inline so the frontend can embed it via pdf.js."""
    case_root = _case_path(case_id)
    manifest_path = _source_dir(case_id, source_id) / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(404, "Source manifest missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rel = manifest.get("original_path")
    if not rel:
        raise HTTPException(404, "Source PDF path not recorded")

    candidate = Path(rel)
    pdf = candidate if candidate.is_absolute() else (case_root / candidate)
    pdf = pdf.resolve()
    if not pdf.exists() or not pdf.is_file():
        raise HTTPException(404, "PDF file no longer at recorded path")
    return FileResponse(
        path=str(pdf),
        media_type="application/pdf",
        filename=manifest.get("original_filename") or pdf.name,
        headers={"Content-Disposition": f'inline; filename="{manifest.get("original_filename") or pdf.name}"'},
    )


@router.get("/cases/{case_id}/sources/{source_id}/blocks/{path:path}")
def get_source_block(case_id: str, source_id: str, path: str):
    """
    Fetch any single block file from the source directory.

    `path` is the manifest-relative path: e.g. `tables/sofp__company.json`,
    `narrative/auditor_report.md`, `notes/note_03_revenue.md`, `raw.txt`.
    """
    src = _source_dir(case_id, source_id)
    target = _resolve(src, path)
    mime, _ = mimetypes.guess_type(target.name)
    if target.suffix == ".md":
        mime = "text/markdown; charset=utf-8"
    elif target.suffix == ".json":
        mime = "application/json"
    elif target.suffix == ".csv":
        mime = "text/csv; charset=utf-8"
    elif target.suffix == ".txt":
        mime = "text/plain; charset=utf-8"
    return FileResponse(path=str(target), media_type=mime or "application/octet-stream")
