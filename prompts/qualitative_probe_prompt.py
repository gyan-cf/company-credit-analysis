"""Qualitative probe questions prompt builder."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from prompts.base_system_prompt import get_base_system_prompt


def build_qualitative_probe_prompt(
    assessment_summary: dict,
    manifest: dict,
    cross_source_context: dict = None,
) -> str:
    system_prompt = get_base_system_prompt()
    cards = assessment_summary.get("cards", [])
    cross_findings = assessment_summary.get("cross_findings", [])

    return f"""{system_prompt}

# Task: Qualitative Assessment — Probe Questions

Based on quantitative findings, generate ranked probe questions for management meeting and document requests.

## Company: {manifest.get('company_name', 'Unknown')}
## Industry: {manifest.get('industry_hint', 'generic')}

## Assessment Cards
```json
{json.dumps(cards, indent=2, default=str)[:6000]}
```

## Cross Findings
```json
{json.dumps(cross_findings, indent=2, default=str)[:2000]}
```

## Cross-Source
```json
{json.dumps(cross_source_context or {}, indent=2, default=str)[:2000]}
```

Return ONLY valid JSON:
{{
  "probes": [
    {{
      "id": "q1",
      "question": "...",
      "priority": "high|medium|low",
      "rationale": "...",
      "theme": "revenue|leverage|liquidity|governance|compliance",
      "documents_requested": ["..."],
      "evidence_links": [["metric", "period", value, ""]],
      "management_meeting": true
    }}
  ],
  "card_view": {{
    "card_type": "QUALITATIVE",
    "summary_title": "Qualitative Probes",
    "summary_subtitle": "N questions for management",
    "strengths": [],
    "risks": [{{"id": "r1", "message": "Top probe theme", "severity": "medium"}}],
    "key_numbers": [{{"label": "High priority probes", "value": 0}}],
    "cross_links": []
  }}
}}
"""
