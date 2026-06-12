"""Industry context analysis prompt builder."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from prompts.base_system_prompt import get_base_system_prompt


def load_industry_context(industry_code: str) -> str:
    context_dir = Path(__file__).parent / "context" / "industries"
    for name in [f"{industry_code}.md", "manufacturing.md", "services.md"]:
        path = context_dir / name
        if path.exists():
            return path.read_text(encoding="utf-8")
    generic = Path(__file__).parent / "context" / "industry_patterns.md"
    return generic.read_text(encoding="utf-8") if generic.exists() else ""


def build_industry_analysis_prompt(
    manifest: dict,
    fs_summary: dict,
    bank_summary: dict = None,
    policy_context: dict = None,
) -> str:
    system_prompt = get_base_system_prompt()
    industry_code = manifest.get("industry_code", "generic")
    industry_context = load_industry_context(industry_code)

    return f"""{system_prompt}

# Task: Industry Overlay Analysis

Apply sector-specific benchmarks and risks to the borrower profile.

## Industry Code: {industry_code}
## Industry Hint: {manifest.get('industry_hint', 'generic')}

## Sector Context
{industry_context[:3000]}

## FS Summary
```json
{json.dumps(fs_summary, indent=2, default=str)[:4000]}
```

## Bank Summary (if any)
```json
{json.dumps(bank_summary or {}, indent=2, default=str)[:2000]}
```

Return ONLY valid JSON:
{{
  "industry_context": {{
    "industry_code": "{industry_code}",
    "sector_name": "...",
    "cyclicality": "low|medium|high",
    "typical_margins": {{}},
    "leverage_tolerance": {{}},
    "key_risks": ["..."],
    "policy_overlays": ["..."],
    "benchmarks": {{}}
  }},
  "card_view": {{
    "card_type": "INDUSTRY",
    "summary_title": "...",
    "summary_subtitle": "...",
    "strengths": [{{"id": "s1", "message": "..."}}],
    "risks": [{{"id": "r1", "message": "...", "severity": "medium"}}],
    "key_numbers": [{{"label": "...", "value": "...", "trend": "stable"}}],
    "cross_links": []
  }}
}}
"""
