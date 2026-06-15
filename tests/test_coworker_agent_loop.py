"""
Smoke tests for the co-worker agent loop.

Scope:
  - Mock mode (no API key) returns a streamed delta + done event.
  - Each tool dispatches and surfaces structured citations.
  - dispatch() recovers from bad input / unknown tool without raising.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


# Ensure the agent loop hits the mock fallback regardless of dev env.
@pytest.fixture(autouse=True)
def _no_api_keys(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # The config singleton may have already cached a key — patch via the
    # public getter as well.
    from config.config import get_config
    cfg = get_config()
    cfg.config.setdefault("anthropic", {})["api_key"] = ""


def _seed_case(tmp_path: Path, case_id: str = "case01") -> Path:
    """Create a minimal case directory the tools + agent loop can read."""
    case_root = tmp_path / case_id
    for sub in ("raw", "parsed", "features", "agents", "chat"):
        (case_root / sub).mkdir(parents=True, exist_ok=True)

    manifest = {
        "case_id": case_id,
        "company_name": "Acme Pte Ltd",
        "industry_hint": "logistics",
        "country": "Singapore",
        "currency": "SGD",
        "status": "completed",
        "uen": "201912345A",
    }
    (case_root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    fs_analytics = {
        "entity": {
            "name": "Acme Pte Ltd",
            "uen": "201912345A",
            "framework": "SFRS",
            "audited": True,
            "consolidated": False,
        },
        "perimeter": "company",
        "fys": ["FY2023", "FY2024"],
        "summary_ratios": {"interest_coverage": 1.2, "debt_equity": 3.5},
        "by_fy": {
            "FY2023": {
                "fy": "FY2023",
                "currency": "SGD",
                "raw": {"revenue": 10_000_000, "ebitda": 1_200_000,
                        "pat": 600_000, "total_debt": 6_000_000,
                        "total_equity": 2_000_000, "cfo": 800_000,
                        "total_assets": 9_000_000, "gross_profit": 3_000_000},
                "ratios": {"interest_coverage": 1.8, "debt_equity": 3.0,
                           "ebitda_margin": 0.12},
            },
            "FY2024": {
                "fy": "FY2024",
                "currency": "SGD",
                "raw": {"revenue": 11_000_000, "ebitda": 900_000,
                        "pat": 200_000, "total_debt": 7_500_000,
                        "total_equity": 2_100_000, "cfo": 400_000,
                        "total_assets": 10_500_000, "gross_profit": 3_100_000},
                "ratios": {"interest_coverage": 1.2, "debt_equity": 3.5,
                           "ebitda_margin": 0.082},
            },
        },
        "trends": {"interest_coverage_yoy_pct": -0.333},
        "review_flags": [],
    }
    (case_root / "features" / "fs_analytics.json").write_text(
        json.dumps(fs_analytics), encoding="utf-8",
    )

    assessment = {
        "cards": [
            {
                "card_type": "FS",
                "summary_title": "FS Spread",
                "risks": [
                    {"id": "r1", "severity": "high", "message": "EBITDA margin collapsed YoY"},
                    {"id": "r2", "severity": "medium", "message": "Receivables growing faster than revenue"},
                    {"id": "r3", "severity": "low", "message": "Minor classification inconsistency in FY2022"},
                ],
            },
            {
                "card_type": "INDUSTRY",
                "summary_title": "Logistics overlay",
                "risks": [
                    {"id": "i1", "severity": "high", "message": "Sector EBITDA margins under pressure from fuel costs"},
                ],
            },
        ],
        "cross_findings": [
            {"id": "x1", "severity": "medium", "message": "Cash on SoFP differs from CFO closing balance", "source": "CROSS"},
        ],
    }
    (case_root / "assessment_summary.json").write_text(
        json.dumps(assessment), encoding="utf-8",
    )

    # Merged statement block — what get_statement reads.
    merged_dir = case_root / "parsed" / "financials" / "merged"
    merged_dir.mkdir(parents=True, exist_ok=True)
    sofp_block = {
        "statement": "sofp",
        "statement_name": "Statement of Financial Position",
        "perimeter": "company",
        "fys": ["FY2023", "FY2024"],
        "rows": [
            {"row_type": "section_header", "label": "Assets", "section_path": ["Assets"], "indent_level": 0, "values": {}},
            {
                "row_type": "line", "label": "Cash and Cash Equivalents",
                "canonical_code": "bs_cash",
                "section_path": ["Assets", "Current Assets"],
                "indent_level": 2,
                "values": {"FY2023": 1_566_827, "FY2024": 490_541},
                "note_ref": "4",
            },
            {
                "row_type": "line", "label": "Trade and Other Receivables",
                "canonical_code": "bs_trade_other_recv",
                "section_path": ["Assets", "Current Assets"],
                "indent_level": 2,
                "values": {"FY2023": 272_994, "FY2024": 318_517},
            },
            {"row_type": "spacer", "label": "", "values": {}},
        ],
    }
    (merged_dir / "sofp__company.json").write_text(json.dumps(sofp_block), encoding="utf-8")

    # Wiki with chunks + a note file + a note_links entry.
    wiki = case_root / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    chunks = [
        {
            "chunk_id": "c1",
            "title": "Note 12 — Going concern",
            "doc_type": "note",
            "topics": ["going", "concern", "liquidity"],
            "text": "The directors have considered going concern given liquidity pressure.",
            "path": "notes/src1/note_12.md",
            "source_id": "src1",
            "source_file": "FY2024.pdf",
            "page_range": [42, 43],
        },
    ]
    (wiki / "chunks.jsonl").write_text(
        "\n".join(json.dumps(c) for c in chunks), encoding="utf-8",
    )
    notes_dir = wiki / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / "src1-note-4.md").write_text(
        "# Note 4 — Cash and Cash Equivalents\n\nCash balance is denominated in SGD.",
        encoding="utf-8",
    )
    (wiki / "note_links.json").write_text(
        json.dumps({
            "links": [
                {
                    "source_id": "src1",
                    "source_file": "FY2024.pdf",
                    "statement": "sofp",
                    "statement_title": "Statement of Financial Position",
                    "row_id": "src1:sofp:1",
                    "row_label": "Cash and Cash Equivalents",
                    "canonical_code": "bs_cash",
                    "row_page": 7,
                    "note_ref": "4",
                    "note_key": "4",
                    "note_no": "4",
                    "note_title": "CASH AND CASH EQUIVALENTS",
                    "note_page_range": [12, 12],
                    "note_wiki_path": "notes/src1-note-4.md",
                },
            ],
        }),
        encoding="utf-8",
    )

    # Agents/qualitative.json — probes the tool returns.
    agents_dir = case_root / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    qualitative = {
        "probes": [
            {
                "id": "q1",
                "question": "What measures are being implemented to improve the negative EBITDA margin?",
                "priority": "high",
                "theme": "revenue",
                "rationale": "EBITDA margin compressed sharply YoY.",
                "documents_requested": ["FY2025 forecast P&L"],
                "evidence_links": [["ebitda_margin", "FY2024", 0.082, ">= 0.10"]],
                "management_meeting": True,
            },
            {
                "id": "q2",
                "question": "Explain the increase in receivables ageing.",
                "priority": "medium",
                "theme": "liquidity",
                "rationale": "Receivables grew faster than revenue.",
                "documents_requested": ["Ageing schedule"],
                "evidence_links": [],
                "management_meeting": True,
            },
        ],
        "card_view": {"card_type": "QUALITATIVE"},
        "_metadata": {"agent": "QUALITATIVE", "success": True},
    }
    (agents_dir / "qualitative.json").write_text(json.dumps(qualitative), encoding="utf-8")

    # Credit report — what report tools read.
    reports_dir = case_root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_payload = {
        "case_id": case_id,
        "template": "credit_analysis",
        "generated_at": "2026-06-14T18:00:00Z",
        "entity_name": "Acme Pte Ltd",
        "fys": ["FY2023", "FY2024"],
        "section_count": 2,
        "sections": [
            {
                "code": "executive_credit_view",
                "number": 1,
                "title": "Executive Credit View",
                "source": "llm",
                "markdown": "Verdict: watchlist. EBITDA compressed sharply in FY2024...",
                "error": None,
            },
            {
                "code": "financial_snapshot",
                "number": 2,
                "title": "Financial Snapshot",
                "source": "deterministic",
                "markdown": "| Line item | FY2023 | FY2024 |\n|---|---|---|\n| Revenue | 10m | 11m |",
                "error": None,
            },
        ],
    }
    (reports_dir / "latest.json").write_text(json.dumps(report_payload), encoding="utf-8")

    return case_root


def _point_case_store_at(tmp_path: Path, monkeypatch):
    """Force CaseStore + the tool modules' cached stores at the tmp dir."""
    from config.config import get_config
    cfg = get_config()
    cfg.config.setdefault("cases", {})["base_dir"] = str(tmp_path)
    # Reset any module-level CaseStore singletons that captured the prod path.
    import core.coworker.tools.findings as findings_tool
    import core.coworker.tools.fs as fs_tool
    import core.coworker.tools.knowledge as kn_tool
    import core.coworker.tools.report as report_tool
    from core.cases.case_store import CaseStore
    store = CaseStore(base_dir=str(tmp_path))
    fs_tool._store = store
    kn_tool._store = store
    report_tool._store = store
    findings_tool._store = store


# ---- tools -------------------------------------------------------------------

def test_get_financial_summary_returns_latest_fy(tmp_path, monkeypatch):
    _seed_case(tmp_path)
    _point_case_store_at(tmp_path, monkeypatch)

    from core.coworker.tools import dispatch

    out = dispatch("get_financial_summary", "case01", {})
    assert not out.get("is_error"), out
    result = out["result"]
    assert result["available"] is True
    assert result["latest_fy"] == "FY2024"
    assert result["latest_raw"]["revenue"] == 11_000_000
    assert result["latest_ratios"]["interest_coverage"] == 1.2
    assert any(c.get("kind") == "fs_analytics" for c in out["citations"])


def test_get_ratio_includes_policy_breach_when_threshold_present(tmp_path, monkeypatch):
    _seed_case(tmp_path)
    _point_case_store_at(tmp_path, monkeypatch)

    from config.config import get_config
    get_config().config.setdefault("portfolio_norms", {})["interest_coverage_min"] = 1.5

    from core.coworker.tools import dispatch
    out = dispatch("get_ratio", "case01", {"name": "interest_coverage"})

    assert not out.get("is_error")
    result = out["result"]
    assert result["available"] is True
    assert [s["fy"] for s in result["series"]] == ["FY2023", "FY2024"]
    assert result["latest"]["value"] == 1.2
    assert result["policy_threshold"] == 1.5
    assert result["breaches_policy"] is True


def test_get_ratio_unknown_name_reports_available_keys(tmp_path, monkeypatch):
    _seed_case(tmp_path)
    _point_case_store_at(tmp_path, monkeypatch)

    from core.coworker.tools import dispatch
    out = dispatch("get_ratio", "case01", {"name": "not_a_real_ratio"})
    assert out["result"]["available"] is False
    assert "available_ratios" in out["result"]
    assert "interest_coverage" in out["result"]["available_ratios"]


def test_search_knowledge_returns_citation_with_page_range(tmp_path, monkeypatch):
    _seed_case(tmp_path)
    _point_case_store_at(tmp_path, monkeypatch)

    from core.coworker.tools import dispatch
    out = dispatch("search_knowledge", "case01", {"query": "going concern liquidity"})
    assert out["result"]["available"] is True
    assert out["result"]["count"] >= 1
    cite = out["citations"][0]
    assert cite["kind"] == "wiki"
    assert cite["page_range"] == [42, 43]
    assert cite["source_id"] == "src1"


def test_dispatch_handles_unknown_tool_gracefully():
    from core.coworker.tools import dispatch
    out = dispatch("does_not_exist", "case01", {})
    assert out["is_error"] is True
    assert "Unknown tool" in out["error"]


def test_dispatch_handles_bad_arguments():
    from core.coworker.tools import dispatch
    out = dispatch("get_ratio", "case01", {"wrong_arg": 1})
    assert out["is_error"] is True


# ---- agent loop -------------------------------------------------------------

def test_agent_loop_mock_mode_streams_delta_then_done(tmp_path, monkeypatch):
    _seed_case(tmp_path)
    _point_case_store_at(tmp_path, monkeypatch)
    # Make sure the agent loop's CaseStore reads from the tmp dir too.
    import core.coworker.agent_loop as al
    from core.cases.case_store import CaseStore
    al._store = CaseStore(base_dir=str(tmp_path))

    events = list(al.run_agent_turn("case01", "How's the company doing?", history=[]))
    assert events, "expected at least one event"
    assert events[-1]["type"] == "done"
    deltas = [e for e in events if e["type"] == "delta"]
    assert deltas, "mock mode should stream deltas"
    final = events[-1]
    assert isinstance(final["text"], str) and final["text"]
    assert final["tool_calls"] == []
    assert final["usage"]["output_tokens"] == 0


def test_agent_loop_returns_error_for_missing_case():
    from core.coworker.agent_loop import run_agent_turn
    events = list(run_agent_turn("no-such-case", "hello", history=[]))
    assert any(e["type"] == "error" for e in events)


# ---- get_statement ----------------------------------------------------------

def test_get_statement_returns_filtered_rows(tmp_path, monkeypatch):
    _seed_case(tmp_path)
    _point_case_store_at(tmp_path, monkeypatch)

    from core.coworker.tools import dispatch
    out = dispatch("get_statement", "case01", {
        "statement": "sofp",
        "canonical_code": "bs_cash",
    })
    assert not out.get("is_error"), out
    result = out["result"]
    assert result["available"] is True
    assert result["row_count_returned"] == 1
    row = result["rows"][0]
    assert row["canonical_code"] == "bs_cash"
    assert row["values"]["FY2024"] == 490541
    assert out["citations"][0]["kind"] == "statement"


def test_get_statement_label_substring_filter(tmp_path, monkeypatch):
    _seed_case(tmp_path)
    _point_case_store_at(tmp_path, monkeypatch)

    from core.coworker.tools import dispatch
    out = dispatch("get_statement", "case01", {
        "statement": "sofp",
        "label_contains": "receivable",
    })
    assert out["result"]["row_count_returned"] == 1
    assert out["result"]["rows"][0]["canonical_code"] == "bs_trade_other_recv"


def test_get_statement_unknown_statement_reports_error(tmp_path, monkeypatch):
    _seed_case(tmp_path)
    _point_case_store_at(tmp_path, monkeypatch)
    from core.coworker.tools import dispatch
    out = dispatch("get_statement", "case01", {"statement": "balance_sheet"})
    assert out["result"]["available"] is False
    assert "Unknown statement" in out["result"]["reason"]


def test_get_statement_missing_file_reports_gracefully(tmp_path, monkeypatch):
    _seed_case(tmp_path)
    _point_case_store_at(tmp_path, monkeypatch)
    from core.coworker.tools import dispatch
    # SoCI not seeded → tool should report unavailable but not raise.
    out = dispatch("get_statement", "case01", {"statement": "soci"})
    assert out["result"]["available"] is False
    assert "soci__company" in out["result"]["expected_path"]


# ---- read_note --------------------------------------------------------------

def test_read_note_returns_markdown_and_linked_rows(tmp_path, monkeypatch):
    _seed_case(tmp_path)
    _point_case_store_at(tmp_path, monkeypatch)

    from core.coworker.tools import dispatch
    out = dispatch("read_note", "case01", {"note_ref": "4", "source_id": "src1"})
    assert not out.get("is_error")
    result = out["result"]
    assert result["available"] is True
    assert result["match_count"] == 1
    note = result["notes"][0]
    assert note["note_title"] == "CASH AND CASH EQUIVALENTS"
    assert "SGD" in note["markdown"]
    assert note["referenced_by_rows"][0]["row_label"] == "Cash and Cash Equivalents"
    cite = out["citations"][0]
    assert cite["kind"] == "note"
    assert cite["page_range"] == [12, 12]


def test_read_note_unknown_ref_lists_available(tmp_path, monkeypatch):
    _seed_case(tmp_path)
    _point_case_store_at(tmp_path, monkeypatch)
    from core.coworker.tools import dispatch
    out = dispatch("read_note", "case01", {"note_ref": "99"})
    assert out["result"]["available"] is False
    assert any(n["note_ref"] == "4" for n in out["result"]["available_notes"])


# ---- report -----------------------------------------------------------------

def test_list_report_sections_returns_toc(tmp_path, monkeypatch):
    _seed_case(tmp_path)
    _point_case_store_at(tmp_path, monkeypatch)
    from core.coworker.tools import dispatch
    out = dispatch("list_report_sections", "case01", {})
    assert out["result"]["available"] is True
    codes = [s["code"] for s in out["result"]["sections"]]
    assert "executive_credit_view" in codes
    assert "financial_snapshot" in codes


def test_get_report_section_returns_markdown(tmp_path, monkeypatch):
    _seed_case(tmp_path)
    _point_case_store_at(tmp_path, monkeypatch)
    from core.coworker.tools import dispatch
    out = dispatch("get_report_section", "case01", {"code": "executive_credit_view"})
    assert out["result"]["available"] is True
    assert "Verdict" in out["result"]["markdown"]
    assert out["citations"][0]["section_code"] == "executive_credit_view"


def test_get_report_section_unknown_code_lists_available(tmp_path, monkeypatch):
    _seed_case(tmp_path)
    _point_case_store_at(tmp_path, monkeypatch)
    from core.coworker.tools import dispatch
    out = dispatch("get_report_section", "case01", {"code": "no_such_section"})
    assert out["result"]["available"] is False
    assert "executive_credit_view" in out["result"]["available_codes"]


# ---- findings ---------------------------------------------------------------

def test_list_red_flags_severity_filter_and_sort(tmp_path, monkeypatch):
    _seed_case(tmp_path)
    _point_case_store_at(tmp_path, monkeypatch)
    from core.coworker.tools import dispatch

    high_only = dispatch("list_red_flags", "case01", {"severity": "high"})
    assert high_only["result"]["available"] is True
    sevs = [f["severity"] for f in high_only["result"]["findings"]]
    assert sevs and all(s == "high" for s in sevs)
    assert len(sevs) >= 2  # FS r1 + INDUSTRY i1

    any_sev = dispatch("list_red_flags", "case01", {"severity": "any"})
    sevs_any = [f["severity"] for f in any_sev["result"]["findings"]]
    # Sorted high → low
    rank = {"high": 3, "medium": 2, "low": 1}
    assert all(rank[sevs_any[i]] >= rank[sevs_any[i + 1]] for i in range(len(sevs_any) - 1))
    # Cross findings included
    assert any(f["kind"] == "cross_finding" for f in any_sev["result"]["findings"])


def test_draft_probe_questions_prefers_high_priority(tmp_path, monkeypatch):
    _seed_case(tmp_path)
    _point_case_store_at(tmp_path, monkeypatch)
    from core.coworker.tools import dispatch
    out = dispatch("draft_probe_questions", "case01", {"priority": "high"})
    result = out["result"]
    assert result["available"] is True
    assert result["source"] == "qualitative_agent"
    assert all(p["priority"] == "high" for p in result["probes"])
    assert result["probes"][0]["id"] == "q1"


def test_draft_probe_questions_theme_filter(tmp_path, monkeypatch):
    _seed_case(tmp_path)
    _point_case_store_at(tmp_path, monkeypatch)
    from core.coworker.tools import dispatch
    out = dispatch("draft_probe_questions", "case01", {"theme": "liquidity"})
    probes = out["result"]["probes"]
    assert probes and all("liquidity" in (p["theme"] or "") for p in probes)


def test_draft_probe_questions_falls_back_to_assessment_when_no_probes(tmp_path, monkeypatch):
    case_root = _seed_case(tmp_path)
    # Wipe the qualitative agent file so the synth path triggers.
    (case_root / "agents" / "qualitative.json").write_text(
        json.dumps({"probes": []}), encoding="utf-8",
    )
    _point_case_store_at(tmp_path, monkeypatch)

    from core.coworker.tools import dispatch
    out = dispatch("draft_probe_questions", "case01", {})
    result = out["result"]
    assert result["available"] is True
    assert result["source"] == "synthesized_from_assessment"
    # Synthesised from FS card risks (severity high/medium).
    assert result["count"] >= 1


# ---- registry sanity check --------------------------------------------------

def test_all_new_tools_are_registered():
    from core.coworker.tools import tool_names
    names = tool_names()
    for expected in (
        "get_financial_summary",
        "get_ratio",
        "get_statement",
        "search_knowledge",
        "read_note",
        "list_report_sections",
        "get_report_section",
        "list_red_flags",
        "draft_probe_questions",
    ):
        assert expected in names, f"missing tool: {expected}"
