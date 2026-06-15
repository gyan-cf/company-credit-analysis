"""
Credit-report generation endpoints.

    POST  /cases/{id}/report/generate       — kick off (background, 202)
    GET   /cases/{id}/report/status         — poll generation progress
    GET   /cases/{id}/report                — fetch the latest JSON
    GET   /cases/{id}/report.docx           — download the latest .docx

A single shared ThreadPoolExecutor enforces one report job at a time per
process (matches the OpenAI per-minute budget for sectional fan-out).
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from core.cases.case_store import CaseStore


logger = logging.getLogger(__name__)
router = APIRouter(tags=["report"])
_store = CaseStore()
_report_executor = ThreadPoolExecutor(max_workers=1)

# In-flight job state, per case. Simple in-memory; survives only for the
# lifetime of the process — long enough for a typical demo run.
_jobs: Dict[str, Dict[str, Any]] = {}


def _case_root(case_id: str) -> Path:
    try:
        _store.get_manifest(case_id)
    except FileNotFoundError:
        raise HTTPException(404, "Case not found")
    return _store._case_path(case_id)


def _run_generation(case_id: str, case_root: Path, template: str) -> None:
    from core.report.generator import generate_report, persist_report
    _jobs[case_id] = {
        "status":     "running",
        "started_at": datetime.utcnow().isoformat() + "Z",
        "template":   template,
    }
    logger.info("report generation started case=%s template=%s", case_id, template)
    try:
        report = generate_report(case_id, case_root, template=template)
        paths = persist_report(case_root, report)
        _jobs[case_id] = {
            "status":        "completed",
            "started_at":    _jobs[case_id].get("started_at"),
            "completed_at":  datetime.utcnow().isoformat() + "Z",
            "template":      template,
            "section_count": report.get("section_count"),
            "duration_s":    report.get("duration_s"),
            "docx_path":     str(paths["docx_path"].relative_to(case_root)),
        }
        logger.info(
            "report generation finished case=%s sections=%s duration=%ss",
            case_id, report.get("section_count"), report.get("duration_s"),
        )
    except Exception as e:
        logger.exception("report generation failed for case=%s", case_id)
        _jobs[case_id] = {
            "status":       "failed",
            "started_at":   _jobs[case_id].get("started_at"),
            "completed_at": datetime.utcnow().isoformat() + "Z",
            "error":        f"{type(e).__name__}: {e}",
            "template":     template,
        }


@router.post("/cases/{case_id}/report/generate")
def trigger_generation(case_id: str, template: str = "credit_analysis"):
    """Queue a fresh report generation. Returns 202 immediately."""
    case_root = _case_root(case_id)
    if not (case_root / "features" / "fs_analytics.json").exists():
        raise HTTPException(
            400,
            "Financial analytics not found for this case. Run /analyze first "
            "(approve every source on the Review page → click Run analysis).",
        )
    if _jobs.get(case_id, {}).get("status") == "running":
        raise HTTPException(409, "A report generation is already in progress for this case.")

    _report_executor.submit(_run_generation, case_id, case_root, template)
    return JSONResponse(
        {
            "case_id":      case_id,
            "template":     template,
            "status":       "queued",
            "poll_url":     f"/api/cases/{case_id}/report/status",
            "download_url": f"/api/cases/{case_id}/report.docx",
        },
        status_code=202,
    )


@router.get("/cases/{case_id}/report/status")
def report_status(case_id: str):
    """In-flight + persisted status for the latest report job on this case."""
    case_root = _case_root(case_id)
    state = dict(_jobs.get(case_id) or {"status": "not_run"})
    latest = case_root / "reports" / "latest.json"
    state["report_on_disk"] = latest.exists()
    if state.get("status") == "not_run" and latest.exists():
        # We have a persisted report from a previous process run.
        state["status"] = "completed"
    return JSONResponse(state)


@router.get("/cases/{case_id}/report")
def get_report(case_id: str):
    """Return the latest report JSON. Backfills `html` per section on the fly
    for older reports generated before the HTML-intermediate migration."""
    case_root = _case_root(case_id)
    latest = case_root / "reports" / "latest.json"
    if not latest.exists():
        raise HTTPException(404, "No report generated yet")
    report = json.loads(latest.read_text(encoding="utf-8"))
    needs_resave = False
    sections = report.get("sections", []) or []
    from core.report.generator import normalize_section_numbers
    sections, renumbered = normalize_section_numbers(sections)
    if renumbered:
        report["sections"] = sections
        report["section_count"] = len(sections)
        needs_resave = True
    if sections and not sections[0].get("html"):
        from core.report.html_renderer import markdown_to_html
        for s in sections:
            if not s.get("html"):
                s["html"] = markdown_to_html(s.get("markdown", ""))
        needs_resave = True
    if needs_resave:
        latest.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if renumbered:
        latest_docx = case_root / "reports" / "latest.docx"
        if latest_docx.exists():
            from core.report.docx_writer import write_report_docx
            write_report_docx(report, latest_docx)
    return JSONResponse(report)


@router.get("/cases/{case_id}/report.docx")
def get_report_docx(case_id: str):
    """Download the latest report as a .docx."""
    case_root = _case_root(case_id)
    latest = case_root / "reports" / "latest.docx"
    if not latest.exists():
        raise HTTPException(404, "No report generated yet")
    return FileResponse(
        path=str(latest),
        media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        filename=f"credit_analysis_report_{case_id}.docx",
    )


# ---- Per-section regenerate ---------------------------------------------------

class RegenerateRequest(BaseModel):
    instruction: Optional[str] = None    # 'tighten' | 'expand' | free-form text


@router.post("/cases/{case_id}/report/sections/{section_code}/regenerate")
def regenerate_section(case_id: str, section_code: str, req: RegenerateRequest = Body(default=RegenerateRequest())):
    """
    Re-run the LLM for one section. Thin HTTP wrapper around
    core.report.generator.regenerate_one_section, which is shared with the
    co-worker's regenerate_report_section pending-action executor.
    """
    from core.report.generator import regenerate_one_section

    case_root = _case_root(case_id)
    try:
        section = regenerate_one_section(case_root, section_code, req.instruction)
    except ValueError as e:
        msg = str(e)
        if msg.startswith("Unknown section code"):
            raise HTTPException(404, msg)
        raise HTTPException(400, msg)
    except RuntimeError as e:
        logger.exception("Regenerate failed for section %s", section_code)
        raise HTTPException(500, str(e))
    return JSONResponse({"ok": True, "section": section})
