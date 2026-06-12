"""
FS-scoped agent surface — exactly the three agents we need for the
financial-statements pipeline.

- `run_fs_agent`         — quant analysis of the FS spread + ratios
- `run_industry_agent`   — sector overlay on the FS summary
- `run_qualitative_agent` — probe questions for the management meeting

Each is a thin shim over an `AgentRunner`: build the prompt → run → return
the parsed dict. Bank / GST / Bureau agents are intentionally not exposed
here — those flows live (for now) in the legacy stack and are not part of
the FS-only release.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .agent_runner import AgentRunner


def run_fs_agent(
    runner: AgentRunner,
    fs_data: Dict[str, Any],
    *,
    policy_context: Optional[Dict[str, Any]] = None,
    cross_source_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """LLM analysis of the FS spread + ratios. Returns memo + card_view."""
    from prompts.fs_analysis_prompt import build_fs_analysis_prompt
    prompt = build_fs_analysis_prompt(fs_data, policy_context, cross_source_context)
    return runner.run(
        prompt=prompt,
        agent="FS",
        card_type="FS",
        metrics=fs_data.get("summary_ratios", {}) or {},
    )


def run_industry_agent(
    runner: AgentRunner,
    manifest: Dict[str, Any],
    fs_summary: Dict[str, Any],
    *,
    policy_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Sector overlay applied to the FS summary."""
    from prompts.industry_analysis_prompt import build_industry_analysis_prompt
    prompt = build_industry_analysis_prompt(manifest, fs_summary, None, policy_context)
    return runner.run(
        prompt=prompt,
        agent="INDUSTRY",
        card_type="INDUSTRY",
        metrics={"industry": manifest.get("industry_code", "")},
    )


def run_qualitative_agent(
    runner: AgentRunner,
    assessment_summary: Dict[str, Any],
    manifest: Dict[str, Any],
    *,
    cross_source_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Probe questions ranked off the quantitative findings."""
    from prompts.qualitative_probe_prompt import build_qualitative_probe_prompt
    prompt = build_qualitative_probe_prompt(
        assessment_summary, manifest, cross_source_context or {}
    )
    return runner.run(
        prompt=prompt,
        agent="QUALITATIVE",
        card_type="QUALITATIVE",
        metrics={"probes": "generated"},
    )
