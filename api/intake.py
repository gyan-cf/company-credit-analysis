"""
Document-intake endpoints — list / preview / delete / tag uploaded source files.

Phase 1 S3 (Document Intake Dashboard) drives off these routes. The existing
POST /cases/{id}/upload already saves the file; this router adds the
management surface the analyst needs (list, preview the PDF inline, remove,
tag the FY).

Metadata (FY tag + extraction status) is kept in `raw/<source_type>/_meta.json`
next to the files, so it survives across processes without a database.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from core.cases.case_store import CaseStore
from core.knowledge import build_case_wiki


logger = logging.getLogger(__name__)
router = APIRouter(tags=["intake"])
_store = CaseStore()

# Serial executor — agentic extraction is expensive and bound by the LLM
# provider's rate limits; running more than one case at a time would also
# blow past the OpenAI per-minute quota.
_extraction_executor = ThreadPoolExecutor(max_workers=1)

# Phase 1 limit per the user's demo flow: max 5 FS uploads (one per FY).
MAX_FINANCIALS_FILES = 5

_FY_FROM_NAME_RE = re.compile(r"(?:FY|fy)(\d{4})|(?:20\d{2})")


def _case_root(case_id: str) -> Path:
    try:
        _store.get_manifest(case_id)
    except FileNotFoundError:
        raise HTTPException(404, "Case not found")
    return _store._case_path(case_id)


def _raw_dir(case_id: str, source_type: str) -> Path:
    if source_type not in ("financials", "bank", "gst", "bureau"):
        raise HTTPException(400, f"Unknown source_type: {source_type!r}")
    raw = _case_root(case_id) / "raw" / source_type
    raw.mkdir(parents=True, exist_ok=True)
    return raw


def _safe_filename(raw: Path, filename: str) -> Path:
    """Reject filenames that try to escape the source dir."""
    target = (raw / filename).resolve()
    try:
        target.relative_to(raw.resolve())
    except ValueError:
        raise HTTPException(400, "Invalid filename")
    return target


def _meta_path(raw: Path) -> Path:
    return raw / "_meta.json"


def _load_meta(raw: Path) -> Dict[str, Dict[str, Any]]:
    mp = _meta_path(raw)
    if not mp.exists():
        return {}
    try:
        return json.loads(mp.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_meta(raw: Path, meta: Dict[str, Dict[str, Any]]) -> None:
    _meta_path(raw).write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def _infer_fy(filename: str) -> Optional[str]:
    """Pull an FY tag out of a filename if we can spot a 4-digit year."""
    m = _FY_FROM_NAME_RE.search(filename)
    if not m:
        return None
    year = m.group(1) or m.group(0)
    if year and len(year) >= 4:
        return f"FY{year[-4:]}"
    return None


class UploadEntry(BaseModel):
    filename: str
    size_bytes: int
    uploaded_at: str
    fy: Optional[str] = None
    extraction_status: str = "pending"
    note: Optional[str] = None


class UpdateUploadRequest(BaseModel):
    fy: Optional[str] = None
    extraction_status: Optional[str] = None
    note: Optional[str] = None


@router.get("/cases/{case_id}/uploads/{source_type}")
def list_uploads(case_id: str, source_type: str):
    """List the files uploaded under raw/<source_type>/ for this case."""
    raw = _raw_dir(case_id, source_type)
    meta = _load_meta(raw)
    files: List[Dict[str, Any]] = []
    for p in sorted(raw.iterdir()):
        if not p.is_file() or p.name.startswith("_") or p.suffix.lower() not in (".pdf", ".xlsx", ".xls"):
            continue
        m = meta.get(p.name, {})
        files.append({
            "filename":          p.name,
            "size_bytes":        p.stat().st_size,
            "uploaded_at":       m.get("uploaded_at")
                                  or datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
            "fy":                m.get("fy") or _infer_fy(p.name),
            "extraction_status": m.get("extraction_status", "pending"),
            "note":              m.get("note"),
        })
    return JSONResponse({
        "case_id":  case_id,
        "source":   source_type,
        "count":    len(files),
        "max":      MAX_FINANCIALS_FILES if source_type == "financials" else None,
        "files":    files,
    })


@router.get("/cases/{case_id}/uploads/{source_type}/{filename}")
def stream_upload(case_id: str, source_type: str, filename: str):
    """Stream the raw file for in-browser preview (PDF via iframe)."""
    raw = _raw_dir(case_id, source_type)
    target = _safe_filename(raw, filename)
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "File not found")
    mime, _ = mimetypes.guess_type(target.name)
    return FileResponse(
        path=str(target),
        media_type=mime or "application/octet-stream",
        filename=target.name,
        headers={"Content-Disposition": f'inline; filename="{target.name}"'},
    )


@router.delete("/cases/{case_id}/uploads/{source_type}/{filename}")
def delete_upload(case_id: str, source_type: str, filename: str):
    """Remove a single upload + its metadata entry."""
    raw = _raw_dir(case_id, source_type)
    target = _safe_filename(raw, filename)
    if target.exists():
        target.unlink()
    meta = _load_meta(raw)
    if filename in meta:
        del meta[filename]
        _save_meta(raw, meta)
    return JSONResponse({"deleted": filename})


def _set_status(raw_dir: Path, filename: str, status: str, note: Optional[str] = None) -> None:
    """Atomically (process-local) update extraction_status for one file."""
    meta = _load_meta(raw_dir)
    entry = meta.get(filename, {})
    entry.setdefault(
        "uploaded_at",
        datetime.fromtimestamp((raw_dir / filename).stat().st_mtime).isoformat()
        if (raw_dir / filename).exists()
        else datetime.now().isoformat(),
    )
    entry["extraction_status"] = status
    if note is not None:
        entry["note"] = note
    meta[filename] = entry
    _save_meta(raw_dir, meta)


def _run_extraction(case_id: str, case_root: Path, raw_dir: Path, fs_files: List[str]) -> None:
    """Background worker — runs the SG pipeline and writes per-file status."""
    from core.ingestion.sg_pipeline import SGIngestionPipeline

    started_at = datetime.now().isoformat()
    logger.info("extraction started for case=%s files=%s", case_id, fs_files)

    # Move every queued file to 'extracting' so the UI sees the change
    # before the first agentic call returns (which takes ~75-90s).
    for fn in fs_files:
        _set_status(raw_dir, fn, "extracting", note=None)

    def on_file(filename: str, status: str, note: Optional[str]) -> None:
        _set_status(raw_dir, filename, status, note)
        try:
            _store.update_status(
                case_id,
                "extracting" if status != "failed" else "extraction_partial",
                40,
            )
        except Exception:
            pass

    try:
        parsed_root = case_root / "parsed" / "financials"
        pipeline = SGIngestionPipeline(
            expand_zips=False, ocr_enabled=False, use_agentic=True,
        )
        result = pipeline.ingest_path(
            raw_dir,
            parsed_root=parsed_root,
            case_root=case_root,
            on_file_processed=on_file,
        )
        # Persist results in the layout the analysis pipeline reads.
        _store.save_parsed(case_id, "sg_ingestion", result.to_dict())
        _store.save_parsed(case_id, "acra_profile", result.profile)
        _store.save_features(
            case_id, "fs_periods_canonical",
            {"periods": result.periods, "summary": result.summary},
        )
        build_case_wiki(case_root)
        # Files that the pipeline never reached (e.g. excluded by classifier
        # or a hard upstream error) get marked as failed so the UI never
        # leaves a file stuck in "extracting".
        for fn in fs_files:
            meta = _load_meta(raw_dir)
            cur = meta.get(fn, {}).get("extraction_status")
            if cur in ("extracting", "queued"):
                _set_status(raw_dir, fn, "failed", "not processed by pipeline")
        _store.update_status(case_id, "extracted", 60)
    except Exception as exc:
        logger.exception("extraction failed for case=%s", case_id)
        for fn in fs_files:
            meta = _load_meta(raw_dir)
            cur = meta.get(fn, {}).get("extraction_status")
            if cur in ("extracting", "queued"):
                _set_status(raw_dir, fn, "failed", str(exc))
        try:
            _store.update_status(case_id, "extraction_failed", 0, error=str(exc))
        except Exception:
            pass
    finally:
        logger.info(
            "extraction finished for case=%s, elapsed=%s",
            case_id,
            (datetime.now() - datetime.fromisoformat(started_at)),
        )


@router.post("/cases/{case_id}/extract")
def trigger_extraction(case_id: str):
    """
    Kick off agentic extraction over every uploaded FS file for this case.

    Returns 202 immediately. Per-file extraction_status is updated in the
    intake metadata as each PDF completes (poll /uploads/financials to
    track progress).
    """
    case_root = _case_root(case_id)
    raw = _raw_dir(case_id, "financials")
    fs_files = [
        p.name for p in sorted(raw.iterdir())
        if p.is_file() and not p.name.startswith("_")
        and p.suffix.lower() in (".pdf", ".xlsx", ".xls")
    ]
    if not fs_files:
        raise HTTPException(400, "No financial statements uploaded for this case yet.")

    # Mark every file as queued before scheduling so the very next poll
    # sees the state change — the background worker flips to "extracting"
    # the moment it starts.
    for fn in fs_files:
        _set_status(raw, fn, "queued")
    try:
        _store.update_status(case_id, "extracting", 15)
    except Exception:
        pass

    _extraction_executor.submit(_run_extraction, case_id, case_root, raw, fs_files)

    return JSONResponse(
        {
            "case_id": case_id,
            "files_queued": fs_files,
            "estimate_seconds": 90 * len(fs_files),
            "status": "queued",
            "poll_url": f"/api/cases/{case_id}/uploads/financials",
        },
        status_code=202,
    )


@router.patch("/cases/{case_id}/uploads/{source_type}/{filename}")
def update_upload(case_id: str, source_type: str, filename: str, req: UpdateUploadRequest):
    """Update the FY tag / extraction_status / note for a single upload."""
    raw = _raw_dir(case_id, source_type)
    target = _safe_filename(raw, filename)
    if not target.exists():
        raise HTTPException(404, "File not found")
    meta = _load_meta(raw)
    entry = meta.get(filename, {})
    entry.setdefault("uploaded_at", datetime.fromtimestamp(target.stat().st_mtime).isoformat())
    if req.fy is not None:
        entry["fy"] = req.fy or None
    if req.extraction_status is not None:
        entry["extraction_status"] = req.extraction_status
    if req.note is not None:
        entry["note"] = req.note
    meta[filename] = entry
    _save_meta(raw, meta)
    return JSONResponse({"filename": filename, **entry})
