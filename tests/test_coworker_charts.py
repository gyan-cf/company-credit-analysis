"""
Tests for Phase 6 — chart tools that emit Vega-Lite v5 specs.

Each test verifies structural validity of the spec (the things the
frontend's react-vega renderer requires) plus the metadata the agent loop
surfaces back to the analyst (chart_type, metric, summary). We don't
actually render the spec — that's the frontend's job — but we sanity-check
that the data block is populated and encoding fields point at real columns.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _seed_case(tmp_path: Path, case_id: str = "case01") -> Path:
    case_root = tmp_path / case_id
    for sub in ("raw", "parsed", "features", "agents", "chat"):
        (case_root / sub).mkdir(parents=True, exist_ok=True)
    (case_root / "manifest.json").write_text(json.dumps({
        "case_id": case_id, "company_name": "Acme Pte Ltd",
        "currency": "SGD",
    }), encoding="utf-8")
    (case_root / "features" / "fs_analytics.json").write_text(json.dumps({
        "entity": {"name": "Acme Pte Ltd"},
        "perimeter": "company",
        "fys": ["FY2024", "FY2023", "FY2022"],
        "summary_ratios": {},
        "by_fy": {
            "FY2022": {
                "fy": "FY2022", "currency": "SGD",
                "raw": {"revenue": 8_000_000, "ebitda": 1_400_000, "cash": 2_500_000},
                "ratios": {"interest_coverage": 2.5, "current_ratio": 1.8,
                           "ebitda_margin": 0.175, "debt_equity": 2.0},
            },
            "FY2023": {
                "fy": "FY2023", "currency": "SGD",
                "raw": {"revenue": 10_000_000, "ebitda": 1_200_000, "cash": 1_500_000},
                "ratios": {"interest_coverage": 1.8, "current_ratio": 1.4,
                           "ebitda_margin": 0.12, "debt_equity": 2.8},
            },
            "FY2024": {
                "fy": "FY2024", "currency": "SGD",
                "raw": {"revenue": 11_000_000, "ebitda": 900_000, "cash": 490_000},
                "ratios": {"interest_coverage": 1.2, "current_ratio": 1.44,
                           "ebitda_margin": 0.082, "debt_equity": 3.5},
            },
        },
        "trends": {},
        "review_flags": [],
    }), encoding="utf-8")
    return case_root


def _point_tools_at(tmp_path: Path):
    from core.cases.case_store import CaseStore
    import core.coworker.tools.charts as charts_tool
    charts_tool._store = CaseStore(base_dir=str(tmp_path))


def _assert_vega_skeleton(spec: dict, *, expected_data: bool = True):
    assert isinstance(spec, dict)
    assert spec.get("$schema", "").startswith("https://vega.github.io/schema/vega-lite/")
    assert "title" in spec
    assert spec.get("width") == "container"
    assert isinstance(spec.get("height"), int) and spec["height"] > 0
    if expected_data:
        # Either `data` at top level or per-layer data — accept either.
        has_top_data = isinstance(spec.get("data"), dict) and isinstance(spec["data"].get("values"), list)
        has_layer_data = "layer" in spec and any(
            isinstance(layer.get("data", {}).get("values"), list)
            for layer in spec["layer"]
        )
        assert has_top_data or has_layer_data, "Spec missing data.values"


# ---- plot_metric ----------------------------------------------------------

def test_plot_metric_revenue_bar_default(tmp_path):
    _seed_case(tmp_path)
    _point_tools_at(tmp_path)
    from core.coworker.tools import dispatch
    out = dispatch("plot_metric", "case01", {"metric": "revenue"})
    assert not out.get("is_error"), out
    r = out["result"]
    assert r["available"] is True
    assert r["chart_type"] == "bar"
    assert r["metric"] == "revenue"
    assert r["fys"] == ["FY2022", "FY2023", "FY2024"]
    assert r["values"][-1] == 11_000_000

    spec = r["vega_spec"]
    _assert_vega_skeleton(spec)
    assert spec["mark"]["type"] == "bar"
    assert spec["encoding"]["x"]["field"] == "fy"
    assert spec["encoding"]["y"]["field"] == "value"
    # Data points only include FYs with non-null values.
    assert len(spec["data"]["values"]) == 3
    # Summary mentions the delta direction.
    assert "Revenue" in r["summary"]


def test_plot_metric_ratio_default_to_line(tmp_path):
    _seed_case(tmp_path)
    _point_tools_at(tmp_path)
    from core.coworker.tools import dispatch
    out = dispatch("plot_metric", "case01", {"metric": "ebitda_margin"})
    r = out["result"]
    assert r["chart_type"] == "line"
    spec = r["vega_spec"]
    assert spec["mark"]["type"] == "line"
    # Percent ratios use percentage y-axis formatting.
    assert "%" in spec["encoding"]["y"]["axis"]["format"]


def test_plot_metric_explicit_kind_overrides_default(tmp_path):
    _seed_case(tmp_path)
    _point_tools_at(tmp_path)
    from core.coworker.tools import dispatch
    out = dispatch("plot_metric", "case01", {"metric": "revenue", "kind": "line"})
    assert out["result"]["vega_spec"]["mark"]["type"] == "line"


def test_plot_metric_fy_filter(tmp_path):
    _seed_case(tmp_path)
    _point_tools_at(tmp_path)
    from core.coworker.tools import dispatch
    out = dispatch("plot_metric", "case01", {"metric": "revenue", "fys": ["FY2023", "FY2024"]})
    r = out["result"]
    assert r["fys"] == ["FY2023", "FY2024"]
    assert len(r["vega_spec"]["data"]["values"]) == 2


def test_plot_metric_unknown_metric_lists_samples(tmp_path):
    _seed_case(tmp_path)
    _point_tools_at(tmp_path)
    from core.coworker.tools import dispatch
    out = dispatch("plot_metric", "case01", {"metric": "no_such_metric"})
    r = out["result"]
    assert r["available"] is False
    assert "available_raw_sample" in r or "available_ratios_sample" in r


def test_plot_metric_no_analytics_yet(tmp_path):
    # Seed manifest but no fs_analytics.
    case_root = tmp_path / "case01"
    case_root.mkdir()
    (case_root / "manifest.json").write_text(
        json.dumps({"case_id": "case01", "company_name": "X"}), encoding="utf-8",
    )
    _point_tools_at(tmp_path)
    from core.coworker.tools import dispatch
    out = dispatch("plot_metric", "case01", {"metric": "revenue"})
    assert out["result"]["available"] is False
    assert "fs_analytics" in out["result"]["reason"].lower() or "run /analyze" in out["result"]["reason"].lower()


# ---- plot_ratio_vs_policy ------------------------------------------------

def test_plot_ratio_vs_policy_with_threshold(tmp_path):
    _seed_case(tmp_path)
    _point_tools_at(tmp_path)
    from config.config import get_config
    get_config().config.setdefault("portfolio_norms", {})["interest_coverage_min"] = 1.5
    from core.coworker.tools import dispatch
    out = dispatch("plot_ratio_vs_policy", "case01", {"metric": "interest_coverage"})
    r = out["result"]
    assert r["available"] is True
    assert r["chart_type"] == "ratio_vs_policy"
    assert r["threshold"] == 1.5
    assert r["policy_direction"] == "min"

    spec = r["vega_spec"]
    _assert_vega_skeleton(spec, expected_data=False)
    assert "layer" in spec
    # At least 3 layers: shaded band + line + threshold rule.
    assert len(spec["layer"]) >= 3
    # One of the layers must be the threshold rule.
    marks = [
        (layer.get("mark", {}) if isinstance(layer.get("mark"), dict) else {"type": layer.get("mark")})
        for layer in spec["layer"]
    ]
    assert any(m.get("type") == "rule" for m in marks)
    # Latest FY2024 IC = 1.2 → below 1.5 → BREACH in summary.
    assert "BREACH" in r["summary"]


def test_plot_ratio_vs_policy_no_threshold_falls_back(tmp_path):
    _seed_case(tmp_path)
    _point_tools_at(tmp_path)
    # Use a ratio that doesn't have a policy hint (ebitda_margin isn't in
    # _POLICY_HINTS).
    from core.coworker.tools import dispatch
    out = dispatch("plot_ratio_vs_policy", "case01", {"metric": "ebitda_margin"})
    r = out["result"]
    assert r["available"] is True
    assert r["threshold"] is None
    # Without threshold there are no shaded breach band or rule layers —
    # just the line layer.
    assert len(r["vega_spec"]["layer"]) == 1


def test_plot_ratio_vs_policy_max_direction(tmp_path):
    _seed_case(tmp_path)
    _point_tools_at(tmp_path)
    from config.config import get_config
    get_config().config.setdefault("portfolio_norms", {})["debt_equity_max"] = 3.0
    from core.coworker.tools import dispatch
    out = dispatch("plot_ratio_vs_policy", "case01", {"metric": "debt_equity"})
    r = out["result"]
    assert r["policy_direction"] == "max"
    assert r["threshold"] == 3.0
    # Latest FY2024 D/E = 3.5 > 3.0 → BREACH.
    assert "BREACH" in r["summary"]


# ---- compare_metrics -----------------------------------------------------

def test_compare_metrics_same_family(tmp_path):
    _seed_case(tmp_path)
    _point_tools_at(tmp_path)
    from core.coworker.tools import dispatch
    out = dispatch("compare_metrics", "case01",
                   {"metrics": ["current_ratio", "interest_coverage"]})
    r = out["result"]
    assert r["available"] is True
    assert r["chart_type"] == "comparison"
    assert r["metrics"] == ["current_ratio", "interest_coverage"]
    spec = r["vega_spec"]
    _assert_vega_skeleton(spec)
    # Color encoding is the multi-series differentiator.
    assert spec["encoding"]["color"]["field"] == "metric"
    # Each FY × metric combo is a row in the data.
    rows = spec["data"]["values"]
    assert len(rows) == 6  # 2 metrics × 3 FYs


def test_compare_metrics_mixed_family_rejected(tmp_path):
    _seed_case(tmp_path)
    _point_tools_at(tmp_path)
    from core.coworker.tools import dispatch
    out = dispatch("compare_metrics", "case01",
                   {"metrics": ["revenue", "ebitda_margin"]})
    r = out["result"]
    assert r["available"] is False
    assert "unit families" in r["reason"]
    assert "metrics_by_family" in r


def test_compare_metrics_validates_count(tmp_path):
    _seed_case(tmp_path)
    _point_tools_at(tmp_path)
    from core.coworker.tools import dispatch
    too_few = dispatch("compare_metrics", "case01", {"metrics": ["revenue"]})
    assert too_few.get("is_error") is True
    too_many = dispatch("compare_metrics", "case01",
                        {"metrics": ["a", "b", "c", "d", "e", "f"]})
    assert too_many.get("is_error") is True


# ---- registry sanity ----------------------------------------------------

def test_chart_tools_registered():
    from core.coworker.tools import tool_names
    names = tool_names()
    for expected in ("plot_metric", "plot_ratio_vs_policy", "compare_metrics"):
        assert expected in names, f"missing tool: {expected}"
