"""
FastAPI application for Company Credit Analyst.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from api.models import CaseStatusResponse, ChatRequest, ChatResponse, CreateCaseRequest
from api.coworker import CoworkerService
from api.ingestion_sg import router as ingestion_sg_router
from api.financials import router as financials_router
from api.intake import router as intake_router, MAX_FINANCIALS_FILES
from api.review import router as review_router
from api.knowledge import router as knowledge_router
from api.report import router as report_router
from core.cases.case_store import CaseStore
from core.pipeline.analysis_pipeline import AnalysisPipeline
from config.config import get_config

app = FastAPI(
    title="Company Credit Analyst API",
    description="Financial statement and credit analysis for corporate borrowers",
    version="1.0.0",
)

config = get_config()
store = CaseStore()
pipeline = AnalysisPipeline(store)
coworker = CoworkerService(store)
executor = ThreadPoolExecutor(max_workers=2)


app.add_middleware(
    CORSMiddleware,
    allow_origins=config.get("api.cors_origins", ["http://localhost:5173"]),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# SG ACRA / FS ingestion endpoints (POST /ingest/run, /ingest/classify, /cases/{id}/ingest/sg)
app.include_router(ingestion_sg_router)
# Per-source labelled blocks (rollup index, source manifest, PDF stream, block files)
app.include_router(financials_router)
# Document intake — list / preview / delete uploaded files, FY tagging
app.include_router(intake_router)
# Review + approval — edits to document.json with audit trail
app.include_router(review_router)
# Case wiki + knowledge search
app.include_router(knowledge_router)
# Section-by-section credit report (LLM generator + DOCX export)
app.include_router(report_router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/cases")
def list_cases():
    return {"cases": store.list_cases()}


@app.post("/cases")
def create_case(req: CreateCaseRequest):
    manifest = store.create_case(
        company_name=req.company_name,
        industry_code=req.industry_code,
        industry_hint=req.industry_hint,
        country=req.country,
        jurisdiction=req.jurisdiction,
        uen=req.uen,
        entity_type=req.entity_type,
        company_status=req.company_status,
        incorporation_date=req.incorporation_date,
        fiscal_year_end=req.fiscal_year_end,
        primary_ssic_code=req.primary_ssic_code,
        primary_ssic_desc=req.primary_ssic_desc,
        registered_address=req.registered_address,
        currency=req.currency,
        facility_type=req.facility_type,
        requested_limit=req.requested_limit,
        relationship_manager=req.relationship_manager,
        priority=req.priority,
        onboarding_stage=req.onboarding_stage,
        cin=req.cin or req.uen,
        pan=req.pan,
        fy_range=req.fy_range,
    )
    return manifest


@app.get("/cases/{case_id}")
def get_case(case_id: str):
    try:
        return store.get_manifest(case_id)
    except FileNotFoundError:
        raise HTTPException(404, "Case not found")


@app.get("/cases/{case_id}/status", response_model=CaseStatusResponse)
def get_status(case_id: str):
    try:
        m = store.get_manifest(case_id)
        return CaseStatusResponse(
            case_id=case_id,
            status=m.get("status", "unknown"),
            progress=m.get("progress", 0),
            company_name=m.get("company_name", ""),
            errors=m.get("errors", []),
        )
    except FileNotFoundError:
        raise HTTPException(404, "Case not found")


@app.post("/cases/{case_id}/upload")
async def upload_file(
    case_id: str,
    source_type: str = Form(...),
    fy: Optional[str] = Form(None),
    file: UploadFile = File(...),
):
    try:
        store.get_manifest(case_id)
    except FileNotFoundError:
        raise HTTPException(404, "Case not found")

    # Enforce the Phase-1 demo limit of 5 FS uploads (one per FY).
    if source_type == "financials":
        raw_dir = store._case_path(case_id) / "raw" / "financials"
        if raw_dir.exists():
            existing = [
                p for p in raw_dir.iterdir()
                if p.is_file() and not p.name.startswith("_")
                and p.suffix.lower() in (".pdf", ".xlsx", ".xls")
            ]
            # Replacing an existing file with the same name doesn't count
            # against the limit, but a new filename does.
            new_name = file.filename or "upload"
            existing_names = {p.name for p in existing}
            if new_name not in existing_names and len(existing) >= MAX_FINANCIALS_FILES:
                raise HTTPException(
                    400,
                    f"Maximum of {MAX_FINANCIALS_FILES} financial statements per case. "
                    "Remove an existing upload first.",
                )

    content = await file.read()
    path = store.save_upload(case_id, source_type, file.filename or "upload", content)

    # Record FY tag + uploaded_at in the per-source metadata file so the
    # intake endpoints surface it.
    if fy is not None or source_type == "financials":
        from datetime import datetime
        from api.intake import _load_meta, _save_meta, _infer_fy
        raw_dir = path.parent
        meta = _load_meta(raw_dir)
        entry = meta.get(path.name, {})
        entry.setdefault("uploaded_at", datetime.now().isoformat())
        if fy:
            entry["fy"] = fy
        elif "fy" not in entry:
            inferred = _infer_fy(path.name)
            if inferred:
                entry["fy"] = inferred
        meta[path.name] = entry
        _save_meta(raw_dir, meta)

    return {"saved": str(path), "source_type": source_type, "filename": file.filename, "fy": fy}


@app.get("/cases/{case_id}/parsed/fs")
def get_fs_parsed(case_id: str):
    data = store.load_parsed(case_id, "fs_normalized")
    if not data:
        raise HTTPException(404, "FS not parsed yet")
    return data


@app.put("/cases/{case_id}/parsed/fs")
def update_fs_parsed(case_id: str, body: dict):
    """Analyst review — update FS parsed data before analysis."""
    store.save_parsed(case_id, "fs_normalized", body)
    return {"status": "updated"}


@app.post("/cases/{case_id}/analyze")
async def analyze_case(case_id: str, provider: Optional[str] = None):
    try:
        store.get_manifest(case_id)
    except FileNotFoundError:
        raise HTTPException(404, "Case not found")

    # Approval gate: every extracted source must be analyst-approved before
    # the analysis pipeline runs. Frontend should already grey out the
    # button — this is the server-side enforcement.
    from api.review import _load_review_summary
    summary = _load_review_summary(case_id)
    if not summary["ready_to_analyze"]:
        raise HTTPException(
            400,
            summary.get("blocked_reason")
            or "All extracted financial-statement sources must be approved before running analysis.",
        )

    loop = asyncio.get_event_loop()
    store.update_status(case_id, "queued", 5)

    def _run():
        return pipeline.run(case_id, provider=provider)

    try:
        result = await loop.run_in_executor(executor, _run)
        return {
            "case_id": case_id,
            "status": "completed",
            "cards": len(result.get("assessment_summary", {}).get("cards", [])),
            "sources_analysed": summary["total"],
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/cases/{case_id}/assessment")
def get_assessment(case_id: str):
    summary = store.load_assessment_summary(case_id)
    if not summary:
        raise HTTPException(404, "Assessment not found — run /analyze first")
    return summary


@app.get("/cases/{case_id}/memo")
def get_memo(case_id: str):
    memo = store.load_credit_memo(case_id)
    if not memo:
        raise HTTPException(404, "Credit memo not found")
    return {"memo": memo}


@app.post("/cases/{case_id}/chat", response_model=ChatResponse)
def chat(case_id: str, req: ChatRequest):
    try:
        store.get_manifest(case_id)
    except FileNotFoundError:
        raise HTTPException(404, "Case not found")

    result = coworker.chat(case_id, req.message, skill=req.skill)
    return ChatResponse(**result)


@app.post("/cases/{case_id}/chat/stream")
def chat_stream(case_id: str, req: ChatRequest):
    """
    Server-Sent Events stream for one co-worker turn.

    Each frame is `data: <json>\\n\\n` carrying one of:
        {type: "delta",       text}
        {type: "tool_use",    id, name, input}
        {type: "tool_result", id, name, output, is_error}
        {type: "done",        text, tool_calls, citations, usage}
        {type: "error",       message}
    """
    try:
        store.get_manifest(case_id)
    except FileNotFoundError:
        raise HTTPException(404, "Case not found")

    return StreamingResponse(
        coworker.chat_stream(case_id, req.message),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/cases/{case_id}/chat/history")
def chat_history(case_id: str):
    return {"history": store.load_chat_history(case_id)}


# ---- Analyst notes (per-case persistent memory injected into co-worker) ----

@app.get("/cases/{case_id}/notes")
def get_analyst_notes(case_id: str):
    """Read the analyst-notes markdown file for a case."""
    try:
        store.get_manifest(case_id)
    except FileNotFoundError:
        raise HTTPException(404, "Case not found")
    content = store.load_analyst_notes(case_id)
    notes_path = store._case_path(case_id) / "analyst_notes.md"
    last_updated = (
        datetime.fromtimestamp(notes_path.stat().st_mtime).isoformat()
        if notes_path.exists() else None
    )
    return {
        "case_id": case_id,
        "content": content,
        "length": len(content),
        "last_updated": last_updated,
    }


@app.put("/cases/{case_id}/notes")
def put_analyst_notes(case_id: str, body: dict):
    """
    Replace the analyst-notes file. Body shape: {"content": "<markdown>"}.
    Empty content clears but keeps the file. The co-worker reads notes on
    every turn, so saves take effect on the next message.
    """
    try:
        store.get_manifest(case_id)
    except FileNotFoundError:
        raise HTTPException(404, "Case not found")
    content = body.get("content", "")
    if not isinstance(content, str):
        raise HTTPException(400, "content must be a string")
    return store.save_analyst_notes(case_id, content)


@app.get("/cases/{case_id}/coworker/suggestions")
def coworker_suggestions(case_id: str):
    """
    Return 3-5 context-aware prompt suggestions for the co-worker rail.
    Tuned to current case state: assessment status, severity of findings,
    presence of probes, report status, ratio breaches.
    """
    try:
        store.get_manifest(case_id)
    except FileNotFoundError:
        raise HTTPException(404, "Case not found")
    from core.coworker.suggestions import build_suggestions
    return {"suggestions": build_suggestions(case_id, store=store)}


def run_server():
    import os
    import uvicorn
    host = os.getenv("API_HOST", config.get("api.host", "127.0.0.1"))
    port = int(os.getenv("API_PORT", config.get("api.port", 8080)))
    uvicorn.run("api.main:app", host=host, port=port, reload=True)


if __name__ == "__main__":
    run_server()
