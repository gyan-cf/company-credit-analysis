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
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


def _utc_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _write_json_file(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _financial_source_dirs(parsed_root: Path) -> List[Path]:
    if not parsed_root.exists():
        return []
    return [
        p for p in sorted(parsed_root.iterdir())
        if p.is_dir()
        and p.name != "merged"
        and not p.name.startswith("_")
        and (p / "manifest.json").exists()
    ]


def _source_entry_from_manifest(
    source_id: str,
    manifest: Dict[str, Any],
    *,
    document_errors: Optional[List[str]] = None,
) -> Dict[str, Any]:
    columns = manifest.get("columns", []) or []
    blocks = manifest.get("blocks", []) or []
    entry = {
        "source_id": source_id,
        "source_type": manifest.get("source_type", "financial_statement"),
        "original_filename": manifest.get("original_filename"),
        "original_path": manifest.get("original_path"),
        "manifest": f"{source_id}/manifest.json",
        "document_json": f"{source_id}/document.json",
        "wiki_document": f"{source_id}/wiki_document.json",
        "document_validation_errors": document_errors or [],
        "entity": (manifest.get("entity") or {}).get("name", ""),
        "uen": (manifest.get("entity") or {}).get("uen", ""),
        "framework": (manifest.get("entity") or {}).get("framework", "SFRS"),
        "audited": manifest.get("audited"),
        "consolidated": manifest.get("consolidated"),
        "extraction_method": manifest.get("extraction_method"),
        "fys": sorted({c.get("fy") for c in columns if c.get("fy")}, reverse=True),
        "perimeters": sorted({c.get("perimeter") for c in columns if c.get("perimeter")}),
        "block_count": len(blocks),
    }
    if manifest.get("review"):
        entry["review"] = manifest["review"]
    return entry


def _profile_from_dict(data: Dict[str, Any]):
    from core.ingestion.acra_profile_extract import CorporateProfile

    paid_up = data.get("paid_up_capital") or {}
    agm = data.get("agm") or {}
    return CorporateProfile(
        uen=data.get("uen", "") or "",
        entity_name=data.get("entity_name", "") or "",
        entity_type=data.get("entity_type", "") or "",
        entity_status=data.get("entity_status", "") or "",
        incorporation_date=data.get("incorporation_date", "") or "",
        fye=data.get("fye", "") or "",
        primary_ssic_code=data.get("primary_ssic_code", "") or "",
        primary_ssic_desc=data.get("primary_ssic_desc", "") or "",
        registered_address=data.get("registered_address", "") or "",
        company_type=data.get("company_type", "") or "",
        company_status=data.get("company_status", "") or "",
        small_company_exemption=data.get("small_company_exemption"),
        audited=data.get("audited"),
        paid_up_capital_amount=paid_up.get("amount"),
        paid_up_capital_currency=paid_up.get("currency", "") or "",
        paid_up_share_class=paid_up.get("share_class", "") or "",
        consolidated_level=data.get("consolidated_level", "") or "",
        agm_required=agm.get("required"),
        agm_date=agm.get("date", "") or "",
        accounting_standards=data.get("accounting_standards", "") or "",
        directors=list(data.get("directors") or []),
        shareholders=list(data.get("shareholders") or []),
        secretaries=list(data.get("secretaries") or []),
        auditors=list(data.get("auditors") or []),
        charges=list(data.get("charges") or []),
        source_files=list(data.get("source_files") or []),
        review_flags=list(data.get("review_flags") or []),
    )


def _classifications_from_dicts(items: List[Dict[str, Any]]):
    from core.ingestion.classifier import ClassifiedFile

    out = []
    for item in items or []:
        out.append(ClassifiedFile(
            path=Path(item.get("path") or item.get("filename") or ""),
            source_type=item.get("source_type") or "unknown",
            uen=item.get("uen"),
            fy=item.get("fy"),
            confidence=float(item.get("confidence") or 0),
            reasons=list(item.get("reasons") or []),
        ))
    return out


def _manifest_pdf_path(case_root: Path, manifest: Dict[str, Any]) -> Path:
    rel = manifest.get("original_path")
    if rel:
        candidate = Path(rel)
        pdf = candidate if candidate.is_absolute() else (case_root / candidate)
        if pdf.exists():
            return pdf
    filename = manifest.get("original_filename") or ""
    return case_root / "raw" / "financials" / filename


def _rebuild_case_financial_artifacts(
    case_id: str,
    case_root: Path,
    parsed_root: Path,
    *,
    started_at: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Rebuild the rollup index, merged blocks, sg_ingestion.json, and canonical
    periods from the current per-source document.json files.

    This is the merge-safe counterpart to a targeted re-extraction: only the
    selected source is re-parsed, then all source documents are folded back
    into the case-level artifacts used by Review, Financials, and reports.
    """
    from core.ingestion.block_writer import write_merged_blocks
    from core.ingestion.document_writer import document_to_fs_extraction, validate_document
    from core.ingestion.sg_pipeline import SGIngestionPipeline

    existing_ingestion = _store.load_parsed(case_id, "sg_ingestion") or {}
    existing_index = _load_json_file(parsed_root / "index.json", {})
    started = started_at or existing_index.get("started_at") or existing_ingestion.get("started_at") or _utc_now()

    sources: List[Dict[str, Any]] = []
    blocks_flat: List[Dict[str, Any]] = []
    source_exts: List[Tuple[str, Any]] = []
    validation_review_flags: List[Dict[str, Any]] = []

    for src in _financial_source_dirs(parsed_root):
        manifest = _load_json_file(src / "manifest.json", {})
        if not manifest:
            continue
        doc_path = src / "document.json"
        doc_errors: List[str] = []
        if doc_path.exists():
            doc = _load_json_file(doc_path, {})
            doc_errors = validate_document(doc) if doc else ["document.json could not be read"]
            if doc:
                ext = document_to_fs_extraction(doc, _manifest_pdf_path(case_root, manifest))
                ext.review_flags = list(manifest.get("review_flags") or [])
                source_exts.append((src.name, ext))
            if doc_errors:
                validation_review_flags.append({
                    "severity": "high",
                    "message": (
                        f"document.json failed sg_fs_document_schema validation "
                        f"({len(doc_errors)} error(s))"
                    ),
                    "source": manifest.get("original_filename") or src.name,
                    "errors": doc_errors[:10],
                })
        sources.append(_source_entry_from_manifest(src.name, manifest, document_errors=doc_errors))
        blocks_flat.extend({"source_id": src.name, **b} for b in (manifest.get("blocks") or []))

    merged_blocks: List[Dict[str, Any]] = []
    merged_dir = parsed_root / "merged"
    if merged_dir.exists():
        shutil.rmtree(merged_dir, ignore_errors=True)
    if source_exts:
        merged_blocks = write_merged_blocks(sources=source_exts, parsed_root=parsed_root)
        blocks_flat.extend({"source_id": "merged", **b} for b in merged_blocks)

    index = {
        "started_at": started,
        "finished_at": _utc_now(),
        "parsed_root": str(parsed_root),
        "source_count": len(sources),
        "block_count": len(blocks_flat),
        "merged_block_count": len(merged_blocks),
        "removed_stale": [],
        "document_validation_error_count": sum(
            len(s.get("document_validation_errors", []) or []) for s in sources
        ),
        "validation_review_flags": validation_review_flags,
        "sources": sources,
        "blocks": blocks_flat,
    }
    _write_json_file(parsed_root / "index.json", index)

    fs_extractions = [ext for _, ext in source_exts]
    periods = SGIngestionPipeline._merge_periods(fs_extractions)
    profile_data = _store.load_parsed(case_id, "acra_profile") or existing_ingestion.get("profile") or {}
    profile = _profile_from_dict(profile_data)
    classified = _classifications_from_dicts(existing_ingestion.get("classifications", []))
    summary = SGIngestionPipeline._build_summary(profile, fs_extractions, periods, classified)
    review_flags: List[Dict[str, Any]] = []
    for ext in fs_extractions:
        review_flags.extend(ext.review_flags)
    review_flags.extend(profile.review_flags)
    review_flags.extend(validation_review_flags)

    rebuilt_ingestion = dict(existing_ingestion)
    rebuilt_ingestion.update({
        "started_at": started,
        "finished_at": index["finished_at"],
        "root": str(case_root / "raw" / "financials"),
        "profile": profile.to_dict(),
        "fs_extractions": [ext.to_dict() for ext in fs_extractions],
        "periods": periods,
        "summary": summary,
        "review_flags": review_flags,
        "blocks_index": index,
    })
    _store.save_parsed(case_id, "sg_ingestion", rebuilt_ingestion)
    _store.save_parsed(case_id, "acra_profile", profile.to_dict())
    _store.save_features(case_id, "fs_periods_canonical", {"periods": periods, "summary": summary})
    build_case_wiki(case_root)
    return index


def _clear_source_review(case_id: str, source_id: str) -> None:
    case_root = _case_root(case_id)
    src = case_root / "parsed" / "financials" / source_id
    manifest_path = src / "manifest.json"
    manifest = _load_json_file(manifest_path, {})
    if manifest:
        manifest.pop("review", None)
        _write_json_file(manifest_path, manifest)
    index_path = case_root / "parsed" / "financials" / "index.json"
    index = _load_json_file(index_path, {})
    changed = False
    for source in index.get("sources", []) or []:
        if source.get("source_id") == source_id:
            source["review"] = {"status": "pending"}
            changed = True
            break
    if changed:
        _write_json_file(index_path, index)


def _run_selected_source_extraction(
    case_id: str,
    case_root: Path,
    raw_dir: Path,
    filename: str,
    previous_source_id: str,
) -> None:
    """Background worker for one-source re-extraction."""
    from core.ingestion.sg_pipeline import SGIngestionPipeline

    started_at = datetime.now().isoformat()
    logger.info(
        "selected extraction started for case=%s source=%s file=%s",
        case_id, previous_source_id, filename,
    )
    _set_status(raw_dir, filename, "extracting", note=None)

    temp_base = (
        case_root / "parsed" / "_selected_extraction" /
        f"{previous_source_id}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    )
    temp_parsed = temp_base / "financials"
    try:
        parsed_root = case_root / "parsed" / "financials"

        def on_file(processed_filename: str, status: str, note: Optional[str]) -> None:
            if processed_filename == filename:
                _set_status(raw_dir, filename, status, note)

        pipeline = SGIngestionPipeline(
            expand_zips=False, ocr_enabled=False, use_agentic=True,
        )
        result = pipeline.ingest_path(
            raw_dir,
            parsed_root=temp_parsed,
            case_root=case_root,
            on_file_processed=on_file,
            selected_filenames=[filename],
        )
        sources = result.blocks_index.get("sources", []) if result.blocks_index else []
        if not sources:
            detail = result.errors[0]["error"] if result.errors else "not processed by pipeline"
            raise RuntimeError(f"No source artifact was produced for {filename}: {detail}")

        new_source_id = sources[0]["source_id"]
        temp_src = temp_parsed / new_source_id
        if not temp_src.exists():
            raise RuntimeError(f"Temporary source artifact missing for {filename}")

        dst = parsed_root / new_source_id
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(temp_src, dst)

        # If the raw PDF was replaced under the same filename, its content hash
        # and source_id can change. Remove the superseded parsed source(s) for
        # that filename only after the new artifact is safely copied.
        for src in _financial_source_dirs(parsed_root):
            if src.name == new_source_id:
                continue
            manifest = _load_json_file(src / "manifest.json", {})
            if manifest.get("original_filename") == filename:
                shutil.rmtree(src, ignore_errors=True)

        _rebuild_case_financial_artifacts(
            case_id,
            case_root,
            parsed_root,
            started_at=result.started_at,
        )
        _set_status(raw_dir, filename, "ready", f"re-extracted as source {new_source_id}")
        try:
            _store.update_status(case_id, "extracted", 60)
        except Exception:
            pass
    except Exception as exc:
        logger.exception(
            "selected extraction failed for case=%s source=%s file=%s",
            case_id, previous_source_id, filename,
        )
        _set_status(raw_dir, filename, "failed", str(exc))
        try:
            _store.update_status(case_id, "extraction_partial", 40, error=str(exc))
        except Exception:
            pass
    finally:
        if temp_base.exists():
            shutil.rmtree(temp_base, ignore_errors=True)
        logger.info(
            "selected extraction finished for case=%s source=%s elapsed=%s",
            case_id,
            previous_source_id,
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


@router.post("/cases/{case_id}/sources/{source_id}/extract")
def trigger_selected_source_extraction(case_id: str, source_id: str):
    """
    Re-start extraction for one already-ingested financial statement source.

    The existing parsed source remains available until the background worker
    has successfully extracted the selected raw file into a temporary parsed
    area and swapped the refreshed artifact back into the case.
    """
    case_root = _case_root(case_id)
    parsed_root = case_root / "parsed" / "financials"
    src_dir = parsed_root / source_id
    if not src_dir.exists() or not src_dir.is_dir():
        raise HTTPException(404, "Source not found for this case")

    manifest = _load_json_file(src_dir / "manifest.json", {})
    filename = manifest.get("original_filename")
    if not filename:
        raise HTTPException(400, "Source manifest does not record the original filename")

    raw = _raw_dir(case_id, "financials")
    target = _safe_filename(raw, filename)
    if not target.exists() or not target.is_file():
        raise HTTPException(404, f"Original upload not found: {filename}")

    _clear_source_review(case_id, source_id)
    _set_status(raw, filename, "queued", "selected source queued for re-extraction")
    try:
        _store.update_status(case_id, "extracting", 15)
    except Exception:
        pass

    _extraction_executor.submit(
        _run_selected_source_extraction,
        case_id,
        case_root,
        raw,
        filename,
        source_id,
    )

    return JSONResponse(
        {
            "case_id": case_id,
            "source_id": source_id,
            "filename": filename,
            "files_queued": [filename],
            "estimate_seconds": 90,
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
