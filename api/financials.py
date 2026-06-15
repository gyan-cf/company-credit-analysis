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

import io
from urllib.parse import quote as _urlquote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from core.cases.case_store import CaseStore
from core.features.fs_analytics import build_fs_agent_data, build_fs_agent_data_from_merged
from core.report.excel_export import analytics_xlsx_filename, build_analytics_workbook


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


@router.get("/cases/{case_id}/financials/analytics")
def get_financials_analytics(case_id: str, perimeter: Optional[str] = None):
    """Return FS analytics: raw line values, ratios, trends, and review flags."""
    requested_perimeter = (perimeter or "").strip().lower()
    case_root = _case_path(case_id)
    saved = _store.load_features(case_id, "fs_analytics") or {}
    ingestion = _store.load_parsed(case_id, "sg_ingestion") or {}
    periods = ingestion.get("periods", []) or []
    target_perimeter = requested_perimeter or saved.get("perimeter") or "company"
    manifest = _store.get_manifest(case_id)
    entity = dict(saved.get("entity") or {})
    entity.setdefault("name", manifest.get("company_name"))
    entity["consolidated"] = target_perimeter == "group"
    review_flags = ingestion.get("review_flags", []) or saved.get("review_flags", []) or []

    data = build_fs_agent_data_from_merged(
        case_root / "parsed" / "financials",
        perimeter=target_perimeter,
        entity=entity,
        review_flags=review_flags,
        fallback_periods=periods,
    )
    if not data and requested_perimeter and periods:
        data = build_fs_agent_data(
            periods,
            perimeter=requested_perimeter,
            entity=entity,
            review_flags=review_flags,
        )
    if not data:
        data = saved
    if not data:
        raise HTTPException(404, "Financial analytics not generated yet — run analysis first")
    return JSONResponse(data)


@router.get("/cases/{case_id}/analytics.xlsx")
def download_analytics_xlsx(case_id: str):
    """
    Stream the full financial-analysis workbook (.xlsx).

    Sheets: Summary, Balance Sheet, Income Statement, Cash Flow, Ratios,
    YoY Trends, Findings, Probes. Generated on every request from the latest
    on-disk artefacts — no caching, so the analyst gets a fresh export
    whenever they click download.
    """
    try:
        _store.get_manifest(case_id)
    except FileNotFoundError:
        raise HTTPException(404, "Case not found")

    try:
        wb = build_analytics_workbook(case_id, store=_store)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Failed to build workbook: {e}")

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = analytics_xlsx_filename(case_id, store=_store)
    # RFC 5987-safe Content-Disposition for non-ASCII company names.
    cd = f"attachment; filename=\"{filename}\"; filename*=UTF-8''{_urlquote(filename)}"
    return StreamingResponse(
        buf,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": cd},
    )


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
