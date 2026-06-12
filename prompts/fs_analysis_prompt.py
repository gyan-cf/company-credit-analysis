"""Financial Statement analysis prompt builder."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from prompts.base_system_prompt import get_base_system_prompt


def load_context_file(filename: str) -> str:
    context_dir = Path(__file__).parent / "context"
    file_path = context_dir / filename
    if file_path.exists():
        return file_path.read_text(encoding="utf-8")
    return ""


def build_fs_analysis_prompt(
    fs_data: dict,
    policy_context: dict = None,
    cross_source_context: dict = None,
) -> str:
    system_prompt = get_base_system_prompt()
    policy_context = policy_context or {}
    company_policy = policy_context.get("company_policy", {})
    portfolio = policy_context.get("portfolio_norms", {})

    cross_summary = ""
    if cross_source_context:
        fs_gst = cross_source_context.get("fs_gst", {})
        fs_bank = cross_source_context.get("fs_bank", {})
        fs_bureau = cross_source_context.get("fs_bureau", {})
        if fs_gst:
            cross_summary += f"\n### FS vs GST\n- Revenue gap: {fs_gst.get('fs_gst_gap_pct', 0)*100:.1f}%\n"
        if fs_bank:
            cross_summary += f"\n### FS vs Bank\n- Revenue gap: {fs_bank.get('fs_bank_gap_pct', 0)*100:.1f}%\n"
        if fs_bureau:
            cross_summary += f"\n### FS vs Bureau\n- Debt gap: {fs_bureau.get('fs_bureau_gap_pct', 0)*100:.1f}%\n"

    return f"""{system_prompt}

# Task: Financial Statement Analysis (Corporate)

Analyze audited/company financial statements. Use ONLY provided JSON. Compute no new numbers.

## Policy Thresholds
- Current ratio min: {portfolio.get('current_ratio_min', 1.0)}
- Debt/Equity max: {portfolio.get('debt_equity_max', 3.0)}
- Interest coverage min: {portfolio.get('interest_coverage_min', 1.5)}
- Debt/EBITDA max: {company_policy.get('max_debt_ebitda', 4.0)}

## Cross-Source Context
{cross_summary}

## Input Data
```json
{json.dumps(fs_data, indent=2, default=str)}
```

## Output (valid JSON only)
{{
  "memo": {{
    "executive_summary": ["..."],
    "profitability": {{"assessment": "...", "key_metrics": []}},
    "leverage": {{"assessment": "...", "key_metrics": []}},
    "liquidity": {{"assessment": "...", "key_metrics": []}},
    "cash_conversion": {{"assessment": "...", "key_metrics": []}},
    "audit_flags": [],
    "strengths": [{{"message": "...", "evidence": [["metric", "FY", value, threshold]]}}],
    "watchouts": [{{"message": "...", "evidence": [["metric", "FY", value, threshold]]}}],
    "red_flags": [{{"message": "...", "evidence": [["metric", "FY", value, threshold]]}}],
    "underwriter_questions": [{{"question": "...", "documents_requested": [], "rationale": "..."}}]
  }},
  "card_view": {{
    "card_type": "FS",
    "summary_title": "...",
    "summary_subtitle": "...",
    "strengths": [{{"id": "s1", "message": "..."}}],
    "risks": [{{"id": "r1", "message": "...", "severity": "high|medium|low"}}],
    "key_numbers": [{{"label": "...", "value": 0, "unit": "ratio", "trend": "up|down|stable"}}],
    "cross_links": [{{"from": "FS", "to": "GST|BANK|BUREAU", "message": "...", "severity": "medium"}}]
  }}
}}
"""
