"""
SFRS(I) canonical line-item taxonomy + label → code resolver.

This is intentionally compact and focused on the lines a credit analyst spreads.
Extend the SYNONYMS dictionary as new client filings reveal new labels.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# ---- Canonical codes per statement -------------------------------------------------

SOFP = {
    "bs_cash":             "Cash and cash equivalents",
    "bs_trade_recv":       "Trade receivables",
    "bs_other_recv":       "Other receivables",
    "bs_trade_other_recv": "Trade and other receivables",
    "bs_prepayments":      "Prepayments",
    "bs_inventory":        "Inventory",
    "bs_amt_due_related":  "Amount due from related parties",
    "bs_total_ca":         "Total current assets",
    "bs_ppe":              "Property, plant and equipment",
    "bs_rou_asset":        "Right-of-use asset",
    "bs_intangibles":      "Intangible assets",
    "bs_inv_subsidiary":   "Investment in subsidiary",
    "bs_inv_associate":    "Investment in associate",
    "bs_deferred_tax_a":   "Deferred tax assets",
    "bs_total_nca":        "Total non-current assets",
    "bs_total_assets":     "Total assets",

    "bs_trade_pay":        "Trade payables",
    "bs_other_pay":        "Other payables",
    "bs_trade_other_pay":  "Trade and other payables",
    "bs_borrowings_st":    "Borrowings (short-term)",
    "bs_lease_liab_st":    "Lease liabilities (current)",
    "bs_current_tax":      "Current tax payable",
    "bs_total_cl":         "Total current liabilities",
    "bs_borrowings_lt":    "Borrowings (long-term)",
    "bs_lease_liab_lt":    "Lease liabilities (non-current)",
    "bs_deferred_tax_l":   "Deferred tax liabilities",
    "bs_total_ncl":        "Total non-current liabilities",
    "bs_total_liab":       "Total liabilities",
    "bs_net_assets":       "Net assets",

    "bs_share_capital":    "Share capital",
    "bs_capital_reserve":  "Capital reserve",
    "bs_translation_res":  "Translation reserve",
    "bs_other_reserves":   "Other reserves",
    "bs_retained":         "Retained earnings",
    "bs_accum_losses":     "Accumulated losses",
    "bs_total_equity":     "Total equity",
}

SOCI = {
    "pl_revenue":          "Revenue",
    "pl_other_income":     "Other income",
    "pl_cost_of_sales":    "Cost of sales",
    "pl_gross_profit":     "Gross profit",
    "pl_employee_exp":     "Employee benefit expense",
    "pl_professional_fees":"Professional fees",
    "pl_service_fees":     "Service fees",
    "pl_rental_exp":       "Rental expense",
    "pl_depreciation":     "Depreciation",
    "pl_amortisation":     "Amortisation",
    "pl_other_op_exp":     "Other operating expenses",
    "pl_fx":               "Exchange gain / (loss)",
    "pl_finance_costs":    "Finance costs / Interest expense",
    "pl_finance_income":   "Finance income / Interest income",
    "pl_total_expenses":   "Total expenses",
    "pl_pbt":              "Profit / (Loss) before income tax",
    "pl_tax":              "Income tax expense / (credit)",
    "pl_pat":              "Profit / (Loss) for the year",
    "pl_oci":              "Other comprehensive income",
    "pl_tci":              "Total comprehensive income",
}

SOCF = {
    "cf_operating":        "Net cash from / (used in) operating activities",
    "cf_investing":        "Net cash from / (used in) investing activities",
    "cf_financing":        "Net cash from / (used in) financing activities",
    "cf_capex":            "Purchase of property, plant and equipment",
    "cf_proceeds_borrow":  "Proceeds from borrowings",
    "cf_repay_borrow":     "Repayment of borrowings",
    "cf_interest_paid":    "Interest paid",
    "cf_tax_paid":         "Income tax paid",
    "cf_dividends_paid":   "Dividends paid",
    "cf_net_change_cash":  "Net change in cash",
    "cf_cash_beg":         "Cash at beginning of period",
    "cf_cash_end":         "Cash at end of period",
}

# Combined view consumers can use
CANONICAL: Dict[str, Dict[str, str]] = {
    "sofp": SOFP,
    "soci": SOCI,
    "socf": SOCF,
}

STATEMENT_OF = {
    "sofp": "Statement of Financial Position",
    "soci": "Statement of Comprehensive Income",
    "socf": "Statement of Cash Flows",
}

# ---- Synonyms ----------------------------------------------------------------------
# Keys are canonical codes; values are lower-cased label fragments (substring match).

SYNONYMS: Dict[str, List[str]] = {
    # SoFP
    "bs_cash":             ["cash and cash equivalents", "cash and bank balances", "cash at bank"],
    "bs_trade_recv":       ["trade receivables", "trade debtors"],
    "bs_other_recv":       ["other receivables"],
    "bs_trade_other_recv": ["trade and other receivables"],
    "bs_prepayments":      ["prepayments", "prepaid"],
    "bs_inventory":        ["inventories", "inventory", "stocks"],
    "bs_amt_due_related":  ["amount due from", "amounts due from related", "due from holding", "due from related"],
    "bs_total_ca":         ["total current assets"],
    "bs_ppe":              ["property, plant and equipment", "plant and equipment", "fixed assets"],
    "bs_rou_asset":        ["right-of-use", "right of use asset"],
    "bs_intangibles":      ["intangible assets", "goodwill"],
    "bs_inv_subsidiary":   ["investment in subsidiary", "investments in subsidiaries"],
    "bs_inv_associate":    ["investment in associate", "investment in associates"],
    "bs_deferred_tax_a":   ["deferred tax assets"],
    "bs_total_nca":        ["total non-current assets", "total non current assets"],
    "bs_total_assets":     ["total assets"],
    "bs_trade_pay":        ["trade payables", "trade creditors"],
    "bs_other_pay":        ["other payables"],
    "bs_trade_other_pay":  ["trade and other payables"],
    "bs_borrowings_st":    ["short-term borrowings", "current borrowings", "bank borrowings"],
    "bs_lease_liab_st":    ["lease liabilities", "current lease"],
    "bs_current_tax":      ["current tax", "income tax payable"],
    "bs_total_cl":         ["total current liabilities"],
    "bs_borrowings_lt":    ["non-current borrowings", "long-term borrowings", "long term borrowings"],
    "bs_lease_liab_lt":    ["non-current lease", "lease liabilities (non"],
    "bs_deferred_tax_l":   ["deferred tax liabilities"],
    "bs_total_ncl":        ["total non-current liabilities", "total non current liabilities"],
    "bs_total_liab":       ["total liabilities"],
    "bs_net_assets":       ["net assets"],
    "bs_share_capital":    ["share capital"],
    "bs_capital_reserve":  ["capital reserve"],
    "bs_translation_res":  ["translation reserve", "foreign currency translation"],
    "bs_other_reserves":   ["other reserves", "reserves"],
    "bs_retained":         ["retained earnings", "retained profits"],
    "bs_accum_losses":     ["accumulated losses", "accumulated deficit"],
    "bs_total_equity":     ["total equity", "shareholders' equity", "shareholders equity"],

    # SoCI
    "pl_revenue":          ["revenue", "turnover"],
    "pl_other_income":     ["other income"],
    "pl_cost_of_sales":    ["cost of sales", "cost of goods sold", "cost of services"],
    "pl_gross_profit":     ["gross profit"],
    "pl_employee_exp":     ["employee benefit", "staff costs", "personnel expense", "salaries"],
    "pl_professional_fees":["professional fees"],
    "pl_service_fees":     ["service fees"],
    "pl_rental_exp":       ["rental expense", "rent expense", "rental"],
    "pl_depreciation":     ["depreciation"],
    "pl_amortisation":     ["amortisation", "amortization"],
    "pl_other_op_exp":     ["other operating expenses", "other expenses"],
    "pl_fx":               ["exchange", "foreign exchange"],
    "pl_finance_costs":    ["finance cost", "interest expense"],
    "pl_finance_income":   ["finance income", "interest income"],
    "pl_total_expenses":   ["total expenses"],
    "pl_pbt":              ["profit before income tax", "loss before income tax", "profit before tax", "loss before tax"],
    "pl_tax":              ["income tax expense", "income tax credit", "tax expense"],
    "pl_pat":              ["profit for the financial year", "loss for the financial year",
                            "profit for the year", "loss for the year"],
    "pl_oci":              ["other comprehensive income"],
    "pl_tci":              ["total comprehensive income"],

    # SoCF
    "cf_operating":        ["cash from operating", "cash used in operating", "operating activities"],
    "cf_investing":        ["cash from investing", "cash used in investing", "investing activities"],
    "cf_financing":        ["cash from financing", "cash used in financing", "financing activities"],
    "cf_capex":            ["purchase of property, plant", "purchase of ppe", "additions to ppe", "capital expenditure"],
    "cf_proceeds_borrow":  ["proceeds from borrowings", "drawdown of"],
    "cf_repay_borrow":     ["repayment of borrowings", "repayment of bank"],
    "cf_interest_paid":    ["interest paid"],
    "cf_tax_paid":         ["income tax paid", "tax paid"],
    "cf_dividends_paid":   ["dividends paid"],
    "cf_net_change_cash":  ["net change in cash", "net increase in cash", "net decrease in cash"],
    "cf_cash_beg":         ["cash at beginning", "cash and cash equivalents at beginning"],
    "cf_cash_end":         ["cash at end", "cash and cash equivalents at end"],
}


# ---- Resolver ---------------------------------------------------------------------

# Pre-compute lowercase synonym list for fast match
_FLAT: List[Tuple[str, str]] = []
for code, names in SYNONYMS.items():
    for n in names:
        _FLAT.append((code, n.lower()))
# Longest first → prefer "trade and other receivables" over "receivables"
_FLAT.sort(key=lambda x: -len(x[1]))


# Header / narrative phrases that should NEVER resolve to a canonical line item,
# even via fuzzy token overlap. These are common in statement banners.
_HEADER_PHRASES = (
    "for the financial year", "for the financial period", "for the year ended",
    "as at", "year ended", "period ended", "31 december", "31 january",
    "see accompanying notes", "notes to the financial statements",
)


def resolve_label(label: str) -> Tuple[Optional[str], float]:
    """Map a raw label to (canonical_code, confidence). Returns (None, 0.0) on miss."""
    if not label:
        return None, 0.0
    s = re.sub(r"\s+", " ", label).strip().lower()
    s = s.replace("’", "'").rstrip(":")
    # Reject statement-banner / header phrases outright
    for ph in _HEADER_PHRASES:
        if ph in s:
            return None, 0.0
    # exact label match (any synonym used wholesale)
    for code, syn in _FLAT:
        if s == syn:
            return code, 1.00
    # phrase contained
    for code, syn in _FLAT:
        if syn in s:
            return code, 0.85
    # token-overlap fallback for short labels — require ≥ 0.75 to avoid loose matches
    tokens = set(re.findall(r"[a-z]+", s))
    best, best_score = None, 0.0
    for code, syn in _FLAT:
        syn_tokens = set(re.findall(r"[a-z]+", syn))
        if not syn_tokens or len(syn_tokens) < 2:
            continue
        overlap = len(tokens & syn_tokens) / max(len(syn_tokens), 1)
        if overlap > best_score and overlap >= 0.75:
            best, best_score = code, overlap
    if best:
        return best, round(0.5 + 0.3 * best_score, 2)
    return None, 0.0


def statement_for(code: str) -> Optional[str]:
    if code in SOFP:
        return "sofp"
    if code in SOCI:
        return "soci"
    if code in SOCF:
        return "socf"
    return None
