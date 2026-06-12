"""
Slim agent runner — single responsibility: take a prompt, return a parsed
+ schema-validated agent result.

Wraps the provider-specific LLM client (`core/client/{claude,openai}_client.py`),
parses the JSON the model returns, and validates the `card_view` against
`schemas/card_schema.json`. Falls back to a deterministic mock when no API
key is available so the rest of the pipeline can run for demo/testing.

Replaces the LLM-call + parse + validate logic that the legacy
`agents/orchestrator.py:MultiAgentOrchestrator` repeated across five
`run_*_agent` methods.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import jsonschema

from config.config import get_config


_CARD_SCHEMA_CACHE: Optional[Dict[str, Any]] = None


def _load_card_schema() -> Dict[str, Any]:
    global _CARD_SCHEMA_CACHE
    if _CARD_SCHEMA_CACHE is not None:
        return _CARD_SCHEMA_CACHE
    schema_path = Path(__file__).resolve().parents[2] / "schemas" / "card_schema.json"
    _CARD_SCHEMA_CACHE = json.loads(schema_path.read_text(encoding="utf-8"))
    return _CARD_SCHEMA_CACHE


def _validate_card_view(card: Dict[str, Any]) -> Optional[str]:
    """Return None on success, or an error string."""
    try:
        jsonschema.validate(instance=card, schema=_load_card_schema())
        return None
    except jsonschema.ValidationError as e:
        return str(e)
    except Exception as e:
        return f"Validation error: {e}"


def _deterministic_mock(agent: str, card_type: str, metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Returned when no API key is configured — keeps the pipeline runnable."""
    numeric_metrics = [
        {"label": k, "value": v, "trend": "stable"}
        for k, v in list(metrics.items())[:5]
        if isinstance(v, (int, float, str))
    ]
    return {
        "memo": {
            "executive_summary": [
                f"{agent} analysis completed using deterministic features.",
                f"Key metrics evaluated: {', '.join(list(metrics.keys())[:5])}",
            ],
            "strengths": [],
            "watchouts": [
                {"message": "Configure ANTHROPIC_API_KEY or OPENAI_API_KEY for full LLM narrative",
                 "evidence": []}
            ],
            "red_flags": [],
            "underwriter_questions": [],
        },
        "card_view": {
            "card_type": card_type,
            "summary_title": f"{card_type} Assessment (Deterministic)",
            "summary_subtitle": "Enable an LLM API key for full analysis",
            "strengths": [{"id": "s1", "message": "Data ingested successfully"}],
            "risks": [{"id": "r1", "message": "Full LLM interpretation pending API key", "severity": "low"}],
            "key_numbers": numeric_metrics,
            "cross_links": [],
        },
        "_metadata": {"agent": agent, "success": True, "mock": True},
    }


class AgentRunner:
    """One LLM client, one schema, one entry point."""

    def __init__(self, *, api_key: Optional[str] = None, provider: Optional[str] = None):
        self.config = get_config()
        self.provider = (provider or self.config.get("llm_provider", "openai")).lower()
        self.use_mock = self._decide_mock(api_key)
        self.client = None if self.use_mock else self._build_client(api_key)

    def _decide_mock(self, api_key: Optional[str]) -> bool:
        if api_key:
            return False
        if not self.config.get("analysis.skip_llm_if_no_api_key", True):
            return False
        openai_key = self.config.get("openai.api_key") or os.getenv("OPENAI_API_KEY", "")
        anthropic_key = self.config.get("anthropic.api_key") or os.getenv("ANTHROPIC_API_KEY", "")
        return not (openai_key or anthropic_key)

    def _build_client(self, api_key: Optional[str]):
        if self.provider == "anthropic":
            from core.client.claude_client import ClaudeClient
            return ClaudeClient(api_key=api_key)
        if self.provider == "openai":
            from core.client.openai_client import OpenAIClient
            return OpenAIClient(api_key=api_key)
        raise ValueError(f"Unknown LLM provider: {self.provider}")

    def run(
        self,
        *,
        prompt: str,
        agent: str,
        card_type: str,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Call the LLM with `prompt`, parse JSON, validate `card_view`.

        On any failure, returns an error result with the same shape as a
        successful call (so callers don't need to branch). Mock mode short-
        circuits straight to a deterministic payload.
        """
        metrics = metrics or {}
        if self.use_mock:
            return _deterministic_mock(agent, card_type, metrics)

        response = self.client.analyze(prompt)
        if not response.get("success", False):
            return {
                "error": response.get("error", "Unknown LLM error"),
                "success": False,
                "_metadata": {"agent": agent, "success": False},
            }

        result = self.client.parse_json_response(response["content"])
        if "error" in result:
            result.setdefault("raw_content", response.get("content", "")[:2000])
            result["_metadata"] = {
                "agent": agent,
                "usage": response.get("usage", {}),
                "success": False,
            }
            return result

        card = result.get("card_view")
        if card is not None:
            err = _validate_card_view(card)
            if err:
                result["_card_view_validation_warning"] = err

        result["_metadata"] = {
            "agent": agent,
            "usage": response.get("usage", {}),
            "success": True,
            "mock": False,
        }
        return result


def aggregate_cards(agent_results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Pull card_views out of agent results into a flat list, and surface any
    cross_links as cross_findings — matches the assessment-summary shape the
    existing UI consumes.
    """
    cards: List[Dict[str, Any]] = []
    cross_findings: List[Dict[str, Any]] = []
    for agent_name, res in agent_results.items():
        card = res.get("card_view")
        if not card:
            continue
        cards.append(card)
        for link in card.get("cross_links", []) or []:
            cross_findings.append({
                "id": f"{agent_name}_{link.get('from','')}_{link.get('to','')}",
                "message": link.get("message", ""),
                "severity": link.get("severity", "low"),
                "source": agent_name.upper(),
            })
    return {"cards": cards, "cross_findings": cross_findings}
