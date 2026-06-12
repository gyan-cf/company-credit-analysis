"""
AI Co-worker — case-scoped chat with analyst skills.
"""

import json
import re
from typing import Any, Dict, List, Optional

from core.cases.case_store import CaseStore
from config.config import get_config


class CoworkerService:
    """Financial analyst co-worker scoped to a case."""

    def __init__(self, store: Optional[CaseStore] = None):
        self.store = store or CaseStore()
        self.config = get_config()

    def _build_context(self, case_id: str) -> str:
        manifest = self.store.get_manifest(case_id)
        assessment = self.store.load_assessment_summary(case_id)
        fs_ratios = self.store.load_features(case_id, "fs_ratios")

        return json.dumps({
            "manifest": manifest,
            "cards_count": len(assessment.get("cards", [])),
            "cross_findings": assessment.get("cross_findings", [])[:10],
            "fs_ratios_latest": self._latest_ratios(fs_ratios),
            "card_summaries": [
                {
                    "type": c.get("card_type"),
                    "title": c.get("summary_title"),
                    "risks": [r.get("message") for r in c.get("risks", [])[:3]],
                }
                for c in assessment.get("cards", [])
            ],
        }, indent=2, default=str)

    def _latest_ratios(self, fs_ratios: Dict) -> Dict:
        by_fy = fs_ratios.get("by_fy", {})
        if not by_fy:
            return {}
        latest = sorted(by_fy.keys())[-1]
        return by_fy[latest].get("ratios", {})

    def _detect_skill(self, message: str) -> str:
        msg = message.lower()
        if "why" in msg and ("flag" in msg or "coverage" in msg or "ratio" in msg):
            return "explain_metric"
        if "compare" in msg or "change" in msg or "yoy" in msg:
            return "compare_periods"
        if "gap" in msg or "reconcil" in msg or "gst" in msg and "fs" in msg:
            return "cross_source_drilldown"
        if "question" in msg or "probe" in msg or "cfo" in msg or "management" in msg:
            return "draft_probe"
        if "if" in msg and ("drop" in msg or "what if" in msg or "%" in msg):
            return "what_if"
        if "rewrite" in msg or "memo" in msg:
            return "memo_section_edit"
        return "general"

    def _skill_explain_metric(self, message: str, context: str, fs_ratios: Dict) -> tuple:
        ratios = self._latest_ratios(fs_ratios)
        citations = []

        for metric, value in ratios.items():
            if metric.replace("_", " ") in message.lower() or metric in message.lower():
                citations.append({
                    "metric": metric,
                    "value": value,
                    "source": "fs_ratios",
                })
                policy = self.config.get("portfolio_norms", {})
                threshold = policy.get(f"{metric}_min") or policy.get("interest_coverage_min")
                reply = (
                    f"**{metric}** is **{value:.2f}** based on the latest FY in case data. "
                )
                if threshold:
                    reply += f"Policy reference threshold: {threshold}. "
                if metric == "interest_coverage" and value < 1.5:
                    reply += "This is below typical minimum (1.5x) — flag for underwriter review."
                elif metric == "debt_equity" and value > 3:
                    reply += "Leverage exceeds conservative corporate tolerance."
                else:
                    reply += "Compare against industry overlay and cross-source checks."
                return reply, citations

        return (
            "I could not match a specific metric in your question. "
            f"Available ratios: {', '.join(ratios.keys())}.",
            citations,
        )

    def _skill_cross_source(self, assessment: Dict) -> tuple:
        findings = assessment.get("cross_findings", [])
        if not findings:
            return "No cross-source findings recorded yet. Run full analysis first.", []

        lines = ["**Cross-source reconciliations:**"]
        citations = []
        for f in findings:
            lines.append(f"- [{f.get('severity')}] {f.get('message')}")
            citations.append({"source": f.get("source"), "message": f.get("message")})
        return "\n".join(lines), citations

    def _skill_draft_probe(self, assessment: Dict) -> tuple:
        qual = self.store.load_agent_result if False else None  # placeholder
        for card in assessment.get("cards", []):
            if card.get("card_type") == "QUALITATIVE":
                return (
                    "Qualitative probes are in the QUALITATIVE assessment card. "
                    "Top themes: revenue quality, leverage, cross-source gaps.",
                    [{"card_type": "QUALITATIVE"}],
                )

        risks = []
        for card in assessment.get("cards", []):
            for r in card.get("risks", []):
                if r.get("severity") in ("high", "medium"):
                    risks.append(r.get("message", ""))

        probes = [
            f"Please explain: {r}" for r in risks[:3]
        ] or [
            "Please provide latest debt schedule with EMI breakdown.",
            "Explain material FS vs GST revenue variance.",
            "Clarify related-party transactions in last FY.",
        ]

        return "**Draft management probes:**\n" + "\n".join(f"- {p}" for p in probes), []

    def chat(
        self,
        case_id: str,
        message: str,
        skill: Optional[str] = None,
        use_llm: bool = False,
    ) -> Dict[str, Any]:
        """Process a co-worker chat message."""
        skill = skill or self._detect_skill(message)
        context = self._build_context(case_id)
        assessment = self.store.load_assessment_summary(case_id)
        fs_ratios = self.store.load_features(case_id, "fs_ratios")

        if skill == "explain_metric":
            reply, citations = self._skill_explain_metric(message, context, fs_ratios)
        elif skill == "cross_source_drilldown":
            reply, citations = self._skill_cross_source(assessment)
        elif skill == "draft_probe":
            reply, citations = self._skill_draft_probe(assessment)
        elif skill == "compare_periods":
            trends = fs_ratios.get("trends", {})
            reply = "**YoY trends:**\n" + "\n".join(
                f"- {k}: {v:.1%}" if isinstance(v, float) else f"- {k}: {v}"
                for k, v in list(trends.items())[:8]
            ) or "Insufficient multi-year data for comparison."
            citations = [{"source": "fs_ratios.trends", "data": trends}]
        elif skill == "what_if":
            m = re.search(r"(\d+)\s*%", message)
            pct = int(m.group(1)) / 100 if m else 0.15
            ratios = self._latest_ratios(fs_ratios)
            ebitda_margin = ratios.get("ebitda_margin", 0.1)
            ic = ratios.get("interest_coverage", 2.0)
            new_ic = ic * (1 - pct) if ic else 0
            reply = (
                f"If EBITDA/stress impact ~{pct:.0%}: interest coverage would move from "
                f"{ic:.2f}x to ~{new_ic:.2f}x (approximate). "
                "Re-run with updated projections for credit committee."
            )
            citations = [{"metric": "interest_coverage", "baseline": ic, "stressed": new_ic}]
        else:
            reply = (
                f"I have context on this case (status: {self.store.get_manifest(case_id).get('status')}). "
                "Ask me to explain a metric, compare periods, drill into cross-source gaps, "
                "or draft management probes."
            )
            citations = []

        self.store.save_chat_message(case_id, "user", message)
        self.store.save_chat_message(case_id, "assistant", reply)

        return {"reply": reply, "citations": citations, "skill_used": skill}
