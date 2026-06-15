"""
Chart tools for the co-worker agent — Vega-Lite spec builders.

Each tool reads the case's analytics + (where relevant) the policy config
and produces a small Vega-Lite v5 spec the rail renders inline via
`react-vega`. Specs are intentionally minimal — width is set to "container"
so they reflow with the chat width, and tooltips give the analyst hover
detail without forcing wide chart formatting.

Tools:
    - plot_metric(metric, fys?, kind?)             line / bar of a single metric
    - plot_ratio_vs_policy(metric)                 ratio + policy threshold rule
    - compare_metrics(metrics, fys?)               multi-series comparison line

All return:
    {"result": {"chart_type", "title", "vega_spec", "summary", ...},
     "citations": [{"kind": "chart", "path": ..., "metric": ...}]}

Each spec uses Singapore-appropriate number formats: ',.0f' for amounts,
'.2f' for ratios, '.1%' for percentage ratios. Theme = clean light, with
the brand navy (#1f3a5f) as primary and a red rule for policy thresholds.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from config.config import get_config
from core.cases.case_store import CaseStore


_store = CaseStore()
_config = get_config()


# ---- shared theme / formatters --------------------------------------------

_BRAND_NAVY = "#1f3a5f"
_BRAND_LILAC = "#7c3aed"
_POLICY_RED = "#dc2626"
_SAFE_GREEN = "#10b981"
_CHART_HEIGHT = 220
_CHART_HEIGHT_TALL = 260

# Which raw FS line items + ratios are plottable and how they're labelled
# + formatted in tooltips and y-axis.
_METRIC_LABELS: Dict[str, str] = {
    # P&L
    "revenue": "Revenue",
    "gross_profit": "Gross profit",
    "ebitda": "EBITDA",
    "ebit": "EBIT",
    "pat": "Profit after tax",
    "cost_of_sales": "Cost of sales",
    "interest_expense": "Interest expense",
    # SoFP
    "total_assets": "Total assets",
    "total_equity": "Total equity",
    "total_debt": "Total debt",
    "current_assets": "Current assets",
    "current_liabilities": "Current liabilities",
    "cash": "Cash",
    "inventory": "Inventory",
    "trade_receivables": "Trade receivables",
    "trade_payables": "Trade payables",
    # SoCF
    "cfo": "CFO",
    "fcf": "Free cash flow",
    # Ratios
    "gross_margin": "Gross margin",
    "ebitda_margin": "EBITDA margin",
    "ebit_margin": "EBIT margin",
    "pat_margin": "PAT margin",
    "return_on_assets": "Return on assets",
    "return_on_equity": "Return on equity",
    "current_ratio": "Current ratio",
    "quick_ratio": "Quick ratio",
    "cash_ratio": "Cash ratio",
    "debt_equity": "Debt / equity",
    "debt_ebitda": "Debt / EBITDA",
    "debt_assets": "Debt / assets",
    "equity_ratio": "Equity ratio",
    "interest_coverage": "Interest coverage",
    "cfo_to_debt": "CFO / debt",
    "fcf_to_debt": "FCF / debt",
    "receivable_days": "Receivable days",
    "payable_days": "Payable days",
    "inventory_days": "Inventory days",
    "asset_turnover": "Asset turnover",
}

_PCT_METRICS = {
    "gross_margin", "ebitda_margin", "ebit_margin", "pat_margin",
    "return_on_assets", "return_on_equity", "debt_assets", "equity_ratio",
    "cfo_to_debt", "fcf_to_debt",
}
_DAYS_METRICS = {"receivable_days", "payable_days", "inventory_days"}
_MULTIPLE_METRICS = {
    "current_ratio", "quick_ratio", "cash_ratio",
    "debt_equity", "debt_ebitda", "interest_coverage",
    "asset_turnover",
}

# Policy threshold lookup: which config key + direction ("min" means
# breach below; "max" means breach above).
_POLICY_HINTS: Dict[str, Tuple[str, str]] = {
    "interest_coverage": ("interest_coverage_min", "min"),
    "debt_equity":       ("debt_equity_max",       "max"),
    "debt_ebitda":       ("debt_ebitda_max",       "max"),
    "current_ratio":     ("current_ratio_min",     "min"),
    "quick_ratio":       ("quick_ratio_min",       "min"),
}


def _fy_year(fy: str) -> int:
    digits = "".join(ch for ch in str(fy) if ch.isdigit())
    return int(digits[-4:]) if len(digits) >= 4 else 0


def _sorted_fys(fys: List[str]) -> List[str]:
    return sorted([fy for fy in fys if fy], key=_fy_year)


def _resolve_fys(
    analytics_fys: List[str],
    requested: Optional[List[str]],
) -> List[str]:
    """Intersect requested FYs with what's available; fall back to all."""
    available = _sorted_fys(analytics_fys)
    if not requested:
        return available
    req_set = set(requested)
    return [fy for fy in available if fy in req_set] or available


def _format_for(metric: str) -> str:
    if metric in _PCT_METRICS:
        return ".1%"
    if metric in _DAYS_METRICS:
        return ".0f"
    if metric in _MULTIPLE_METRICS:
        return ".2f"
    return ",.0f"


def _unit_for(metric: str) -> str:
    if metric in _PCT_METRICS:
        return "%"
    if metric in _DAYS_METRICS:
        return " days"
    if metric in _MULTIPLE_METRICS:
        return "x"
    return ""


def _is_ratio(metric: str) -> bool:
    return metric in _PCT_METRICS or metric in _DAYS_METRICS or metric in _MULTIPLE_METRICS


def _metric_value(by_fy: Dict[str, Dict[str, Any]], fy: str, metric: str) -> Optional[float]:
    bucket = by_fy.get(fy) or {}
    raw = bucket.get("raw") or {}
    ratios = bucket.get("ratios") or {}
    if metric in raw:
        v = raw.get(metric)
    elif metric in ratios:
        v = ratios.get(metric)
    else:
        v = None
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _gather_series(
    case_id: str, metric: str, fys: Optional[List[str]] = None,
) -> Optional[Tuple[List[str], List[Optional[float]], Dict[str, Any], str]]:
    """
    Returns (fys, values, fs_data, label) or None if no analytics on disk.
    `metric` is matched against canonical raw/ratio keys; not validated.
    """
    fs_data = _store.load_features(case_id, "fs_analytics") or {}
    if not fs_data:
        return None
    target_fys = _resolve_fys(fs_data.get("fys") or [], fys)
    by_fy = fs_data.get("by_fy") or {}
    label = _METRIC_LABELS.get(metric, metric)
    values = [_metric_value(by_fy, fy, metric) for fy in target_fys]
    return target_fys, values, fs_data, label


def _series_summary(
    fys: List[str], values: List[Optional[float]], metric: str, label: str,
) -> str:
    paired = [(fy, v) for fy, v in zip(fys, values) if v is not None]
    if not paired:
        return f"No {label} values available for the requested FYs."
    fmt = _format_for(metric)
    if len(paired) == 1:
        fy, v = paired[0]
        return f"{label} in {fy}: {_format_value(v, fmt)}{_unit_for(metric)}"
    first_fy, first_v = paired[0]
    last_fy, last_v = paired[-1]
    delta_pct = (last_v - first_v) / abs(first_v) * 100 if first_v else None
    pieces = [
        f"{label} moved from {_format_value(first_v, fmt)}{_unit_for(metric)} "
        f"in {first_fy} to {_format_value(last_v, fmt)}{_unit_for(metric)} in {last_fy}",
    ]
    if delta_pct is not None:
        sign = "+" if delta_pct >= 0 else ""
        pieces.append(f"({sign}{delta_pct:.1f}% over the period)")
    return " ".join(pieces) + "."


def _format_value(v: float, fmt: str) -> str:
    """Apply a printf-style format; '%' format converts the decimal to percent."""
    if fmt == ".1%":
        return f"{v * 100:.1f}%"
    if fmt == ",.0f":
        return f"{v:,.0f}"
    if fmt == ".2f":
        return f"{v:.2f}"
    if fmt == ".0f":
        return f"{v:.0f}"
    return str(v)


def _vega_skeleton(title: str, height: int = _CHART_HEIGHT) -> Dict[str, Any]:
    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "title": {"text": title, "fontSize": 13, "fontWeight": "bold", "color": "#1f2937"},
        "width": "container",
        "height": height,
        "background": "transparent",
        "config": {
            "axis": {"labelFontSize": 10, "titleFontSize": 11, "titleColor": "#475569"},
            "view": {"stroke": "transparent"},
        },
    }


# ---- plot_metric ----------------------------------------------------------

PLOT_METRIC_SPEC: Dict[str, Any] = {
    "name": "plot_metric",
    "description": (
        "Render an inline chart of a single financial metric across FYs — "
        "use when the analyst says 'plot X', 'show me the X trend', "
        "'chart Y over time', or any visual ask. Works on raw line items "
        "(revenue, ebitda, pat, cash, total_debt, etc.) or ratios "
        "(ebitda_margin, current_ratio, etc.). Returns a Vega-Lite spec "
        "rendered inline plus a one-sentence text summary."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "metric": {
                "type": "string",
                "description": (
                    "Canonical metric key. Raw items: revenue, gross_profit, "
                    "ebitda, ebit, pat, cash, total_assets, total_equity, "
                    "total_debt, cfo, fcf, current_assets, current_liabilities, "
                    "trade_receivables, trade_payables, inventory. Ratios: "
                    "ebitda_margin, current_ratio, debt_equity, interest_coverage, "
                    "receivable_days, return_on_equity, etc."
                ),
            },
            "fys": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional FY filter, e.g. ['FY2023', 'FY2024']. Defaults to all available.",
            },
            "kind": {
                "type": "string",
                "enum": ["line", "bar"],
                "description": "Chart mark. Defaults to 'line' for ratios / 'bar' for raw amounts.",
            },
        },
        "required": ["metric"],
    },
}


def plot_metric(
    case_id: str,
    metric: str,
    fys: Optional[List[str]] = None,
    kind: Optional[str] = None,
) -> Dict[str, Any]:
    metric = (metric or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not metric:
        return {"is_error": True, "error": "plot_metric requires 'metric'."}

    gathered = _gather_series(case_id, metric, fys=fys)
    if gathered is None:
        return {
            "result": {"available": False,
                       "reason": "No FS analytics on disk yet. Run /analyze first."},
            "citations": [],
        }
    target_fys, values, fs_data, label = gathered
    if all(v is None for v in values):
        sample_raw = list((fs_data.get("by_fy", {}).get(target_fys[0], {}).get("raw") or {}).keys())[:10] if target_fys else []
        sample_ratios = list((fs_data.get("by_fy", {}).get(target_fys[0], {}).get("ratios") or {}).keys())[:10] if target_fys else []
        return {
            "result": {
                "available": False,
                "reason": f"Metric '{metric}' not present in fs_analytics for any FY.",
                "available_raw_sample": sample_raw,
                "available_ratios_sample": sample_ratios,
            },
            "citations": [],
        }

    mark_kind = (kind or "").lower() or ("line" if _is_ratio(metric) else "bar")
    if mark_kind not in ("line", "bar"):
        mark_kind = "line"

    fmt = _format_for(metric)
    unit = _unit_for(metric)
    title = f"{label}" + (f" ({unit})" if unit and unit != "%" else "")

    data_values = [
        {"fy": fy, "value": v} for fy, v in zip(target_fys, values) if v is not None
    ]
    spec = _vega_skeleton(title)
    spec["data"] = {"values": data_values}
    spec["mark"] = (
        {"type": "line", "point": {"size": 60, "filled": True}, "color": _BRAND_NAVY,
         "strokeWidth": 2}
        if mark_kind == "line"
        else {"type": "bar", "color": _BRAND_NAVY, "cornerRadiusTopLeft": 3,
              "cornerRadiusTopRight": 3}
    )
    spec["encoding"] = {
        "x": {"field": "fy", "type": "ordinal", "title": "Financial year",
              "axis": {"labelAngle": 0}},
        "y": {"field": "value", "type": "quantitative", "title": label,
              "axis": {"format": fmt}},
        "tooltip": [
            {"field": "fy", "title": "FY"},
            {"field": "value", "title": label, "format": fmt},
        ],
    }

    summary = _series_summary(target_fys, values, metric, label)
    return {
        "result": {
            "available": True,
            "chart_type": mark_kind,
            "title": title,
            "metric": metric,
            "metric_label": label,
            "fys": target_fys,
            "values": values,
            "vega_spec": spec,
            "summary": summary,
        },
        "citations": [{
            "kind": "chart",
            "path": f"cases/{case_id}/features/fs_analytics.json",
            "chart_type": mark_kind,
            "metric": metric,
        }],
    }


# ---- plot_ratio_vs_policy -------------------------------------------------

PLOT_RATIO_VS_POLICY_SPEC: Dict[str, Any] = {
    "name": "plot_ratio_vs_policy",
    "description": (
        "Render a ratio's trend against the policy threshold — "
        "line of the ratio across FYs plus a horizontal rule at the policy "
        "boundary, with the breach zone shaded. Use when the analyst wants "
        "to see how close a metric is to a policy threshold "
        "('chart IC vs policy', 'plot D/E with the policy band'). "
        "Supported ratios: interest_coverage, debt_equity, debt_ebitda, "
        "current_ratio, quick_ratio. For ratios without a configured "
        "policy threshold, falls back to plot_metric behaviour."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "metric": {
                "type": "string",
                "description": "Ratio key — see description for the supported set.",
            },
        },
        "required": ["metric"],
    },
}


def plot_ratio_vs_policy(case_id: str, metric: str) -> Dict[str, Any]:
    metric = (metric or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not metric:
        return {"is_error": True, "error": "plot_ratio_vs_policy requires 'metric'."}

    gathered = _gather_series(case_id, metric)
    if gathered is None:
        return {
            "result": {"available": False,
                       "reason": "No FS analytics on disk yet. Run /analyze first."},
            "citations": [],
        }
    target_fys, values, fs_data, label = gathered
    if all(v is None for v in values):
        return {
            "result": {
                "available": False,
                "reason": f"Ratio '{metric}' not present in fs_analytics for any FY.",
            },
            "citations": [],
        }

    policy_key, direction = _POLICY_HINTS.get(metric, (None, None))
    threshold: Optional[float] = None
    if policy_key:
        try:
            v = _config.get(f"portfolio_norms.{policy_key}")
            threshold = float(v) if v is not None else None
        except (TypeError, ValueError):
            threshold = None

    fmt = _format_for(metric)
    unit = _unit_for(metric)
    title = f"{label} vs policy" if threshold is not None else label

    data_values = [
        {"fy": fy, "value": v} for fy, v in zip(target_fys, values) if v is not None
    ]
    spec = _vega_skeleton(title, height=_CHART_HEIGHT_TALL)
    spec["data"] = {"values": data_values}

    # Compose layers: shaded breach band + ratio line + threshold rule.
    layers: List[Dict[str, Any]] = []

    if threshold is not None and data_values:
        ys = [d["value"] for d in data_values]
        y_min = min(ys + [threshold])
        y_max = max(ys + [threshold])
        # Modest padding so the band reaches the chart edges.
        pad = max((y_max - y_min) * 0.15, 0.001)
        if direction == "min":
            # Breach below threshold — shade from y_axis_min to threshold.
            band_bottom = min(y_min - pad, 0 if min(ys) >= 0 else y_min - pad)
            layers.append({
                "data": {"values": [{"y1": band_bottom, "y2": threshold}]},
                "mark": {"type": "rect", "color": "#fee2e2", "opacity": 0.55},
                "encoding": {
                    "y": {"field": "y1", "type": "quantitative"},
                    "y2": {"field": "y2"},
                },
            })
        else:  # direction == "max" — breach above threshold
            band_top = y_max + pad
            layers.append({
                "data": {"values": [{"y1": threshold, "y2": band_top}]},
                "mark": {"type": "rect", "color": "#fee2e2", "opacity": 0.55},
                "encoding": {
                    "y": {"field": "y1", "type": "quantitative"},
                    "y2": {"field": "y2"},
                },
            })

    layers.append({
        "mark": {"type": "line", "point": {"size": 60, "filled": True},
                 "color": _BRAND_NAVY, "strokeWidth": 2},
        "encoding": {
            "x": {"field": "fy", "type": "ordinal", "title": "Financial year",
                  "axis": {"labelAngle": 0}},
            "y": {"field": "value", "type": "quantitative", "title": label,
                  "axis": {"format": fmt}},
            "tooltip": [
                {"field": "fy", "title": "FY"},
                {"field": "value", "title": label, "format": fmt},
            ],
        },
    })

    if threshold is not None:
        layers.append({
            "data": {"values": [{"threshold": threshold}]},
            "mark": {"type": "rule", "color": _POLICY_RED, "strokeDash": [5, 4], "strokeWidth": 1.5},
            "encoding": {
                "y": {"field": "threshold", "type": "quantitative"},
                "tooltip": [{"field": "threshold", "title": "Policy threshold", "format": fmt}],
            },
        })

    spec["layer"] = layers

    # Build a verdict summary.
    latest_fy = target_fys[-1] if target_fys else None
    latest_val = values[-1] if values else None
    summary_parts = [_series_summary(target_fys, values, metric, label)]
    if threshold is not None and latest_val is not None:
        breach = (
            latest_val < threshold if direction == "min"
            else latest_val > threshold
        )
        verdict = "BREACH" if breach else "within policy"
        summary_parts.append(
            f"Latest {latest_fy} value {_format_value(latest_val, fmt)}{unit} vs "
            f"policy {_format_value(threshold, fmt)}{unit} — {verdict}."
        )

    return {
        "result": {
            "available": True,
            "chart_type": "ratio_vs_policy",
            "title": title,
            "metric": metric,
            "metric_label": label,
            "fys": target_fys,
            "values": values,
            "threshold": threshold,
            "policy_direction": direction,
            "vega_spec": spec,
            "summary": " ".join(summary_parts),
        },
        "citations": [{
            "kind": "chart",
            "path": f"cases/{case_id}/features/fs_analytics.json",
            "chart_type": "ratio_vs_policy",
            "metric": metric,
        }],
    }


# ---- compare_metrics ------------------------------------------------------

COMPARE_METRICS_SPEC: Dict[str, Any] = {
    "name": "compare_metrics",
    "description": (
        "Render a multi-series line chart comparing 2-5 metrics across FYs. "
        "Use when the analyst wants to see relationships ('plot revenue "
        "and EBITDA together', 'compare current and quick ratios'). All "
        "metrics must be of the same unit-family (all amounts, all ratios, "
        "all margins) — otherwise the y-axis is meaningless. If the units "
        "differ, fall back to issuing multiple plot_metric calls instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "metrics": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2-5 metric keys to overlay.",
                "minItems": 2,
                "maxItems": 5,
            },
            "fys": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional FY filter. Defaults to all available.",
            },
        },
        "required": ["metrics"],
    },
}


_SERIES_COLORS = [_BRAND_NAVY, _BRAND_LILAC, _SAFE_GREEN, "#f97316", "#0ea5e9"]


def compare_metrics(
    case_id: str,
    metrics: List[str],
    fys: Optional[List[str]] = None,
) -> Dict[str, Any]:
    if not metrics or not isinstance(metrics, list):
        return {"is_error": True, "error": "compare_metrics requires a 'metrics' list."}
    norm = [m.strip().lower().replace("-", "_").replace(" ", "_") for m in metrics if m]
    if len(norm) < 2:
        return {"is_error": True, "error": "Provide at least two metrics to compare."}
    if len(norm) > 5:
        return {"is_error": True, "error": "compare_metrics supports at most five metrics."}

    fs_data = _store.load_features(case_id, "fs_analytics") or {}
    if not fs_data:
        return {
            "result": {"available": False,
                       "reason": "No FS analytics on disk yet. Run /analyze first."},
            "citations": [],
        }
    target_fys = _resolve_fys(fs_data.get("fys") or [], fys)
    by_fy = fs_data.get("by_fy") or {}

    # Sanity check that everything is in the same unit family.
    families = set()
    for m in norm:
        if m in _PCT_METRICS:
            families.add("pct")
        elif m in _DAYS_METRICS:
            families.add("days")
        elif m in _MULTIPLE_METRICS:
            families.add("ratio")
        else:
            families.add("amount")
    if len(families) > 1:
        return {
            "result": {
                "available": False,
                "reason": (
                    "Metrics span multiple unit families "
                    f"({sorted(families)}) — y-axis would be meaningless. "
                    "Use separate plot_metric calls instead."
                ),
                "metrics_by_family": {f: [m for m in norm if _family_of(m) == f] for f in families},
            },
            "citations": [],
        }
    family = next(iter(families))

    flat: List[Dict[str, Any]] = []
    for m in norm:
        label = _METRIC_LABELS.get(m, m)
        for fy in target_fys:
            v = _metric_value(by_fy, fy, m)
            if v is not None:
                flat.append({"fy": fy, "metric": label, "value": v})

    if not flat:
        return {
            "result": {
                "available": False,
                "reason": "None of the requested metrics had values for the selected FYs.",
            },
            "citations": [],
        }

    sample_metric = norm[0]
    fmt = _format_for(sample_metric)
    unit = _unit_for(sample_metric)
    title = " vs ".join(_METRIC_LABELS.get(m, m) for m in norm)
    if family == "amount":
        title += " (amounts)"

    spec = _vega_skeleton(title)
    spec["data"] = {"values": flat}
    spec["mark"] = {"type": "line", "point": {"size": 50, "filled": True}, "strokeWidth": 2}
    spec["encoding"] = {
        "x": {"field": "fy", "type": "ordinal", "title": "Financial year",
              "axis": {"labelAngle": 0}},
        "y": {"field": "value", "type": "quantitative",
              "title": "Value" + (f" ({unit})" if unit and unit != "%" else ""),
              "axis": {"format": fmt}},
        "color": {
            "field": "metric", "type": "nominal",
            "scale": {"range": _SERIES_COLORS[: len(norm)]},
            "legend": {"orient": "bottom", "title": None},
        },
        "tooltip": [
            {"field": "metric", "title": "Metric"},
            {"field": "fy", "title": "FY"},
            {"field": "value", "title": "Value", "format": fmt},
        ],
    }

    # Per-metric one-liner.
    summary_lines = []
    for m in norm:
        label = _METRIC_LABELS.get(m, m)
        ms_values = [_metric_value(by_fy, fy, m) for fy in target_fys]
        summary_lines.append("· " + _series_summary(target_fys, ms_values, m, label))
    summary = "\n".join(summary_lines)

    return {
        "result": {
            "available": True,
            "chart_type": "comparison",
            "title": title,
            "metrics": norm,
            "fys": target_fys,
            "vega_spec": spec,
            "summary": summary,
        },
        "citations": [{
            "kind": "chart",
            "path": f"cases/{case_id}/features/fs_analytics.json",
            "chart_type": "comparison",
            "metrics": norm,
        }],
    }


def _family_of(metric: str) -> str:
    if metric in _PCT_METRICS: return "pct"
    if metric in _DAYS_METRICS: return "days"
    if metric in _MULTIPLE_METRICS: return "ratio"
    return "amount"
