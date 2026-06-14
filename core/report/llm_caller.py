"""
Section-level LLM caller for the credit memo generator.

Plain chat completion (no tool calling) — each section asks for markdown
output directly, which is the natural format for the report sections.
"""

from __future__ import annotations

import logging
import os
from typing import Optional


logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-4o"
DEFAULT_MAX_TOKENS = 2500


SYSTEM_PROMPT = """\
You are a senior credit analyst at a Singapore bank, drafting one section of an
internal Credit Analysis Report for the credit-risk committee.

Style:
- Formal, precise, evidence-led. Singapore credit-analyst voice.
- Reference specific numbers and FYs from the data provided. Never invent.
- Where the data is insufficient or ambiguous, say so explicitly.
- Use SFRS(I) / SG SME terminology where relevant.

Output:
- PURE MARKDOWN — no JSON, no ```markdown``` fence, no preamble or sign-off.
- Do NOT include the section heading (e.g. "5. Revenue and Business Momentum")
  in your output — that's added by the report writer.
- Start directly with the section content.
- 2-4 short paragraphs OR 3-6 bullet points (whichever is more appropriate).
- Bold key conclusions sparingly.
"""


def call_section_llm(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = 0.3,
    api_key: Optional[str] = None,
) -> str:
    """Call OpenAI with the section prompt; return markdown content."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
    )
    content = response.choices[0].message.content or ""
    usage = getattr(response, "usage", None)
    if usage is not None:
        logger.info(
            "report_section usage: prompt=%s completion=%s total=%s",
            getattr(usage, "prompt_tokens", "?"),
            getattr(usage, "completion_tokens", "?"),
            getattr(usage, "total_tokens", "?"),
        )
    return content.strip()
