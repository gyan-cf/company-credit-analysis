"""
Tests for Phase 4 follow-up: override_extracted_value + rerun_analysis.

Covers:
  - override_extracted_value tool returns a preview without mutating.
  - Executor resolves (source_id, statement, canonical_code, perimeter, fy)
    → JSON path inside document.json, patches the cell, appends an audit
    row identical to what the review dashboard writes.
  - Missing source / statement block / column / row produce clear errors.
  - rerun_analysis tool returns a preview.
  - Executor submits the pipeline run to the background ThreadPoolExecutor
    and returns "queued" without blocking.
  - Document-patch helper is shared correctly between api/review.py and the
    co-worker executor (same audit shape, same path semantics).
"""

from __future__ import annotations

import json
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _seed_case_with_document(tmp_path: Path, case_id: str = "case01",
                             source_id: str = "src123") -> Path:
    """Lay down a case + one source with a realistic document.json shape."""
    case_root = tmp_path / case_id
    for sub in ("raw", "parsed", "features", "agents", "chat"):
        (case_root / sub).mkdir(parents=True, exist_ok=True)
    (case_root / "manifest.json").write_text(json.dumps({
        "case_id": case_id, "company_name": "Acme Pte Ltd",
        "industry_hint": "logistics", "status": "completed",
    }), encoding="utf-8")

    src_dir = case_root / "parsed" / "financials" / source_id
    src_dir.mkdir(parents=True, exist_ok=True)

    doc = {
        "document": {"source_id": source_id, "entity": {"name": "Acme"}},
        "blocks": [
            {"kind": "cover", "title": "Cover", "page_range": [1, 1], "markdown": "..."},
            {
                "kind": "statement", "type": "sofp",
                "title": "Statement of Financial Position",
                "page_range": [3, 4],
                "columns": [
                    {"id": "company_FY2024", "perimeter": "company", "fy": "FY2024"},
                    {"id": "company_FY2023", "perimeter": "company", "fy": "FY2023"},
                ],
                "rows": [
                    {
                        "row_type": "line", "label": "Cash and Cash Equivalents",
                        "canonical_code": "bs_cash", "indent_level": 2,
                        "values": {"company_FY2024": 490_541, "company_FY2023": 1_566_827},
                    },
                    {
                        "row_type": "line", "label": "Trade Receivables",
                        "canonical_code": "bs_trade_other_recv", "indent_level": 2,
                        "values": {"company_FY2024": 318_517, "company_FY2023": 272_994},
                    },
                ],
            },
            {
                "kind": "statement", "type": "soci", "title": "P&L",
                "page_range": [5, 5],
                "columns": [
                    {"id": "company_FY2024", "perimeter": "company", "fy": "FY2024"},
                ],
                "rows": [
                    {
                        "row_type": "line", "label": "Revenue",
                        "canonical_code": "pl_revenue", "indent_level": 0,
                        "values": {"company_FY2024": 11_000_000},
                    },
                ],
            },
        ],
    }
    (src_dir / "document.json").write_text(json.dumps(doc), encoding="utf-8")
    return case_root


def _store_at(tmp_path: Path):
    from core.cases.case_store import CaseStore
    return CaseStore(base_dir=str(tmp_path))


def _point_tools_at(tmp_path: Path):
    from core.cases.case_store import CaseStore
    import core.coworker.tools.actions as actions_tool
    actions_tool._store = CaseStore(base_dir=str(tmp_path))


# ---- override_extracted_value tool returns a preview --------------------------

def test_override_tool_returns_preview(tmp_path):
    _seed_case_with_document(tmp_path)
    _point_tools_at(tmp_path)
    from core.coworker.tools import dispatch
    out = dispatch("override_extracted_value", "case01", {
        "source_id": "src123", "statement": "sofp",
        "canonical_code": "bs_cash", "fy": "FY2024",
        "value": 500_000, "perimeter": "company",
        "reason": "Sponsor confirmed",
    })
    assert not out.get("is_error"), out
    result = out["result"]
    assert result["preview"] is True
    assert result["kind"] == "override_extracted_value"
    assert result["payload"]["canonical_code"] == "bs_cash"
    assert result["payload"]["value"] == 500_000

    # Pending file exists; document.json untouched.
    pending = tmp_path / "case01" / "pending_actions" / f"{result['token']}.json"
    assert pending.exists()
    doc = json.loads((tmp_path / "case01" / "parsed" / "financials" / "src123" / "document.json").read_text(encoding="utf-8"))
    assert doc["blocks"][1]["rows"][0]["values"]["company_FY2024"] == 490_541


def test_override_tool_validates_inputs(tmp_path):
    _seed_case_with_document(tmp_path)
    _point_tools_at(tmp_path)
    from core.coworker.tools import dispatch

    bad_statement = dispatch("override_extracted_value", "case01", {
        "source_id": "src123", "statement": "bs", "canonical_code": "bs_cash",
        "fy": "FY2024", "value": 1,
    })
    assert bad_statement.get("is_error") is True

    bad_perimeter = dispatch("override_extracted_value", "case01", {
        "source_id": "src123", "statement": "sofp", "canonical_code": "bs_cash",
        "fy": "FY2024", "value": 1, "perimeter": "consolidated",
    })
    assert bad_perimeter.get("is_error") is True


# ---- override executor mutates and audits ------------------------------------

def test_override_executor_patches_cell_and_appends_audit(tmp_path):
    _seed_case_with_document(tmp_path)
    store = _store_at(tmp_path)
    from core.coworker.pending_actions import (
        create_pending_action, execute_pending_action,
    )
    action = create_pending_action("case01", "override_extracted_value", {
        "source_id": "src123", "statement": "sofp",
        "canonical_code": "bs_cash", "perimeter": "company",
        "fy": "FY2023", "value": 1_500_000, "reason": "Misread",
    }, "desc", store=store)
    out = execute_pending_action("case01", action.token, store=store)
    assert out["ok"] is True
    result = out["result"]
    assert result["old_value"] == 1_566_827
    assert result["new_value"] == 1_500_000
    assert result["row_label"] == "Cash and Cash Equivalents"
    assert "company_FY2023" in result["column_id"]

    # document.json mutated.
    doc = json.loads((tmp_path / "case01" / "parsed" / "financials" / "src123" / "document.json").read_text(encoding="utf-8"))
    assert doc["blocks"][1]["rows"][0]["values"]["company_FY2023"] == 1_500_000

    # Audit row appended in the same shape the review dashboard reads.
    audits = json.loads((tmp_path / "case01" / "parsed" / "financials" / "src123" / "document.audits.json").read_text(encoding="utf-8"))
    assert len(audits) == 1
    entry = audits[0]
    assert entry["user"] == "coworker"
    assert entry["reason"] == "Misread"
    assert entry["old_value"] == 1_566_827
    assert entry["new_value"] == 1_500_000
    assert entry["path"] == ["blocks", 1, "rows", 0, "values", "company_FY2023"]


def test_override_executor_unknown_source_errors(tmp_path):
    _seed_case_with_document(tmp_path)
    store = _store_at(tmp_path)
    from core.coworker.pending_actions import (
        create_pending_action, execute_pending_action,
    )
    action = create_pending_action("case01", "override_extracted_value", {
        "source_id": "ghost", "statement": "sofp",
        "canonical_code": "bs_cash", "perimeter": "company",
        "fy": "FY2024", "value": 1,
    }, "desc", store=store)
    out = execute_pending_action("case01", action.token, store=store)
    assert out["ok"] is False
    assert "ghost" in out["error"]


def test_override_executor_unknown_column_lists_available(tmp_path):
    _seed_case_with_document(tmp_path)
    store = _store_at(tmp_path)
    from core.coworker.pending_actions import (
        create_pending_action, execute_pending_action,
    )
    action = create_pending_action("case01", "override_extracted_value", {
        "source_id": "src123", "statement": "sofp",
        "canonical_code": "bs_cash", "perimeter": "group",
        "fy": "FY2024", "value": 1,
    }, "desc", store=store)
    out = execute_pending_action("case01", action.token, store=store)
    assert out["ok"] is False
    # Error message lists available perimeter/fy combos.
    assert "company/FY2024" in out["error"] or "company/FY2023" in out["error"]


def test_override_executor_unknown_canonical_code_lists_sample(tmp_path):
    _seed_case_with_document(tmp_path)
    store = _store_at(tmp_path)
    from core.coworker.pending_actions import (
        create_pending_action, execute_pending_action,
    )
    action = create_pending_action("case01", "override_extracted_value", {
        "source_id": "src123", "statement": "sofp",
        "canonical_code": "bs_does_not_exist", "perimeter": "company",
        "fy": "FY2024", "value": 1,
    }, "desc", store=store)
    out = execute_pending_action("case01", action.token, store=store)
    assert out["ok"] is False
    assert "bs_cash" in out["error"]  # sample list includes existing codes


# ---- rerun_analysis ----------------------------------------------------------

def test_rerun_analysis_tool_returns_preview(tmp_path):
    _seed_case_with_document(tmp_path)
    _point_tools_at(tmp_path)
    from core.coworker.tools import dispatch
    out = dispatch("rerun_analysis", "case01", {"reason": "After receivables override"})
    assert not out.get("is_error")
    result = out["result"]
    assert result["preview"] is True
    assert result["kind"] == "rerun_analysis"
    assert "After receivables override" in result["payload"]["reason"]


def test_rerun_analysis_executor_submits_and_returns_quickly(tmp_path, monkeypatch):
    _seed_case_with_document(tmp_path)
    store = _store_at(tmp_path)

    # Stub the shared executor + pipeline so we don't actually run the
    # 30-60s LLM pipeline in tests.
    submitted = {"called": False, "result": None}
    fake_executor = MagicMock()
    def _capture_submit(fn, *args, **kwargs):
        submitted["called"] = True
        # Execute synchronously for the test, but capture that submit was called.
        f = Future()
        try:
            f.set_result(fn(*args, **kwargs))
        except Exception as e:
            f.set_exception(e)
        return f
    fake_executor.submit.side_effect = _capture_submit

    fake_pipeline = MagicMock()
    fake_pipeline.run = MagicMock(return_value={"ok": True})

    monkeypatch.setattr("api.main.executor", fake_executor)
    monkeypatch.setattr("api.main.pipeline", fake_pipeline)

    from core.coworker.pending_actions import (
        create_pending_action, execute_pending_action,
    )
    action = create_pending_action("case01", "rerun_analysis",
                                   {"reason": "Test rerun"}, "desc", store=store)
    out = execute_pending_action("case01", action.token, store=store)
    assert out["ok"] is True
    result = out["result"]
    assert result["queued"] is True
    assert "Test rerun" in result["summary"]
    assert submitted["called"] is True
    fake_pipeline.run.assert_called_once_with("case01")


# ---- shared patch helper is wired correctly ---------------------------------

def test_review_endpoint_and_coworker_share_patch_helper(tmp_path):
    """
    Both code paths route through core.cases.document_patch — verify by
    issuing two updates (one through the helper directly, one via the
    co-worker executor) and confirming both audit rows have the same shape.
    """
    _seed_case_with_document(tmp_path)
    store = _store_at(tmp_path)

    src_dir = tmp_path / "case01" / "parsed" / "financials" / "src123"

    # Direct helper call (simulates the api/review.py PATCH flow).
    from core.cases.document_patch import patch_document_cell
    entry_direct = patch_document_cell(
        src_dir,
        ["blocks", 1, "rows", 1, "values", "company_FY2024"],
        320_000,
        user="analyst",
        reason="Manual edit",
    )
    assert entry_direct["old_value"] == 318_517
    assert entry_direct["new_value"] == 320_000

    # Co-worker executor (Bob the agent path).
    from core.coworker.pending_actions import (
        create_pending_action, execute_pending_action,
    )
    action = create_pending_action("case01", "override_extracted_value", {
        "source_id": "src123", "statement": "sofp",
        "canonical_code": "bs_trade_other_recv", "perimeter": "company",
        "fy": "FY2023", "value": 270_000,
    }, "desc", store=store)
    out = execute_pending_action("case01", action.token, store=store)
    assert out["ok"] is True

    audits = json.loads((src_dir / "document.audits.json").read_text(encoding="utf-8"))
    assert len(audits) == 2
    # Both rows have identical shape.
    for row in audits:
        for key in ("at", "user", "path", "old_value", "new_value", "reason"):
            assert key in row
    assert audits[0]["user"] == "analyst"
    assert audits[1]["user"] == "coworker"


# ---- registry sanity --------------------------------------------------------

def test_phase4_followup_tools_registered():
    from core.coworker.tools import tool_names
    names = tool_names()
    for expected in ("override_extracted_value", "rerun_analysis"):
        assert expected in names, f"missing tool: {expected}"
