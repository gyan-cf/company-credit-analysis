import json
from pathlib import Path

import api.intake as intake
from core.cases.case_store import CaseStore


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


class _CapturingExecutor:
    def __init__(self):
        self.calls = []

    def submit(self, fn, *args):
        self.calls.append((fn, args))
        return object()


def test_selected_source_extraction_queues_only_that_statement(tmp_path, monkeypatch):
    store = CaseStore(base_dir=str(tmp_path / "cases"))
    executor = _CapturingExecutor()
    monkeypatch.setattr(intake, "_store", store)
    monkeypatch.setattr(intake, "_extraction_executor", executor)

    manifest = store.create_case(company_name="Demo Pte Ltd")
    case_id = manifest["case_id"]
    case_root = store._case_path(case_id)
    raw = case_root / "raw" / "financials"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "FY2024.pdf").write_bytes(b"%PDF-1.4 selected")
    (raw / "FY2023.pdf").write_bytes(b"%PDF-1.4 untouched")
    _write_json(raw / "_meta.json", {
        "FY2024.pdf": {"uploaded_at": "2026-01-01T00:00:00", "extraction_status": "ready"},
        "FY2023.pdf": {"uploaded_at": "2026-01-01T00:00:00", "extraction_status": "ready"},
    })

    parsed = case_root / "parsed" / "financials"
    _write_json(parsed / "src2024" / "manifest.json", {
        "source_id": "src2024",
        "original_filename": "FY2024.pdf",
        "original_path": "raw/financials/FY2024.pdf",
        "entity": {"name": "Demo Pte Ltd", "uen": "202400001A", "framework": "SFRS"},
        "columns": [{"perimeter": "company", "fy": "FY2024"}],
        "blocks": [],
        "review": {"status": "approved", "approved_at": "2026-01-02T00:00:00"},
    })
    _write_json(parsed / "index.json", {
        "sources": [
            {
                "source_id": "src2024",
                "original_filename": "FY2024.pdf",
                "review": {"status": "approved"},
            },
            {
                "source_id": "src2023",
                "original_filename": "FY2023.pdf",
                "review": {"status": "approved"},
            },
        ],
        "blocks": [],
    })

    response = intake.trigger_selected_source_extraction(case_id, "src2024")
    body = json.loads(response.body)

    assert response.status_code == 202
    assert body["files_queued"] == ["FY2024.pdf"]
    assert len(executor.calls) == 1
    _, args = executor.calls[0]
    assert args[3] == "FY2024.pdf"
    assert args[4] == "src2024"

    meta = json.loads((raw / "_meta.json").read_text(encoding="utf-8"))
    assert meta["FY2024.pdf"]["extraction_status"] == "queued"
    assert meta["FY2023.pdf"]["extraction_status"] == "ready"

    source_manifest = json.loads((parsed / "src2024" / "manifest.json").read_text(encoding="utf-8"))
    assert "review" not in source_manifest
    index = json.loads((parsed / "index.json").read_text(encoding="utf-8"))
    statuses = {s["source_id"]: s.get("review", {}).get("status") for s in index["sources"]}
    assert statuses == {"src2024": "pending", "src2023": "approved"}
