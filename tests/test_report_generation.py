import json
from pathlib import Path

from core.report.generator import (
    load_case_context,
    normalize_section_numbers,
    render_financial_snapshot,
)
from core.report.template import SECTIONS_FS_ONLY, build_section_context


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_fs_only_report_sections_are_contiguously_numbered():
    numbers = [s["number"] for s in SECTIONS_FS_ONLY]

    assert numbers == list(range(1, len(SECTIONS_FS_ONLY) + 1))


def test_report_section_number_normalizer_fills_existing_gaps():
    sections, changed = normalize_section_numbers([
        {"code": "executive_credit_view", "number": 1},
        {"code": "borrower_profile", "number": 2},
        {"code": "financial_snapshot", "number": 4},
        {"code": "strengths", "number": 14},
    ])

    assert changed is True
    assert [s["number"] for s in sections] == [1, 2, 3, 4]


def test_report_context_prefers_merged_financials_for_latest_fy(tmp_path):
    case_root = tmp_path / "case"

    _write_json(case_root / "features" / "fs_analytics.json", {
        "entity": {"name": "Demo Pte Ltd"},
        "perimeter": "company",
        "fys": ["FY2024", "FY2023"],
        "summary_ratios": {},
        "by_fy": {
            "FY2024": {"raw": {"revenue": None, "pat": None}, "ratios": {}},
            "FY2023": {"raw": {"revenue": 583626, "pat": -1264399}, "ratios": {}},
        },
        "trends": {},
    })
    _write_json(case_root / "parsed" / "sg_ingestion.json", {
        "periods": [
            {"perimeter": "company", "fy": "FY2024", "period_end": "31 December 2024", "currency": "SGD"},
            {"perimeter": "company", "fy": "FY2023", "period_end": "31 December 2023", "currency": "SGD"},
        ],
        "review_flags": [],
    })
    _write_json(case_root / "parsed" / "financials" / "merged" / "soci__company.json", {
        "statement": "soci",
        "perimeter": "company",
        "fys": ["FY2024", "FY2023"],
        "rows": [
            {
                "canonical_code": "pl_revenue",
                "label": "Revenue",
                "values": {"FY2024": 362570, "FY2023": 583626},
            },
            {
                "canonical_code": "pl_pbt",
                "label": "Loss Before Tax",
                "values": {"FY2024": -983684, "FY2023": -1264399},
            },
            {
                "canonical_code": "pl_pat",
                "label": "Loss for the Year",
                "values": {"FY2024": -983684, "FY2023": -1264399},
            },
        ],
    })
    _write_json(case_root / "parsed" / "financials" / "merged" / "sofp__company.json", {
        "statement": "sofp",
        "perimeter": "company",
        "fys": ["FY2024", "FY2023"],
        "rows": [
            {
                "canonical_code": "bs_total_assets",
                "label": "Total Assets",
                "values": {"FY2024": 1450714, "FY2023": 2117763},
            },
            {
                "canonical_code": "bs_total_equity",
                "label": "Total Equity",
                "values": {"FY2024": 894232, "FY2023": 1731449},
            },
        ],
    })

    context = load_case_context(case_root)
    analytics = context["analytics"]

    assert analytics["by_fy"]["FY2024"]["raw"]["revenue"] == 362570.0
    assert analytics["by_fy"]["FY2024"]["raw"]["pat"] == -983684.0
    assert analytics["by_fy"]["FY2024"]["ratios"]["pat_margin"] == -983684.0 / 362570.0
    assert analytics["by_fy"]["FY2024"]["ratios"]["return_on_assets"] == -983684.0 / 1450714.0
    assert analytics["by_fy"]["FY2024"]["ratios"]["return_on_equity"] == -983684.0 / 894232.0

    snapshot = render_financial_snapshot(context)
    assert "| Revenue | 362,570 | 583,626 |" in snapshot
    assert "| PAT | -983,684 | -1,264,399 |" in snapshot

    profitability_section = next(s for s in SECTIONS_FS_ONLY if s["code"] == "profitability")
    profitability_context = build_section_context(profitability_section, context)
    assert "### FY2024" in profitability_context
    assert "- pat_margin:" in profitability_context
    assert "- return_on_assets:" in profitability_context
    assert "- return_on_equity:" in profitability_context


def test_report_context_uses_onboarding_ssic_when_acra_profile_blank(tmp_path):
    case_root = tmp_path / "case"

    _write_json(case_root / "manifest.json", {
        "company_name": "GOIMPACT CAPITAL PARTNERS (SINGAPORE) PTE. LTD.",
        "uen": "202037175R",
        "entity_type": "Private Company Limited by Shares",
        "company_status": "Live Company",
        "primary_ssic_code": "85409",
        "primary_ssic_desc": "Training courses n.e.c.",
        "industry_hint": "Education and training",
        "country": "Singapore",
        "jurisdiction": "Singapore",
    })
    _write_json(case_root / "features" / "fs_analytics.json", {
        "entity": {},
        "perimeter": "company",
        "fys": ["FY2024"],
        "summary_ratios": {},
        "by_fy": {"FY2024": {"raw": {"revenue": 1000}, "ratios": {}}},
        "trends": {},
    })
    _write_json(case_root / "parsed" / "acra_profile.json", {
        "uen": "",
        "entity_name": "",
        "primary_ssic_code": "",
        "primary_ssic_desc": "",
    })
    _write_json(case_root / "parsed" / "sg_ingestion.json", {"periods": [], "review_flags": []})

    context = load_case_context(case_root)
    entity = context["analytics"]["entity"]

    assert entity["name"] == "GOIMPACT CAPITAL PARTNERS (SINGAPORE) PTE. LTD."
    assert entity["uen"] == "202037175R"
    assert entity["ssic_code"] == "85409"
    assert entity["ssic_description"] == "Training courses n.e.c."

    borrower_section = next(s for s in SECTIONS_FS_ONLY if s["code"] == "borrower_profile")
    borrower_context = build_section_context(borrower_section, context)

    assert "Primary SSIC: 85409 - Training courses n.e.c." in borrower_context
    assert "- primary_ssic_code: 85409" in borrower_context
    assert "- primary_ssic_desc: Training courses n.e.c." in borrower_context
    assert "Use onboarding case profile fields when ACRA profile fields are blank." in borrower_context
