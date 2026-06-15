"""
End-to-end financial-statement analysis pipeline.

Scope (this stage): financial statements only. Bank / GST / Bureau ingestion
have been retired from `/analyze` — the FS path is the production flow and
the legacy chain (`features/*`, `core/data`, `core/engine`, `core/output`,
`agents/orchestrator.py`) is no longer exercised here.

Flow per case:

    1. Ingest financials (SGIngestionPipeline) — produces:
         parsed/financials/<source_id>/{manifest.json, tables/, narrative/, notes/}
         parsed/financials/index.json
         parsed/sg_ingestion.json
         features/fs_periods_canonical.json
    2. Compute FS analytics over the merged canonical periods (`core/features/fs_analytics`)
    3. Run FS + Industry + Qualitative agents via the slim runner
    4. Aggregate card_views into `assessment_summary`
    5. Emit a FS-focused credit memo

Outputs (all under `cases/<id>/`):
    parsed/sg_ingestion.json           # raw ingestion result
    parsed/financials/                 # labelled-block bundles + rollup index
    features/fs_periods_canonical.json # canonical multi-period spread
    features/fs_analytics.json         # ratios + trends + agent payload
    agents/<agent>.json                # per-agent memo + card_view
    assessment.json                    # aggregated card view
    memo.md                            # committee-ready credit memo
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from config.config import get_config
from core.agents.agent_runner import AgentRunner, aggregate_cards
from core.agents.fs_analysis import (
    run_fs_agent,
    run_industry_agent,
    run_qualitative_agent,
)
from core.cases.case_store import CaseStore
from core.features.fs_analytics import build_fs_agent_data, build_fs_agent_data_from_merged
from core.ingestion.sg_pipeline import SGIngestionPipeline
from core.knowledge import build_case_wiki


class AnalysisPipeline:
    """FS-only credit analysis: ingest → analytics → agents → memo."""

    def __init__(self, case_store: Optional[CaseStore] = None):
        self.store = case_store or CaseStore()
        self.config = get_config()

    # ------------------------------------------------------------------ ingestion

    def _ensure_financials_ingested(self, case_id: str) -> Dict[str, Any]:
        """Run SGIngestionPipeline if no blocks index exists yet for this case."""
        case_root = self.store._case_path(case_id)
        parsed_root = case_root / "parsed" / "financials"
        index_path = parsed_root / "index.json"
        if index_path.exists():
            return json.loads(index_path.read_text(encoding="utf-8"))

        raw_dir = case_root / "raw" / "financials"
        if not raw_dir.exists() or not any(raw_dir.iterdir()):
            raise FileNotFoundError(
                f"No financial statements uploaded for case {case_id}. "
                f"Upload PDFs to {raw_dir} (POST /cases/{case_id}/upload, source_type=financials) "
                f"or POST /cases/{case_id}/ingest/sg first."
            )

        pipeline = SGIngestionPipeline(expand_zips=True, ocr_enabled=True)
        result = pipeline.ingest_path(
            raw_dir, parsed_root=parsed_root, case_root=case_root,
        ).to_dict()
        self.store.save_parsed(case_id, "sg_ingestion", result)
        self.store.save_features(
            case_id, "fs_periods_canonical",
            {"periods": result.get("periods", []), "summary": result.get("summary", {})},
        )
        build_case_wiki(case_root)
        return result.get("blocks_index", {})

    # ------------------------------------------------------------------ analytics

    def _build_fs_analytics(self, case_id: str, manifest: Dict[str, Any]) -> Dict[str, Any]:
        """Read canonical periods → compute ratios + agent payload."""
        ingestion = self.store.load_parsed(case_id, "sg_ingestion") or {}
        periods = ingestion.get("periods", [])
        profile = ingestion.get("profile", {}) or {}
        review_flags = ingestion.get("review_flags", [])

        entity = {
            "name": profile.get("entity_name") or manifest.get("company_name", ""),
            "uen": profile.get("uen") or manifest.get("cin", ""),
            "framework": profile.get("framework", "SFRS"),
            "audited": profile.get("audited", False),
            "consolidated": profile.get("consolidated", False),
            "ssic": profile.get("primary_ssic_code", ""),
            "industry_hint": manifest.get("industry_hint", "generic"),
        }
        financials_dir = self.store._case_path(case_id) / "parsed" / "financials"
        fs_data = build_fs_agent_data_from_merged(
            financials_dir,
            perimeter="company",
            entity=entity,
            review_flags=review_flags,
            fallback_periods=periods,
        ) or build_fs_agent_data(
            periods, perimeter="company", entity=entity, review_flags=review_flags,
        )
        self.store.save_features(case_id, "fs_analytics", fs_data)
        return fs_data

    # ------------------------------------------------------------------ memo

    @staticmethod
    def _fmt_num(v: Any) -> str:
        if v is None:
            return "—"
        if isinstance(v, float):
            if abs(v) < 10:
                return f"{v:.2f}"
            return f"{v:,.0f}"
        return str(v)

    def _generate_credit_memo(
        self,
        assessment: Dict[str, Any],
        manifest: Dict[str, Any],
        fs_data: Dict[str, Any],
    ) -> str:
        company = (
            fs_data.get("entity", {}).get("name")
            or manifest.get("company_name")
            or "Unknown"
        )
        industry = manifest.get("industry_hint", "generic")
        uen = fs_data.get("entity", {}).get("uen") or manifest.get("cin", "—")

        cards = assessment.get("cards", [])
        cross_findings = assessment.get("cross_findings", [])
        full_results = assessment.get("full_results", {}) or {}
        fs_result = full_results.get("fs", {})
        fs_memo = fs_result.get("memo", {}) or {}

        fys = fs_data.get("fys", [])
        by_fy = fs_data.get("by_fy", {})
        trends = fs_data.get("trends", {})

        lines = [
            f"# Credit Memorandum — {company}",
            "",
            "## 1. Borrower Profile",
            f"- **Company:** {company}",
            f"- **UEN:** {uen}",
            f"- **Industry:** {industry}",
            f"- **Framework:** {fs_data.get('entity', {}).get('framework', 'SFRS')}"
            f" · {'Audited' if fs_data.get('entity', {}).get('audited') else 'Unaudited'}"
            f" · {'Consolidated' if fs_data.get('entity', {}).get('consolidated') else 'Standalone'}",
            "",
            "## 2. Executive Summary",
        ]
        for bullet in (fs_memo.get("executive_summary") or [])[:4]:
            lines.append(f"- {bullet}")
        if not fs_memo.get("executive_summary"):
            lines.append(f"- {len(cards)} assessment card(s) generated from {len(fys)} FY period(s).")
        lines.append("")

        # Financial summary block
        lines.append("## 3. Financial Summary")
        if fys:
            metric_rows = [
                ("Revenue",          [self._fmt_num(by_fy[fy]["raw"].get("revenue")) for fy in fys]),
                ("Gross profit",     [self._fmt_num(by_fy[fy]["raw"].get("gross_profit")) for fy in fys]),
                ("EBITDA",           [self._fmt_num(by_fy[fy]["raw"].get("ebitda")) for fy in fys]),
                ("PAT",              [self._fmt_num(by_fy[fy]["raw"].get("pat")) for fy in fys]),
                ("Total assets",     [self._fmt_num(by_fy[fy]["raw"].get("total_assets")) for fy in fys]),
                ("Total equity",     [self._fmt_num(by_fy[fy]["raw"].get("total_equity")) for fy in fys]),
                ("Total debt",       [self._fmt_num(by_fy[fy]["raw"].get("total_debt")) for fy in fys]),
                ("CFO",              [self._fmt_num(by_fy[fy]["raw"].get("cfo")) for fy in fys]),
            ]
            header = "| Line item | " + " | ".join(fys) + " |"
            sep = "|---|" + "|".join(["---:"] * len(fys)) + "|"
            lines.append(header)
            lines.append(sep)
            for label, vals in metric_rows:
                lines.append(f"| {label} | " + " | ".join(vals) + " |")
            lines.append("")

        # Ratios
        lines.append("## 4. Key Ratios")
        if fys:
            ratio_keys = [
                ("gross_margin",     "Gross margin"),
                ("ebitda_margin",    "EBITDA margin"),
                ("pat_margin",       "PAT margin"),
                ("current_ratio",    "Current ratio"),
                ("quick_ratio",      "Quick ratio"),
                ("debt_equity",      "Debt / Equity"),
                ("debt_ebitda",      "Debt / EBITDA"),
                ("interest_coverage","Interest coverage"),
                ("receivable_days",  "Receivable days"),
                ("payable_days",     "Payable days"),
            ]
            header = "| Ratio | " + " | ".join(fys) + " |"
            sep = "|---|" + "|".join(["---:"] * len(fys)) + "|"
            lines.append(header)
            lines.append(sep)
            for key, label in ratio_keys:
                vals = [self._fmt_num(by_fy[fy]["ratios"].get(key)) for fy in fys]
                lines.append(f"| {label} | " + " | ".join(vals) + " |")
            lines.append("")

        # YoY trends — show only the most decision-relevant
        lines.append("## 5. YoY Trends")
        for k in ("revenue_growth_yoy", "ebitda_growth_yoy", "pat_growth_yoy"):
            v = trends.get(k)
            if v is not None:
                lines.append(f"- {k.replace('_', ' ').title()}: {v*100:.1f}%")
        lines.append("")

        # Agent strengths / watchouts / red flags
        def _list(section: str) -> list:
            return [x for x in (fs_memo.get(section) or [])]

        if _list("strengths"):
            lines.append("## 6. Strengths")
            for s in _list("strengths"):
                lines.append(f"- {s.get('message','')}")
            lines.append("")
        if _list("watchouts"):
            lines.append("## 7. Watchouts")
            for w in _list("watchouts"):
                lines.append(f"- {w.get('message','')}")
            lines.append("")
        if _list("red_flags"):
            lines.append("## 8. Red Flags")
            for r in _list("red_flags"):
                lines.append(f"- {r.get('message','')}")
            lines.append("")

        # Qualitative probes
        qualitative = full_results.get("qualitative", {})
        probes = ((qualitative or {}).get("probes")) or []
        if probes:
            lines.append("## 9. Probe Questions")
            for p in probes[:8]:
                q = p.get("question", "")
                lines.append(f"- {q}")
            lines.append("")

        # Cross-findings (will mostly be empty in FS-only mode)
        if cross_findings:
            lines.append("## 10. Cross-Source Findings")
            for cf in cross_findings:
                lines.append(f"- [{cf.get('severity','low')}] {cf.get('message','')}")
            lines.append("")

        lines.extend([
            "## Recommendation",
            "Provisional based on FS spread; finalise after bank conduct + bureau review.",
            "",
            "---",
            "*Generated by CrediSage — FS-only stage*",
        ])
        return "\n".join(lines)

    # ------------------------------------------------------------------ run

    def run(self, case_id: str, provider: Optional[str] = None) -> Dict[str, Any]:
        manifest = self.store.get_manifest(case_id)
        self.store.update_status(case_id, "ingesting", 10)

        try:
            self._ensure_financials_ingested(case_id)
            self.store.update_status(case_id, "ingesting", 35)

            fs_data = self._build_fs_analytics(case_id, manifest)
            self.store.update_status(case_id, "analyzing", 55)

            runner = AgentRunner(provider=provider)
            policy_context = self.config.get_policy_context()
            policy_context["company_policy"] = self.config.get("company_policy", {})

            results: Dict[str, Dict[str, Any]] = {}
            results["fs"] = run_fs_agent(runner, fs_data, policy_context=policy_context)
            self.store.update_status(case_id, "analyzing", 70)

            results["industry"] = run_industry_agent(
                runner, manifest, fs_data, policy_context=policy_context,
            )
            self.store.update_status(case_id, "analyzing", 80)

            partial = aggregate_cards({k: v for k, v in results.items() if k != "qualitative"})
            results["qualitative"] = run_qualitative_agent(runner, partial, manifest)
            self.store.update_status(case_id, "analyzing", 90)

            aggregated = aggregate_cards(results)
            assessment = {
                "cards": aggregated["cards"],
                "cross_findings": aggregated["cross_findings"],
                "agent_results": {
                    k: {
                        "success": v.get("_metadata", {}).get("success", "error" not in v),
                        "has_memo": "memo" in v,
                        "has_card_view": "card_view" in v,
                        "mock": v.get("_metadata", {}).get("mock", False),
                    }
                    for k, v in results.items()
                },
                "full_results": results,
                "manifest": manifest,
            }
            self.store.save_assessment_summary(case_id, assessment)
            for agent_name, result in results.items():
                self.store.save_agent_result(case_id, agent_name, result)

            self.store.update_status(case_id, "generating_memo", 95)
            memo = self._generate_credit_memo(assessment, manifest, fs_data)
            self.store.save_credit_memo(case_id, memo)
            knowledge = build_case_wiki(self.store._case_path(case_id))

            self.store.update_status(case_id, "completed", 100)
            return {
                "case_id": case_id,
                "status": "completed",
                "assessment_summary": assessment,
                "credit_memo": memo,
                "knowledge": {
                    "page_count": knowledge.get("page_count", 0),
                    "chunk_count": knowledge.get("chunk_count", 0),
                    "evidence_count": knowledge.get("evidence_count", 0),
                },
            }

        except Exception as e:
            self.store.update_status(case_id, "failed", 0, error=str(e))
            raise
