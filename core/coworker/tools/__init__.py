"""
Tool registry for the co-worker agent.

Each tool module exposes:
    - a constant `SPEC: dict` (Anthropic tool-use JSON spec)
    - a function `call(case_id: str, **kwargs) -> dict`
      returning `{"result": ..., "citations": [...]}`

The dispatcher catches exceptions from tool calls and converts them to a
`{"is_error": True, "error": "..."}` payload so the model can recover. Tools
are intentionally narrow: one job each, deterministic where possible.
"""

from __future__ import annotations

import traceback
from typing import Any, Callable, Dict, List

from . import actions, charts, findings, fs, knowledge, report


# Order here is the order the model sees the tools in the prompt.
# Grouped roughly by intent: spread / ratio / statement → knowledge → report →
# assessment findings → write actions. Within each family, the cheapest /
# most general tool comes first so the model is biased toward it for open-
# ended questions. Write tools are last so they're not the model's first
# instinct on ambiguous asks.
_TOOLS: List[Dict[str, Any]] = [
    {"spec": fs.GET_FINANCIAL_SUMMARY_SPEC, "call": fs.get_financial_summary},
    {"spec": fs.GET_RATIO_SPEC,             "call": fs.get_ratio},
    {"spec": fs.GET_STATEMENT_SPEC,         "call": fs.get_statement},
    {"spec": knowledge.SEARCH_KNOWLEDGE_SPEC, "call": knowledge.search_knowledge},
    {"spec": knowledge.READ_NOTE_SPEC,       "call": knowledge.read_note},
    {"spec": report.LIST_REPORT_SECTIONS_SPEC, "call": report.list_report_sections},
    {"spec": report.GET_REPORT_SECTION_SPEC,   "call": report.get_report_section},
    {"spec": findings.LIST_RED_FLAGS_SPEC,        "call": findings.list_red_flags},
    {"spec": findings.DRAFT_PROBE_QUESTIONS_SPEC, "call": findings.draft_probe_questions},
    # Visual tools — return Vega-Lite specs rendered inline in the rail.
    {"spec": charts.PLOT_METRIC_SPEC,             "call": charts.plot_metric},
    {"spec": charts.PLOT_RATIO_VS_POLICY_SPEC,    "call": charts.plot_ratio_vs_policy},
    {"spec": charts.COMPARE_METRICS_SPEC,         "call": charts.compare_metrics},
    # Write-side tools — all create pending actions, never mutate inline.
    {"spec": actions.FLAG_FOR_COMMITTEE_SPEC,          "call": actions.flag_for_committee},
    {"spec": actions.ANNOTATE_FINDING_SPEC,            "call": actions.annotate_finding},
    {"spec": actions.REGENERATE_REPORT_SECTION_SPEC,   "call": actions.regenerate_report_section},
    {"spec": actions.OVERRIDE_EXTRACTED_VALUE_SPEC,    "call": actions.override_extracted_value},
    {"spec": actions.RERUN_ANALYSIS_SPEC,              "call": actions.rerun_analysis},
]

TOOL_SPECS: List[Dict[str, Any]] = [t["spec"] for t in _TOOLS]
_BY_NAME: Dict[str, Callable[..., Dict[str, Any]]] = {
    t["spec"]["name"]: t["call"] for t in _TOOLS
}


def tool_names() -> List[str]:
    return list(_BY_NAME.keys())


def dispatch(name: str, case_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run a tool by name. Never raises — converts any exception into an error
    payload the agent loop can hand back to the model.
    """
    fn = _BY_NAME.get(name)
    if fn is None:
        return {
            "is_error": True,
            "error": f"Unknown tool: {name}. Available: {', '.join(_BY_NAME)}",
        }
    try:
        out = fn(case_id, **(args or {}))
    except TypeError as e:
        return {"is_error": True, "error": f"Bad arguments for {name}: {e}"}
    except Exception as e:  # noqa: BLE001 — must not crash the loop
        return {
            "is_error": True,
            "error": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc(limit=4),
        }
    if not isinstance(out, dict):
        return {"is_error": True, "error": f"Tool {name} returned non-dict: {type(out).__name__}"}
    out.setdefault("citations", [])
    return out


__all__ = ["TOOL_SPECS", "dispatch", "tool_names"]
