"""
Credit-policy thresholds + cell-value parsing for colouring ratio tables
in the generated report HTML.

Mirrors the policy bands used by the Financials workspace
(`frontend/src/pages/Financials.tsx → RATIO_POLICY`) so a single Pass /
Watch / Risk colour scheme runs end-to-end across the workbench and the
generated report.
"""

from __future__ import annotations

import re
from typing import Literal, Optional, Tuple


PolicyStatus = Literal["pass", "watch", "risk"]
PolicyDir = Literal["above", "below"]


# Pass = stay above this threshold (for direction="above") or stay below
# this threshold (for direction="below"). Outside `watch` = risk.
RATIO_POLICY: dict[str, dict] = {
    "current_ratio":      {"pass": 1.0,  "watch": 0.7,  "direction": "above"},
    "quick_ratio":        {"pass": 0.8,  "watch": 0.5,  "direction": "above"},
    "cash_ratio":         {"pass": 0.3,  "watch": 0.1,  "direction": "above"},
    "debt_equity":        {"pass": 3.0,  "watch": 5.0,  "direction": "below"},
    "debt_ebitda":        {"pass": 4.0,  "watch": 6.0,  "direction": "below"},
    "interest_coverage":  {"pass": 1.5,  "watch": 1.0,  "direction": "above"},
    "ebitda_margin":      {"pass": 0.10, "watch": 0.05, "direction": "above"},
    "pat_margin":         {"pass": 0.05, "watch": 0.0,  "direction": "above"},
    "ebit_margin":        {"pass": 0.08, "watch": 0.03, "direction": "above"},
    "gross_margin":       {"pass": 0.20, "watch": 0.10, "direction": "above"},
    "return_on_equity":   {"pass": 0.10, "watch": 0.0,  "direction": "above"},
    "return_on_assets":   {"pass": 0.05, "watch": 0.0,  "direction": "above"},
    "cfo_to_debt":        {"pass": 0.20, "watch": 0.05, "direction": "above"},
    "fcf_to_debt":        {"pass": 0.10, "watch": 0.0,  "direction": "above"},
    "receivable_days":    {"pass": 60,   "watch": 90,   "direction": "below"},
    "payable_days":       {"pass": 90,   "watch": 120,  "direction": "below"},
    "inventory_days":     {"pass": 60,   "watch": 90,   "direction": "below"},
}


# Substring → ratio_key. First match wins. Order matters: longer / more
# specific phrases come first so "ebitda margin" doesn't accidentally
# match a row whose label is just "Margin".
_LABEL_HINTS: list[Tuple[str, str]] = [
    ("debt / ebitda",        "debt_ebitda"),
    ("debt/ebitda",          "debt_ebitda"),
    ("net debt / ebitda",    "debt_ebitda"),
    ("debt / equity",        "debt_equity"),
    ("debt/equity",          "debt_equity"),
    ("d/e",                  "debt_equity"),
    ("gearing",              "debt_equity"),
    ("interest cover",       "interest_coverage"),
    ("interest coverage",    "interest_coverage"),
    ("icr",                  "interest_coverage"),
    ("ebitda margin",        "ebitda_margin"),
    ("ebit margin",          "ebit_margin"),
    ("operating margin",     "ebit_margin"),
    ("pat margin",           "pat_margin"),
    ("net margin",           "pat_margin"),
    ("net profit margin",    "pat_margin"),
    ("gross margin",         "gross_margin"),
    ("return on equity",     "return_on_equity"),
    ("return on assets",     "return_on_assets"),
    ("roe",                  "return_on_equity"),
    ("roa",                  "return_on_assets"),
    ("current ratio",        "current_ratio"),
    ("quick ratio",          "quick_ratio"),
    ("cash ratio",           "cash_ratio"),
    ("receivable days",      "receivable_days"),
    ("debtor days",          "receivable_days"),
    ("days sales outstanding","receivable_days"),
    ("dso",                  "receivable_days"),
    ("payable days",         "payable_days"),
    ("creditor days",        "payable_days"),
    ("days payable outstanding","payable_days"),
    ("dpo",                  "payable_days"),
    ("inventory days",       "inventory_days"),
    ("days inventory outstanding","inventory_days"),
    ("dio",                  "inventory_days"),
    ("cfo / debt",           "cfo_to_debt"),
    ("cfo/debt",             "cfo_to_debt"),
    ("fcf / debt",           "fcf_to_debt"),
    ("fcf/debt",             "fcf_to_debt"),
]


def match_ratio_key(label: str) -> Optional[str]:
    """Return the canonical ratio key for a row label, or None if no match."""
    if not label:
        return None
    s = re.sub(r"\s+", " ", label).strip().lower()
    s = s.replace("÷", "/").replace("×", "x").replace("–", "-")
    for hint, key in _LABEL_HINTS:
        if hint in s:
            return key
    return None


# Parse cell text — handles forms like:
#   "1.44x" "0.62" "55%" "(3.36)" "− 3.36" "120 days" "12.5%" "n/a" "—"
_PARENS_RE = re.compile(r"^\(([^)]+)\)$")
_PERCENT_RE = re.compile(r"%$")
_DAYS_RE = re.compile(r"\bdays?\b", re.I)
_X_RE = re.compile(r"x$", re.I)
_NUMERIC_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


def parse_ratio_value(text: str) -> Optional[float]:
    """Best-effort numeric parser for ratio table cells."""
    if not text:
        return None
    s = text.strip()
    if s in {"—", "-", "n/a", "N/A", "na", "—%"}:
        return None
    s = s.replace("−", "-").replace(",", "")  # Unicode minus, thousands sep
    is_percent = bool(_PERCENT_RE.search(s))
    is_days = bool(_DAYS_RE.search(s))
    is_x = bool(_X_RE.search(s))
    is_neg = False
    m = _PARENS_RE.match(s)
    if m:
        is_neg = True
        s = m.group(1).strip()
    s = s.replace("%", "").replace("x", "").replace("X", "")
    s = _DAYS_RE.sub("", s).strip()
    m = _NUMERIC_RE.search(s)
    if not m:
        return None
    try:
        val = float(m.group(0))
    except ValueError:
        return None
    if is_neg:
        val = -val
    if is_percent and not is_days and not is_x:
        val = val / 100.0
    return val


def policy_status(ratio_key: str, value: Optional[float]) -> Optional[PolicyStatus]:
    """Map a ratio value to Pass / Watch / Risk, given the configured policy."""
    p = RATIO_POLICY.get(ratio_key)
    if p is None or value is None:
        return None
    if p["direction"] == "above":
        if value >= p["pass"]:
            return "pass"
        if value >= p["watch"]:
            return "watch"
        return "risk"
    # direction == "below"
    if value <= p["pass"]:
        return "pass"
    if value <= p["watch"]:
        return "watch"
    return "risk"


# Light cell tint per status — survives bleach allowlist + htmldocx export.
STATUS_BG: dict[PolicyStatus, str] = {
    "pass":  "#d1fae5",
    "watch": "#fef3c7",
    "risk":  "#fee2e2",
}
