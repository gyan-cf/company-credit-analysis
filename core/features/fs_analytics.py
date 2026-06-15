"""
Financial-statement analytics over SG canonical periods.

Input shape — produced by `core/ingestion/sg_pipeline.SGIngestionPipeline._merge_periods`:

    {
      "perimeter": "company" | "group",
      "fy": "FY2024",
      "period_end": "...",
      "currency": "SGD",
      "statements": {
        "sofp": [{"canonical_code": "bs_cash", "amount": 123, ...}, ...],
        "soci": [...],
        "socf": [...]
      }
    }

Output: per-FY ratios, YoY trends, and a `fs_agent_data` dict that the FS LLM
agent reads directly. Replaces the legacy `features/fs_ratios.compute_fs_ratios`
+ `core/data/data_transformer.transform_fs_to_json` pair for the SG flow.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


# ---- Canonical-code lookup helpers --------------------------------------------

def _amount_by_code(statement_lines: List[Dict[str, Any]], code: str) -> Optional[float]:
    """First numeric value in `statement_lines` whose canonical_code matches."""
    for line in statement_lines:
        if line.get("canonical_code") == code:
            amt = line.get("amount")
            if amt is not None:
                try:
                    return float(amt)
                except (TypeError, ValueError):
                    continue
    return None


def _first_amount(statement_lines: List[Dict[str, Any]], codes: Iterable[str]) -> Optional[float]:
    """First match across a priority-ordered list of codes."""
    for code in codes:
        v = _amount_by_code(statement_lines, code)
        if v is not None:
            return v
    return None


def _sum_amounts(statement_lines: List[Dict[str, Any]], codes: Iterable[str]) -> float:
    total = 0.0
    for code in codes:
        v = _amount_by_code(statement_lines, code)
        if v is not None:
            total += v
    return total


def _safe_div(a: Optional[float], b: Optional[float], default: Optional[float] = None) -> Optional[float]:
    if a is None or b is None:
        return default
    if b == 0:
        return default
    return a / b


def _days_ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    """Days metric (× 365) that propagates None instead of crashing."""
    base = _safe_div(numerator, denominator)
    return None if base is None else base * 365


# ---- Raw extraction from one period -------------------------------------------

def extract_raw_from_period(period: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """
    Pull the numbers we need for ratios from one canonical period.

    Returned keys mirror the legacy `{balance_sheet, pnl, cash_flow}` shape so
    downstream prompts and analytics stay readable.
    """
    statements = period.get("statements", {})
    soci = statements.get("soci", []) or []
    sofp = statements.get("sofp", []) or []
    socf = statements.get("socf", []) or []

    # P&L
    revenue = _amount_by_code(soci, "pl_revenue")
    cost_of_sales = _amount_by_code(soci, "pl_cost_of_sales")
    gross_profit = _amount_by_code(soci, "pl_gross_profit")
    if gross_profit is None and revenue is not None and cost_of_sales is not None:
        gross_profit = revenue - cost_of_sales
    other_income = _amount_by_code(soci, "pl_other_income")
    interest_exp = _amount_by_code(soci, "pl_finance_costs")
    interest_inc = _amount_by_code(soci, "pl_finance_income")
    depreciation = _amount_by_code(soci, "pl_depreciation")
    amortisation = _amount_by_code(soci, "pl_amortisation")
    tax = _amount_by_code(soci, "pl_tax")
    pbt = _amount_by_code(soci, "pl_pbt")
    pat = _amount_by_code(soci, "pl_pat")
    if pat is None and pbt is not None and tax is not None:
        pat = pbt - tax

    ebit: Optional[float] = None
    if pbt is not None:
        ebit = pbt + (interest_exp or 0.0) - (interest_inc or 0.0)
    ebitda: Optional[float] = None
    if ebit is not None:
        ebitda = ebit + (depreciation or 0.0) + (amortisation or 0.0)

    # Balance sheet
    cash = _amount_by_code(sofp, "bs_cash")
    inventory = _amount_by_code(sofp, "bs_inventory")
    trade_recv = _first_amount(sofp, ["bs_trade_recv", "bs_trade_other_recv"])
    trade_pay = _first_amount(sofp, ["bs_trade_pay", "bs_trade_other_pay"])
    current_assets = _amount_by_code(sofp, "bs_total_ca")
    current_liab = _amount_by_code(sofp, "bs_total_cl")
    total_assets = _amount_by_code(sofp, "bs_total_assets")
    total_equity = _amount_by_code(sofp, "bs_total_equity")
    net_assets = _amount_by_code(sofp, "bs_net_assets")
    short_debt = _amount_by_code(sofp, "bs_borrowings_st")
    long_debt = _amount_by_code(sofp, "bs_borrowings_lt")
    total_debt: Optional[float] = None
    if short_debt is not None or long_debt is not None:
        total_debt = (short_debt or 0.0) + (long_debt or 0.0)

    # Cash flow
    cfo = _amount_by_code(socf, "cf_operating")
    capex = _amount_by_code(socf, "cf_capex")
    interest_paid = _amount_by_code(socf, "cf_interest_paid")
    tax_paid = _amount_by_code(socf, "cf_tax_paid")

    return {
        # P&L
        "revenue": revenue,
        "cost_of_sales": cost_of_sales,
        "gross_profit": gross_profit,
        "other_income": other_income,
        "interest_expense": interest_exp,
        "interest_income": interest_inc,
        "depreciation": depreciation,
        "amortisation": amortisation,
        "ebit": ebit,
        "ebitda": ebitda,
        "pbt": pbt,
        "pat": pat,
        "tax": tax,
        # BS
        "cash": cash,
        "inventory": inventory,
        "trade_receivables": trade_recv,
        "trade_payables": trade_pay,
        "current_assets": current_assets,
        "current_liabilities": current_liab,
        "total_assets": total_assets,
        "total_equity": total_equity or net_assets,
        "short_term_debt": short_debt,
        "long_term_debt": long_debt,
        "total_debt": total_debt,
        # CF
        "cfo": cfo,
        "capex": capex,
        "interest_paid": interest_paid,
        "tax_paid": tax_paid,
    }


# ---- Ratio computation --------------------------------------------------------

def compute_ratios(raw: Dict[str, Optional[float]]) -> Dict[str, Optional[float]]:
    """Compute the credit-analyst ratio panel for one period."""
    revenue = raw.get("revenue")
    cost_of_sales = raw.get("cost_of_sales") or revenue
    gross_profit = raw.get("gross_profit")
    ebit = raw.get("ebit")
    ebitda = raw.get("ebitda")
    pat = raw.get("pat")
    interest = raw.get("interest_expense")

    current_assets = raw.get("current_assets")
    current_liab = raw.get("current_liabilities")
    inventory = raw.get("inventory")
    cash = raw.get("cash")
    total_assets = raw.get("total_assets")
    total_equity = raw.get("total_equity")
    total_debt = raw.get("total_debt")
    trade_recv = raw.get("trade_receivables")
    trade_pay = raw.get("trade_payables")

    cfo = raw.get("cfo")
    capex = abs(raw.get("capex")) if raw.get("capex") is not None else None

    quick_assets: Optional[float] = None
    if current_assets is not None:
        quick_assets = current_assets - (inventory or 0.0)

    fcf: Optional[float] = None
    if cfo is not None:
        fcf = cfo - (capex or 0.0)

    return {
        # Profitability
        "gross_margin":      _safe_div(gross_profit, revenue),
        "ebitda_margin":     _safe_div(ebitda, revenue),
        "ebit_margin":       _safe_div(ebit, revenue),
        "pat_margin":        _safe_div(pat, revenue),
        "return_on_assets":   _safe_div(pat, total_assets),
        "return_on_equity":   _safe_div(pat, total_equity),
        # Liquidity
        "current_ratio":     _safe_div(current_assets, current_liab),
        "quick_ratio":       _safe_div(quick_assets, current_liab),
        "cash_ratio":        _safe_div(cash, current_liab),
        # Leverage
        "debt_equity":       _safe_div(total_debt, total_equity),
        "debt_ebitda":       _safe_div(total_debt, ebitda),
        "debt_assets":       _safe_div(total_debt, total_assets),
        "equity_ratio":      _safe_div(total_equity, total_assets),
        # Coverage
        "interest_coverage": _safe_div(ebit, interest),
        # Working-capital days
        "receivable_days":   _days_ratio(trade_recv, revenue),
        "payable_days":      _days_ratio(trade_pay, cost_of_sales),
        "inventory_days":    _days_ratio(inventory, cost_of_sales),
        # Cash conversion
        "cfo_pat":           _safe_div(cfo, pat),
        "fcf":               fcf,
        "cash_to_assets":    _safe_div(cash, total_assets),
    }


# ---- Trends across FYs --------------------------------------------------------

def _fy_year(fy: str) -> int:
    if fy and len(fy) >= 6 and fy[2:].isdigit():
        return int(fy[2:])
    return 0


def compute_trends(by_fy: Dict[str, Dict[str, Any]]) -> Dict[str, Optional[float]]:
    """YoY percent change of ratios between the two latest FYs + growth rates."""
    fys = sorted(by_fy.keys(), key=_fy_year)
    if len(fys) < 2:
        return {}
    prev, curr = fys[-2], fys[-1]
    prev_r = by_fy[prev]["ratios"]
    curr_r = by_fy[curr]["ratios"]
    trends: Dict[str, Optional[float]] = {}
    for key in curr_r:
        p_val = prev_r.get(key)
        c_val = curr_r.get(key)
        if p_val in (None, 0) or c_val is None:
            trends[f"{key}_yoy_pct"] = None
        else:
            trends[f"{key}_yoy_pct"] = (c_val - p_val) / abs(p_val)
    for key in ("revenue", "ebitda", "pat", "total_assets", "total_equity"):
        p_val = prev_r.get(key) or by_fy[prev]["raw"].get(key)
        c_val = curr_r.get(key) or by_fy[curr]["raw"].get(key)
        if p_val and c_val is not None:
            trends[f"{key}_growth_yoy"] = (c_val - p_val) / abs(p_val)
    return trends


# ---- Top-level entry: canonical periods → fs_agent_data -----------------------

def build_fs_agent_data(
    periods: List[Dict[str, Any]],
    *,
    perimeter: str = "company",
    entity: Optional[Dict[str, Any]] = None,
    review_flags: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    From the merged canonical periods, produce the structured payload the FS
    LLM agent reads. Filters to a single perimeter (default company) so the
    agent reasons about one consistent entity scope.
    """
    selected = [p for p in periods if p.get("perimeter") == perimeter]
    if not selected:
        selected = list(periods)
        perimeter = selected[0].get("perimeter", perimeter) if selected else perimeter

    by_fy: Dict[str, Dict[str, Any]] = {}
    for p in selected:
        fy = p.get("fy", "")
        raw = extract_raw_from_period(p)
        by_fy[fy] = {
            "fy": fy,
            "period_end": p.get("period_end"),
            "currency": p.get("currency", "SGD"),
            "raw": raw,
            "ratios": compute_ratios(raw),
        }

    fys = sorted(by_fy.keys(), key=_fy_year, reverse=True)
    latest_ratios = by_fy[fys[0]]["ratios"] if fys else {}
    trends = compute_trends(by_fy)

    return {
        "entity": entity or {},
        "perimeter": perimeter,
        "fys": fys,
        "summary_ratios": latest_ratios,
        "by_fy": by_fy,
        "trends": trends,
        "review_flags": review_flags or [],
    }


def periods_from_merged_statements(
    financials_dir: Path,
    *,
    perimeter: str = "company",
    fallback_periods: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Build canonical period objects from parsed/financials/merged/*.json.

    The report and UI-reviewed financials operate on the merged statement
    sidecars. These can be more complete than the initial ingestion periods,
    especially when one FY comes from OCR/agentic extraction. Reconstructing
    periods from merged sidecars keeps downstream analytics aligned with the
    reviewed statement view.
    """
    merged_dir = Path(financials_dir) / "merged"
    if not merged_dir.exists():
        return []

    fallback_by_key = {
        (p.get("perimeter"), p.get("fy")): p
        for p in (fallback_periods or [])
        if p.get("perimeter") and p.get("fy")
    }
    periods: Dict[str, Dict[str, Any]] = {}

    for statement in ("soci", "sofp", "socf"):
        path = merged_dir / f"{statement}__{perimeter}.json"
        if not path.exists():
            continue
        try:
            block = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue

        fys = block.get("fys") or []
        if not fys:
            keys = set()
            for row in block.get("rows", []) or []:
                keys.update((row.get("values") or {}).keys())
            fys = sorted(keys)

        for fy in fys:
            fallback = fallback_by_key.get((perimeter, fy), {})
            period = periods.setdefault(
                fy,
                {
                    "perimeter": perimeter,
                    "fy": fy,
                    "period_end": fallback.get("period_end"),
                    "currency": fallback.get("currency") or block.get("currency") or "SGD",
                    "statements": {"soci": [], "sofp": [], "socf": []},
                },
            )
            if not period.get("period_end") and fallback.get("period_end"):
                period["period_end"] = fallback.get("period_end")
            if not period.get("currency") and fallback.get("currency"):
                period["currency"] = fallback.get("currency")

        for row in block.get("rows", []) or []:
            code = row.get("canonical_code")
            if not code:
                continue
            values = row.get("values") or {}
            provenance = row.get("provenance") or {}
            for fy in fys:
                amount = values.get(fy)
                if amount is None:
                    continue
                period = periods.get(fy)
                if not period:
                    continue
                prov = provenance.get(fy) or {}
                period["statements"][statement].append({
                    "canonical_code": code,
                    "amount": amount,
                    "label": row.get("label") or row.get("raw_label"),
                    "source_id": prov.get("source_id"),
                    "page": prov.get("page"),
                    "confidence": prov.get("confidence"),
                })

    return sorted(periods.values(), key=lambda p: _fy_year(p.get("fy", "")), reverse=True)


def build_fs_agent_data_from_merged(
    financials_dir: Path,
    *,
    perimeter: str = "company",
    entity: Optional[Dict[str, Any]] = None,
    review_flags: Optional[List[Dict[str, Any]]] = None,
    fallback_periods: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build analytics directly from merged statement sidecars when available."""
    periods = periods_from_merged_statements(
        financials_dir,
        perimeter=perimeter,
        fallback_periods=fallback_periods,
    )
    if not periods:
        return {}
    return build_fs_agent_data(
        periods,
        perimeter=perimeter,
        entity=entity,
        review_flags=review_flags,
    )
