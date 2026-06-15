"""
Contextual prompt suggestions for the co-worker rail.

Derives 3-5 actionable suggested questions from the current case state. The
goal is to nudge the analyst toward the next meaningful question without
showing the same hardcoded list on every page.

Inputs (all optional — handles missing artefacts gracefully):
    cases/<id>/manifest.json
    cases/<id>/assessment_summary.json
    cases/<id>/features/fs_analytics.json
    cases/<id>/agents/qualitative.json
    cases/<id>/reports/latest.json

Returns: list of dicts {label, message} ranked by relevance. `label` is the
short pill text; `message` is the full prompt sent to the agent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.cases.case_store import CaseStore


_DEFAULT_FALLBACK = [
    {"label": "Key risks",
     "message": "What are the most important credit risks from the financial statements?"},
    {"label": "Management probes",
     "message": "What questions should I ask management based on the latest analysis?"},
    {"label": "Liquidity view",
     "message": "Explain the liquidity position with source references."},
]


def build_suggestions(case_id: str, store: Optional[CaseStore] = None) -> List[Dict[str, str]]:
    """Return up to 5 case-aware suggestions, falling back to a generic set."""
    store = store or CaseStore()
    try:
        manifest = store.get_manifest(case_id)
    except FileNotFoundError:
        return list(_DEFAULT_FALLBACK)

    assessment = store.load_assessment_summary(case_id) or {}
    fs_analytics = store.load_features(case_id, "fs_analytics") or {}
    qualitative = store.load_agent_result(case_id, "qualitative") or {}
    report = _load_latest_report(store, case_id)

    suggestions: List[Dict[str, str]] = []
    seen_messages = set()

    def add(label: str, message: str) -> None:
        if message in seen_messages:
            return
        seen_messages.add(message)
        suggestions.append({"label": label, "message": message})

    cards = assessment.get("cards") or []
    cross_findings = assessment.get("cross_findings") or []
    has_analysis = bool(cards) or bool(fs_analytics)

    # ---- Case isn't analysed yet --------------------------------------------
    if not has_analysis:
        add("Run analysis",
            "What do I need to do to run the financial analysis on this case?")
        add("What's here",
            f"What documents and information do we have for {manifest.get('company_name', 'this borrower')}?")
        add("Next steps",
            "Walk me through the next steps to get this case to credit committee.")
        return suggestions or list(_DEFAULT_FALLBACK)

    # ---- High-severity findings drive the top suggestion --------------------
    high_risks = _count_risks(cards, severity="high")
    if high_risks > 0:
        add("Top risks",
            f"Walk me through the {high_risks} high-severity risk{'s' if high_risks != 1 else ''}.")

    # ---- Cross-source discrepancies ----------------------------------------
    if cross_findings:
        add("Cross-source gaps",
            f"Explain the {len(cross_findings)} cross-source finding{'s' if len(cross_findings) != 1 else ''}.")

    # ---- Liquidity / leverage shortcuts ------------------------------------
    latest_ratios = _latest_ratios(fs_analytics)
    if latest_ratios.get("interest_coverage") is not None and latest_ratios["interest_coverage"] < 1.5:
        add("Coverage shortfall",
            "Interest coverage looks weak. Explain the drivers and what would close the gap.")
    elif latest_ratios.get("current_ratio") is not None and latest_ratios["current_ratio"] < 1.0:
        add("Liquidity stress",
            "Current ratio is below 1. Walk through the liquidity position with source references.")
    elif fs_analytics:
        add("Liquidity view",
            "Explain the liquidity position with source references.")

    # ---- Management probes -------------------------------------------------
    probes = qualitative.get("probes") or []
    high_probes = [p for p in probes if (p.get("priority") or "").lower() == "high"]
    if high_probes:
        add("Top management probes",
            f"Show me the {len(high_probes)} high-priority probe question{'s' if len(high_probes) != 1 else ''} for management.")
    elif probes:
        add("Management probes",
            "What questions should I ask management based on the latest analysis?")

    # ---- Report-aware --------------------------------------------------------
    if report:
        add("Report summary",
            "Summarize what the credit report says about the borrower's overall risk profile.")
    elif cards:
        add("Draft report",
            "What sections would the credit report cover based on the current findings?")

    # ---- Always-useful catch-alls ------------------------------------------
    if len(suggestions) < 4:
        add("Compare FYs",
            "Compare the latest FY against the prior year — what changed and why?")
    if len(suggestions) < 5:
        add("Notable trends",
            "What are the most notable YoY trends in the financials?")

    return suggestions[:5] or list(_DEFAULT_FALLBACK)


def _load_latest_report(store: CaseStore, case_id: str) -> Optional[Dict[str, Any]]:
    path = store._case_path(case_id) / "reports" / "latest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _count_risks(cards: List[Dict[str, Any]], severity: str) -> int:
    sev = severity.lower()
    n = 0
    for card in cards:
        for risk in (card.get("risks") or []):
            if (risk.get("severity") or "").lower() == sev:
                n += 1
    return n


def _latest_ratios(fs_data: Dict[str, Any]) -> Dict[str, Any]:
    fys = fs_data.get("fys") or []
    if not fys:
        return {}
    # fys may be in either order; pick the highest-year entry deterministically.
    def _fy_year(fy: str) -> int:
        digits = "".join(ch for ch in str(fy) if ch.isdigit())
        return int(digits[-4:]) if len(digits) >= 4 else 0
    latest = sorted(fys, key=_fy_year)[-1]
    by_fy = fs_data.get("by_fy") or {}
    return (by_fy.get(latest) or {}).get("ratios") or {}
