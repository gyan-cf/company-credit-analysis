"""Integration test for API and pipeline."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi.testclient import TestClient

from api.main import app
from core.cases.case_store import CaseStore
from core.pipeline.analysis_pipeline import AnalysisPipeline


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CASES_BASE_DIR", str(tmp_path / "cases"))
    store = CaseStore(base_dir=str(tmp_path / "cases"))
    return TestClient(app), store


def test_health():
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_create_and_analyze_case(tmp_path, monkeypatch):
    import shutil
    cases_dir = tmp_path / "cases"
    repo_root = Path(__file__).parent.parent
    monkeypatch.chdir(repo_root)

    store = CaseStore(base_dir=str(cases_dir))
    manifest = store.create_case(
        company_name="GOIMPACT CAPITAL PARTNERS (SG) PL",
        industry_code="services",
        industry_hint="Financial Services",
    )
    case_id = manifest["case_id"]

    # Seed the case with two demo FS PDFs — the pipeline now refuses to run
    # without uploads (the old silent demo fallback was removed).
    raw_fs = cases_dir / case_id / "raw" / "financials"
    raw_fs.mkdir(parents=True, exist_ok=True)
    for src in [
        repo_root / "input" / "financials" / "FY2022" / "c230774815_z124_1.pdf",
        repo_root / "input" / "financials" / "FY2023" / "c240763134_z124_1.pdf",
    ]:
        shutil.copy(src, raw_fs)

    pipeline = AnalysisPipeline(store)
    result = pipeline.run(case_id)

    assert result["status"] == "completed"
    summary = store.load_assessment_summary(case_id)
    assert len(summary.get("cards", [])) >= 1
    memo = store.load_credit_memo(case_id)
    assert "Credit Memorandum" in memo or "Executive Summary" in memo
