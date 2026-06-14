"""
FastAPI routes for the Singapore ACRA / FS ingestion module.

Endpoints:
    POST /ingest/classify           — classify a single uploaded file (no extraction)
    POST /ingest/run                — run the full pipeline over an uploaded folder.zip
                                       OR an existing case's raw/financials directory
    POST /cases/{case_id}/ingest/sg — pipeline-run scoped to a case; persists output
    GET  /cases/{case_id}/ingest/sg — fetch the last persisted ingestion result
"""

from __future__ import annotations

import io
import json
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from core.ingestion import SGIngestionPipeline, classify_file
from core.cases.case_store import CaseStore
from core.knowledge import build_case_wiki

router = APIRouter(tags=["ingestion-sg"])
_store = CaseStore()


class IngestRunRequest(BaseModel):
    path: str
    expand_zips: bool = True
    ocr_enabled: bool = True
    ocr_dpi: int = 300


@router.post("/ingest/classify")
async def classify_uploaded(file: UploadFile = File(...)):
    """Classify a single uploaded file. Useful for the upload UI's instant routing."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{file.filename}") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        cf = classify_file(tmp_path)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
    d = cf.to_dict()
    d["filename"] = file.filename
    return d


@router.post("/ingest/run")
async def run_pipeline_on_path(req: IngestRunRequest):
    """Run the SG ingestion pipeline on a server-side folder path (e.g. input/financials)."""
    root = Path(req.path)
    if not root.exists():
        raise HTTPException(404, f"Path not found: {req.path}")
    pipeline = SGIngestionPipeline(
        expand_zips=req.expand_zips,
        ocr_enabled=req.ocr_enabled,
        ocr_dpi=req.ocr_dpi,
    )
    result = pipeline.ingest_path(root)
    return result.to_dict()


@router.post("/cases/{case_id}/ingest/sg")
async def run_case_ingestion(
    case_id: str,
    expand_zips: bool = Form(True),
    ocr_enabled: bool = Form(True),
):
    """
    Run the SG ingestion pipeline against `raw/financials/` for the case,
    persist the result, and return a summary.
    """
    try:
        _store.get_manifest(case_id)
    except FileNotFoundError:
        raise HTTPException(404, "Case not found")

    case_root = _store._case_path(case_id)
    raw_dir = case_root / "raw" / "financials"
    if not raw_dir.exists() or not any(p.suffix.lower() in (".pdf", ".xlsx", ".xls") for p in raw_dir.iterdir() if p.is_file()):
        raise HTTPException(
            400,
            f"No financial statements uploaded for case {case_id}. "
            f"Upload PDFs / xlsx to raw/financials/ first "
            f"(POST /cases/{case_id}/upload with source_type=financials).",
        )

    parsed_root = case_root / "parsed" / "financials"
    pipeline = SGIngestionPipeline(expand_zips=expand_zips, ocr_enabled=ocr_enabled)
    result = pipeline.ingest_path(
        raw_dir, parsed_root=parsed_root, case_root=case_root,
    ).to_dict()

    # Persist
    _store.save_parsed(case_id, "sg_ingestion", result)
    _store.save_parsed(case_id, "acra_profile", result.get("profile", {}))
    _store.save_features(case_id, "fs_periods_canonical",
                        {"periods": result.get("periods", []), "summary": result.get("summary", {})})
    wiki_summary = build_case_wiki(case_root)

    return {
        "case_id": case_id,
        "summary": result.get("summary", {}),
        "review_flags": result.get("review_flags", []),
        "errors": result.get("errors", []),
        "blocks_index": {
            "source_count": result.get("blocks_index", {}).get("source_count", 0),
            "block_count": result.get("blocks_index", {}).get("block_count", 0),
        },
        "knowledge": {
            "page_count": wiki_summary.get("page_count", 0),
            "chunk_count": wiki_summary.get("chunk_count", 0),
            "evidence_count": wiki_summary.get("evidence_count", 0),
        },
    }


@router.get("/cases/{case_id}/ingest/sg")
async def get_case_ingestion(case_id: str):
    data = _store.load_parsed(case_id, "sg_ingestion")
    if not data:
        raise HTTPException(404, "No ingestion result for this case — POST to run first")
    return data
