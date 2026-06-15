"""
Tests for Phase-3 additions: analyst notes + dynamic suggestions.

Covers:
  - CaseStore round-trip for analyst_notes.md
  - build_case_header injects the notes block when present, omits when empty
  - agent_loop picks notes up live (no restart needed)
  - build_suggestions branches on case state (unanalysed / red flags /
    cross-findings / weak coverage / probes / report present)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---- shared fixture: seed a case dir ------------------------------------------

def _seed_case(tmp_path: Path, case_id: str = "case01", *, analysed: bool = True) -> Path:
    case_root = tmp_path / case_id
    for sub in ("raw", "parsed", "features", "agents", "chat"):
        (case_root / sub).mkdir(parents=True, exist_ok=True)

    (case_root / "manifest.json").write_text(json.dumps({
        "case_id": case_id,
        "company_name": "Acme Pte Ltd",
        "industry_hint": "logistics",
        "currency": "SGD",
        "uen": "201912345A",
        "status": "completed" if analysed else "created",
    }), encoding="utf-8")

    if analysed:
        (case_root / "features" / "fs_analytics.json").write_text(json.dumps({
            "entity": {"name": "Acme Pte Ltd", "framework": "SFRS"},
            "perimeter": "company",
            "fys": ["FY2023", "FY2024"],
            "by_fy": {
                "FY2024": {"raw": {}, "ratios": {
                    "interest_coverage": 1.2, "current_ratio": 1.4,
                }},
            },
            "trends": {}, "review_flags": [],
        }), encoding="utf-8")
        (case_root / "assessment_summary.json").write_text(json.dumps({
            "cards": [{
                "card_type": "FS",
                "risks": [
                    {"id": "r1", "severity": "high", "message": "EBITDA collapsed"},
                    {"id": "r2", "severity": "high", "message": "Coverage <1.5x"},
                ],
            }],
            "cross_findings": [
                {"severity": "medium", "message": "SoFP cash vs CFO closing"},
            ],
        }), encoding="utf-8")
        (case_root / "agents" / "qualitative.json").write_text(json.dumps({
            "probes": [{"priority": "high", "question": "Explain EBITDA decline"}],
        }), encoding="utf-8")

    return case_root


def _store_at(tmp_path: Path):
    from core.cases.case_store import CaseStore
    return CaseStore(base_dir=str(tmp_path))


# ---- analyst notes -------------------------------------------------------------

def test_notes_round_trip(tmp_path):
    _seed_case(tmp_path)
    store = _store_at(tmp_path)
    assert store.load_analyst_notes("case01") == ""

    meta = store.save_analyst_notes(
        "case01",
        "FY22 receivables: confirmed 272,994 (override the extraction).",
    )
    assert meta["length"] > 0
    assert meta["case_id"] == "case01"
    assert store.load_analyst_notes("case01").startswith("FY22 receivables")


def test_notes_save_empty_clears_but_keeps_file(tmp_path):
    _seed_case(tmp_path)
    store = _store_at(tmp_path)
    store.save_analyst_notes("case01", "scratch")
    store.save_analyst_notes("case01", "")
    notes_path = tmp_path / "case01" / "analyst_notes.md"
    assert notes_path.exists()
    assert notes_path.read_text(encoding="utf-8") == ""


def test_notes_save_for_unknown_case_raises(tmp_path):
    store = _store_at(tmp_path)
    with pytest.raises(FileNotFoundError):
        store.save_analyst_notes("does-not-exist", "anything")


# ---- prompt injection ---------------------------------------------------------

def test_case_header_includes_notes_block_when_present():
    from prompts.coworker_system_prompt import build_case_header
    manifest = {"company_name": "Acme", "industry_hint": "logistics"}
    header = build_case_header(manifest, analyst_notes="FY22 receivables override = 272,994")
    assert "Analyst notes" in header
    assert "FY22 receivables override" in header
    assert "persistent memory" in header.lower()


def test_case_header_omits_notes_block_when_empty():
    from prompts.coworker_system_prompt import build_case_header
    header = build_case_header({"company_name": "Acme"}, analyst_notes="   \n  ")
    assert "Analyst notes" not in header


def test_agent_loop_reads_notes_into_system_prompt(tmp_path, monkeypatch):
    """Mock-mode loop: the system prompt is constructed but no LLM is called."""
    _seed_case(tmp_path)
    store = _store_at(tmp_path)
    store.save_analyst_notes("case01", "Sponsor confirmed FX is hedged — DO NOT flag FX as risk.")

    # Force mock mode + point the agent_loop's store at our tmp dir.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from config.config import get_config
    get_config().config.setdefault("anthropic", {})["api_key"] = ""

    import core.coworker.agent_loop as al
    al._store = store

    # Capture the system prompt by patching the build_case_header import
    # used inside agent_loop. Easier: monkeypatch ClaudeClient construction
    # to fail and catch the system prompt through a sidecar list.
    captured = {}
    real_build = al.build_case_header
    def spy(manifest, fs_analytics=None, assessment=None, analyst_notes=None):
        captured["notes"] = analyst_notes
        return real_build(manifest, fs_analytics, assessment, analyst_notes)
    monkeypatch.setattr(al, "build_case_header", spy)

    events = list(al.run_agent_turn("case01", "hello", history=[]))
    assert captured.get("notes", "").startswith("Sponsor confirmed FX is hedged")
    assert events[-1]["type"] == "done"


# ---- suggestions --------------------------------------------------------------

def test_suggestions_for_unanalysed_case(tmp_path):
    _seed_case(tmp_path, analysed=False)
    from core.coworker.suggestions import build_suggestions
    out = build_suggestions("case01", store=_store_at(tmp_path))
    assert 3 <= len(out) <= 5
    labels = [s["label"] for s in out]
    assert any("Run analysis" in l or "Next steps" in l for l in labels)


def test_suggestions_with_high_severity_findings(tmp_path):
    _seed_case(tmp_path)  # default seed has 2 high-severity risks
    from core.coworker.suggestions import build_suggestions
    out = build_suggestions("case01", store=_store_at(tmp_path))
    # First (or near-first) suggestion should reference the top risks.
    assert any("Top risks" in s["label"] for s in out)
    top_msg = next(s["message"] for s in out if s["label"] == "Top risks")
    assert "2 high-severity" in top_msg


def test_suggestions_with_cross_findings(tmp_path):
    _seed_case(tmp_path)
    from core.coworker.suggestions import build_suggestions
    out = build_suggestions("case01", store=_store_at(tmp_path))
    assert any("Cross-source gaps" in s["label"] for s in out)


def test_suggestions_with_coverage_shortfall(tmp_path):
    _seed_case(tmp_path)
    from core.coworker.suggestions import build_suggestions
    out = build_suggestions("case01", store=_store_at(tmp_path))
    # Latest IC seeded = 1.2 → below the 1.5 threshold
    assert any("Coverage shortfall" in s["label"] for s in out)


def test_suggestions_unknown_case_returns_defaults(tmp_path):
    from core.coworker.suggestions import build_suggestions, _DEFAULT_FALLBACK
    out = build_suggestions("ghost-case", store=_store_at(tmp_path))
    assert out == _DEFAULT_FALLBACK


def test_suggestions_cap_at_five(tmp_path):
    _seed_case(tmp_path)
    from core.coworker.suggestions import build_suggestions
    out = build_suggestions("case01", store=_store_at(tmp_path))
    assert len(out) <= 5
