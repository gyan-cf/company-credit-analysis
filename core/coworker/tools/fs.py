"""
FS / analytics tools for the co-worker agent.

These read the same `features/fs_analytics.json` the offline pipeline writes,
so the agent sees exactly what the analyst sees on the Financials page.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from config.config import get_config
from core.cases.case_store import CaseStore


_store = CaseStore()
_config = get_config()


_STATEMENT_LABELS = {
    "sofp": "Statement of Financial Position",
    "soci": "Statement of Comprehensive Income",
    "socf": "Statement of Cash Flows",
}


# ---- get_financial_summary ----------------------------------------------------

GET_FINANCIAL_SUMMARY_SPEC: Dict[str, Any] = {
    "name": "get_financial_summary",
    "description": (
        "Return the headline FS snapshot for this case: available FYs, latest-FY "
        "raw figures (revenue, EBITDA, PAT, total debt, equity, CFO), and the "
        "summary ratio block. Use this first when the analyst asks an open "
        "question like 'how is the company doing' before drilling into a "
        "specific ratio. Reads features/fs_analytics.json."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "perimeter": {
                "type": "string",
                "enum": ["company", "group"],
                "description": "Reporting perimeter. Defaults to company.",
            },
        },
    },
}


def _fy_year_key(fy: str) -> int:
    digits = "".join(ch for ch in str(fy) if ch.isdigit())
    return int(digits[-4:]) if len(digits) >= 4 else 0


def get_financial_summary(case_id: str, perimeter: str = "company") -> Dict[str, Any]:
    fs_data = _store.load_features(case_id, "fs_analytics") or {}
    if not fs_data:
        return {
            "result": {
                "available": False,
                "reason": "No FS analytics for this case yet. Analyst must run /analyze.",
            },
            "citations": [],
        }

    fys: List[str] = fs_data.get("fys") or []
    by_fy: Dict[str, Any] = fs_data.get("by_fy") or {}
    summary_ratios: Dict[str, Any] = fs_data.get("summary_ratios") or {}
    entity = fs_data.get("entity") or {}

    latest_fy = sorted(fys, key=_fy_year_key)[-1] if fys else None
    latest = by_fy.get(latest_fy, {}) if latest_fy else {}
    raw = latest.get("raw") or {}
    ratios = latest.get("ratios") or {}

    headline_raw_keys = (
        "revenue", "gross_profit", "ebitda", "pat",
        "total_assets", "total_equity", "total_debt", "cfo",
    )
    headline_ratio_keys = (
        "gross_margin", "ebitda_margin", "pat_margin",
        "current_ratio", "quick_ratio",
        "debt_equity", "debt_ebitda", "interest_coverage",
    )

    result = {
        "available": True,
        "entity": {
            "name": entity.get("name"),
            "uen": entity.get("uen"),
            "framework": entity.get("framework"),
            "audited": entity.get("audited"),
            "consolidated": entity.get("consolidated"),
        },
        "perimeter": fs_data.get("perimeter", perimeter),
        "currency": (by_fy.get(latest_fy, {}) or {}).get("currency") if latest_fy else None,
        "fys": fys,
        "latest_fy": latest_fy,
        "latest_raw": {k: raw.get(k) for k in headline_raw_keys},
        "latest_ratios": {k: ratios.get(k) for k in headline_ratio_keys},
        "summary_ratios": summary_ratios,
        "review_flag_count": len(fs_data.get("review_flags") or []),
    }

    citations: List[Dict[str, Any]] = [{
        "kind": "fs_analytics",
        "path": f"cases/{case_id}/features/fs_analytics.json",
        "perimeter": result["perimeter"],
        "latest_fy": latest_fy,
    }]
    return {"result": result, "citations": citations}


# ---- get_ratio ----------------------------------------------------------------

GET_RATIO_SPEC: Dict[str, Any] = {
    "name": "get_ratio",
    "description": (
        "Return a single named ratio for this case across all FYs, plus the "
        "policy threshold from config if one exists, plus the YoY trend. Use "
        "when the analyst asks about a specific metric (interest coverage, "
        "debt/EBITDA, current ratio, etc.). Returns null values for FYs where "
        "the ratio could not be computed (typically missing inputs)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Canonical ratio key, e.g. interest_coverage, debt_equity, "
                    "debt_ebitda, current_ratio, quick_ratio, ebitda_margin, "
                    "pat_margin, gross_margin, receivable_days, payable_days."
                ),
            },
        },
        "required": ["name"],
    },
}

# Map of ratio key -> (policy threshold key in config.portfolio_norms, comparison direction)
# direction: "min" → flag if value < threshold; "max" → flag if value > threshold
_POLICY_HINTS = {
    "interest_coverage": ("interest_coverage_min", "min"),
    "debt_equity":       ("debt_equity_max",       "max"),
    "debt_ebitda":       ("debt_ebitda_max",       "max"),
    "current_ratio":     ("current_ratio_min",     "min"),
    "quick_ratio":       ("quick_ratio_min",       "min"),
}


def get_ratio(case_id: str, name: str) -> Dict[str, Any]:
    fs_data = _store.load_features(case_id, "fs_analytics") or {}
    if not fs_data:
        return {
            "result": {"available": False, "reason": "No FS analytics for this case yet."},
            "citations": [],
        }

    name = (name or "").strip().lower().replace("-", "_").replace(" ", "_")
    fys: List[str] = fs_data.get("fys") or []
    by_fy: Dict[str, Any] = fs_data.get("by_fy") or {}
    trends: Dict[str, Any] = fs_data.get("trends") or {}

    series = [
        {"fy": fy, "value": (by_fy.get(fy, {}).get("ratios") or {}).get(name)}
        for fy in sorted(fys, key=_fy_year_key)
    ]
    have_any = any(s["value"] is not None for s in series)
    if not have_any:
        sample = sorted({k for fy in fys for k in (by_fy.get(fy, {}).get("ratios") or {}).keys()})
        return {
            "result": {
                "available": False,
                "name": name,
                "reason": f"Ratio '{name}' not present in fs_analytics for any FY.",
                "available_ratios": sample[:25],
            },
            "citations": [],
        }

    policy_key, direction = _POLICY_HINTS.get(name, (None, None))
    threshold: Optional[float] = None
    if policy_key:
        threshold = _config.get(f"portfolio_norms.{policy_key}")

    latest = series[-1]
    breaches_policy: Optional[bool] = None
    if threshold is not None and latest["value"] is not None:
        breaches_policy = (
            latest["value"] < threshold if direction == "min"
            else latest["value"] > threshold
        )

    result = {
        "available": True,
        "name": name,
        "series": series,
        "latest": latest,
        "yoy_pct": trends.get(f"{name}_yoy_pct"),
        "policy_threshold": threshold,
        "policy_direction": direction,
        "breaches_policy": breaches_policy,
    }
    citations = [{
        "kind": "ratio",
        "path": f"cases/{case_id}/features/fs_analytics.json",
        "ratio": name,
        "fys": [s["fy"] for s in series],
    }]
    return {"result": result, "citations": citations}


# ---- get_statement -----------------------------------------------------------

GET_STATEMENT_SPEC: Dict[str, Any] = {
    "name": "get_statement",
    "description": (
        "Return rows from a merged financial statement (cross-source spread) — "
        "the same data the analyst sees on the Financials page. Use when the "
        "analyst asks about a specific line item (e.g. 'how have receivables "
        "trended', 'show me the cash position'), section (e.g. 'all current "
        "liabilities'), or wants to scan a statement. Filtering keeps the "
        "response compact — prefer it over fetching the whole statement when "
        "you know what you're looking for."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "statement": {
                "type": "string",
                "enum": ["sofp", "soci", "socf"],
                "description": (
                    "sofp = balance sheet, soci = P&L / OCI, socf = cash flow."
                ),
            },
            "perimeter": {
                "type": "string",
                "enum": ["company", "group"],
                "description": "Reporting perimeter. Defaults to company.",
            },
            "canonical_code": {
                "type": "string",
                "description": (
                    "Optional exact canonical_code filter, e.g. bs_cash, "
                    "bs_trade_other_recv, pl_revenue. Returns just that row "
                    "with full FY values."
                ),
            },
            "label_contains": {
                "type": "string",
                "description": (
                    "Optional case-insensitive substring filter on the row "
                    "label. Use when you don't know the canonical_code."
                ),
            },
            "section_contains": {
                "type": "string",
                "description": (
                    "Optional case-insensitive substring filter on the row's "
                    "section_path (e.g. 'current liabilities')."
                ),
            },
            "max_rows": {
                "type": "integer",
                "description": "Cap on rows returned. Defaults 60, max 200.",
                "minimum": 1,
                "maximum": 200,
            },
        },
        "required": ["statement"],
    },
}


def get_statement(
    case_id: str,
    statement: str,
    perimeter: str = "company",
    canonical_code: Optional[str] = None,
    label_contains: Optional[str] = None,
    section_contains: Optional[str] = None,
    max_rows: int = 60,
) -> Dict[str, Any]:
    statement = (statement or "").lower()
    perimeter = (perimeter or "company").lower()
    if statement not in _STATEMENT_LABELS:
        return {
            "result": {
                "available": False,
                "reason": f"Unknown statement '{statement}'. Pick sofp, soci, or socf.",
            },
            "citations": [],
        }
    if perimeter not in ("company", "group"):
        return {
            "result": {
                "available": False,
                "reason": f"Unknown perimeter '{perimeter}'. Pick company or group.",
            },
            "citations": [],
        }

    rel_path = f"parsed/financials/merged/{statement}__{perimeter}.json"
    merged_path = _store._case_path(case_id) / "parsed" / "financials" / "merged" / f"{statement}__{perimeter}.json"
    if not merged_path.exists():
        return {
            "result": {
                "available": False,
                "reason": (
                    f"Merged {statement}/{perimeter} not on disk. The case "
                    "may not have been ingested yet, or this perimeter is "
                    "absent in the source FS."
                ),
                "expected_path": rel_path,
            },
            "citations": [],
        }

    try:
        block = json.loads(merged_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {
            "result": {"available": False, "reason": f"Corrupt merged block: {e}"},
            "citations": [],
        }

    rows: List[Dict[str, Any]] = block.get("rows") or []
    fys: List[str] = block.get("fys") or []

    code_filter = (canonical_code or "").strip().lower() or None
    label_filter = (label_contains or "").strip().lower() or None
    section_filter = (section_contains or "").strip().lower() or None

    def _matches(row: Dict[str, Any]) -> bool:
        if row.get("row_type") == "spacer":
            return False
        if code_filter and (row.get("canonical_code") or "").lower() != code_filter:
            return False
        if label_filter and label_filter not in (row.get("label") or "").lower():
            return False
        if section_filter:
            sp = " > ".join(row.get("section_path") or []).lower()
            if section_filter not in sp:
                return False
        return True

    filtered = [r for r in rows if _matches(r)]
    total_after_filter = len(filtered)
    max_rows = max(1, min(int(max_rows or 60), 200))
    truncated = total_after_filter > max_rows
    sliced = filtered[:max_rows]

    compact_rows = [
        {
            "label": r.get("label"),
            "canonical_code": r.get("canonical_code"),
            "row_type": r.get("row_type"),
            "section_path": r.get("section_path"),
            "indent_level": r.get("indent_level"),
            "values": r.get("values"),
            "note_ref": r.get("note_ref"),
        }
        for r in sliced
    ]

    result = {
        "available": True,
        "statement": statement,
        "statement_name": block.get("statement_name") or _STATEMENT_LABELS[statement],
        "perimeter": perimeter,
        "fys": fys,
        "row_count_total": len(rows),
        "row_count_returned": len(compact_rows),
        "truncated": truncated,
        "filters_applied": {
            "canonical_code": canonical_code,
            "label_contains": label_contains,
            "section_contains": section_contains,
        },
        "rows": compact_rows,
    }
    if not compact_rows and (code_filter or label_filter or section_filter):
        sample_labels = [r.get("label") for r in rows[:8] if r.get("label")]
        result["hint"] = (
            "Filter matched no rows. Sample labels in this statement: "
            + ", ".join(sample_labels[:6])
        )

    citations = [{
        "kind": "statement",
        "path": f"cases/{case_id}/{rel_path}",
        "statement": statement,
        "perimeter": perimeter,
        "fys": fys,
    }]
    return {"result": result, "citations": citations}
