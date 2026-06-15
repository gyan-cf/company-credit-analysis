"""
Assessment-finding tools for the co-worker agent.

`list_red_flags` aggregates risks across the FS / Industry / Qualitative
assessment cards (with optional severity filter).
`draft_probe_questions` returns the ranked probe questions the qualitative
agent surfaced for the management meeting.

Both read the artifacts the analysis pipeline persists under
`cases/<id>/agents/` and `cases/<id>/assessment_summary.json`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.cases.case_store import CaseStore


_store = CaseStore()


_SEVERITY_ORDER = {"high": 3, "medium": 2, "low": 1, "info": 0}


# ---- list_red_flags ----------------------------------------------------------

LIST_RED_FLAGS_SPEC: Dict[str, Any] = {
    "name": "list_red_flags",
    "description": (
        "Return the consolidated risks / watch-outs from the assessment cards "
        "(FS, Industry, Qualitative). Each entry includes the source card, "
        "severity, and the human-readable message. Use when the analyst asks "
        "'what are the red flags' / 'what should I worry about' / 'walk me "
        "through the risks'. Filter by severity if you want only the most "
        "serious items."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "severity": {
                "type": "string",
                "enum": ["high", "medium", "low", "any"],
                "description": (
                    "Minimum severity to include. 'high' returns only the "
                    "most serious findings; 'any' returns everything. "
                    "Defaults to 'any'."
                ),
            },
            "max_items": {
                "type": "integer",
                "description": "Cap on findings returned. Default 25, max 100.",
                "minimum": 1,
                "maximum": 100,
            },
        },
    },
}


def _severity_at_least(sev: str, threshold: str) -> bool:
    return _SEVERITY_ORDER.get(sev, 0) >= _SEVERITY_ORDER.get(threshold, 0)


def list_red_flags(
    case_id: str,
    severity: str = "any",
    max_items: int = 25,
) -> Dict[str, Any]:
    summary = _store.load_assessment_summary(case_id) or {}
    cards: List[Dict[str, Any]] = summary.get("cards") or []
    cross_findings: List[Dict[str, Any]] = summary.get("cross_findings") or []
    if not cards and not cross_findings:
        return {
            "result": {
                "available": False,
                "reason": (
                    "No assessment summary on disk yet. Analyst must run "
                    "/analyze first."
                ),
            },
            "citations": [],
        }

    severity = (severity or "any").lower()
    if severity not in ("high", "medium", "low", "any"):
        severity = "any"
    max_items = max(1, min(int(max_items or 25), 100))

    findings: List[Dict[str, Any]] = []

    for card in cards:
        card_type = card.get("card_type") or "UNKNOWN"
        for risk in (card.get("risks") or []):
            sev = (risk.get("severity") or "low").lower()
            if severity != "any" and not _severity_at_least(sev, severity):
                continue
            findings.append({
                "id": risk.get("id"),
                "severity": sev,
                "message": risk.get("message"),
                "source": card_type,
                "kind": "card_risk",
                "card_title": card.get("summary_title"),
            })

    for cf in cross_findings:
        sev = (cf.get("severity") or "low").lower()
        if severity != "any" and not _severity_at_least(sev, severity):
            continue
        findings.append({
            "id": cf.get("id"),
            "severity": sev,
            "message": cf.get("message"),
            "source": cf.get("source") or "CROSS",
            "kind": "cross_finding",
        })

    findings.sort(key=lambda f: _SEVERITY_ORDER.get(f.get("severity", "low"), 0), reverse=True)
    truncated = len(findings) > max_items
    findings = findings[:max_items]

    by_severity: Dict[str, int] = {}
    for f in findings:
        by_severity[f["severity"]] = by_severity.get(f["severity"], 0) + 1

    result = {
        "available": True,
        "severity_filter": severity,
        "count": len(findings),
        "truncated": truncated,
        "by_severity": by_severity,
        "findings": findings,
    }
    citations = [{
        "kind": "assessment",
        "path": f"cases/{case_id}/assessment_summary.json",
        "card_types": [c.get("card_type") for c in cards],
    }]
    return {"result": result, "citations": citations}


# ---- draft_probe_questions ---------------------------------------------------

DRAFT_PROBE_QUESTIONS_SPEC: Dict[str, Any] = {
    "name": "draft_probe_questions",
    "description": (
        "Return the qualitative-agent's probe questions for the management "
        "meeting, ranked by priority. Each probe has a question, theme, "
        "rationale, the documents to request, evidence links back to the "
        "underlying metrics, and a flag for whether it belongs in the "
        "management meeting. Use when the analyst asks 'what should I ask "
        "the CFO / sponsor / management', 'draft my probe list', or 'what "
        "documents should I request before committee'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "priority": {
                "type": "string",
                "enum": ["high", "medium", "low", "any"],
                "description": (
                    "Minimum priority to include. Defaults to 'any'."
                ),
            },
            "theme": {
                "type": "string",
                "description": (
                    "Optional theme filter (case-insensitive substring), e.g. "
                    "'revenue', 'leverage', 'related party', 'liquidity'."
                ),
            },
            "max_items": {
                "type": "integer",
                "description": "Cap on probes returned. Default 15, max 40.",
                "minimum": 1,
                "maximum": 40,
            },
        },
    },
}


_PRIORITY_ORDER = {"high": 3, "medium": 2, "low": 1}


def draft_probe_questions(
    case_id: str,
    priority: str = "any",
    theme: Optional[str] = None,
    max_items: int = 15,
) -> Dict[str, Any]:
    qual = _store.load_agent_result(case_id, "qualitative") or {}
    probes: List[Dict[str, Any]] = qual.get("probes") or []
    if not probes:
        # Fall back to a synthesized list off the assessment risks so the
        # tool stays useful even when the qualitative agent didn't produce
        # explicit probes (mock mode, or no LLM key at analyze time).
        return _synthesize_from_assessment(case_id, max_items)

    priority = (priority or "any").lower()
    if priority not in ("high", "medium", "low", "any"):
        priority = "any"
    theme_filter = (theme or "").strip().lower() or None
    max_items = max(1, min(int(max_items or 15), 40))

    selected: List[Dict[str, Any]] = []
    for p in probes:
        prio = (p.get("priority") or "medium").lower()
        if priority != "any" and _PRIORITY_ORDER.get(prio, 0) < _PRIORITY_ORDER.get(priority, 0):
            continue
        if theme_filter and theme_filter not in (p.get("theme") or "").lower():
            continue
        selected.append({
            "id": p.get("id"),
            "question": p.get("question"),
            "priority": prio,
            "theme": p.get("theme"),
            "rationale": p.get("rationale"),
            "documents_requested": p.get("documents_requested") or [],
            "evidence_links": p.get("evidence_links") or [],
            "management_meeting": bool(p.get("management_meeting")),
        })

    selected.sort(key=lambda p: _PRIORITY_ORDER.get(p.get("priority", "low"), 0), reverse=True)
    truncated = len(selected) > max_items
    selected = selected[:max_items]

    result = {
        "available": True,
        "source": "qualitative_agent",
        "priority_filter": priority,
        "theme_filter": theme,
        "count": len(selected),
        "truncated": truncated,
        "probes": selected,
    }
    citations = [{
        "kind": "probes",
        "path": f"cases/{case_id}/agents/qualitative.json",
        "agent": "qualitative",
    }]
    return {"result": result, "citations": citations}


def _synthesize_from_assessment(case_id: str, max_items: int) -> Dict[str, Any]:
    summary = _store.load_assessment_summary(case_id) or {}
    cards = summary.get("cards") or []
    if not cards:
        return {
            "result": {
                "available": False,
                "reason": (
                    "No qualitative probes and no assessment risks on disk. "
                    "Analyst must run /analyze first."
                ),
            },
            "citations": [],
        }

    derived: List[Dict[str, Any]] = []
    for card in cards:
        card_type = card.get("card_type") or "UNKNOWN"
        for risk in (card.get("risks") or []):
            sev = (risk.get("severity") or "low").lower()
            if sev not in ("high", "medium"):
                continue
            derived.append({
                "id": risk.get("id"),
                "question": f"Please explain: {risk.get('message', '').rstrip('.')}.",
                "priority": "high" if sev == "high" else "medium",
                "theme": card_type.lower(),
                "rationale": (
                    f"Synthesised from {card_type} card risk because the "
                    "qualitative agent did not produce explicit probes."
                ),
                "documents_requested": [],
                "evidence_links": [],
                "management_meeting": True,
            })
    derived = derived[:max(1, min(int(max_items or 15), 40))]
    return {
        "result": {
            "available": True,
            "source": "synthesized_from_assessment",
            "count": len(derived),
            "truncated": False,
            "probes": derived,
        },
        "citations": [{
            "kind": "assessment",
            "path": f"cases/{case_id}/assessment_summary.json",
            "agent": "synthesized",
        }],
    }
