"""
FastAPI application for Company Credit Analyst.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from api.models import CaseStatusResponse, ChatRequest, ChatResponse, CreateCaseRequest
from api.coworker import CoworkerService
from api.ingestion_sg import router as ingestion_sg_router
from api.financials import router as financials_router
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
        cin=req.cin,
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
    file: UploadFile = File(...),
):
    try:
        store.get_manifest(case_id)
    except FileNotFoundError:
        raise HTTPException(404, "Case not found")

    content = await file.read()
    path = store.save_upload(case_id, source_type, file.filename or "upload", content)
    return {"saved": str(path), "source_type": source_type, "filename": file.filename}


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


@app.get("/cases/{case_id}/chat/history")
def chat_history(case_id: str):
    return {"history": store.load_chat_history(case_id)}


def run_server():
    import os
    import uvicorn
    host = os.getenv("API_HOST", config.get("api.host", "127.0.0.1"))
    port = int(os.getenv("API_PORT", config.get("api.port", 8080)))
    uvicorn.run("api.main:app", host=host, port=port, reload=True)


if __name__ == "__main__":
    run_server()
