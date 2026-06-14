"""
Section-by-section credit report generator.

Loads aggregated case context (analytics + ACRA profile + per-source
documents), spawns one LLM call per LLM-driven section in parallel via
ThreadPoolExecutor, materialises a deterministic Financial Snapshot table,
and persists both the JSON and a Word (.docx) export under
`cases/<id>/reports/`.

Entry points:
    generate_report(case_id, case_root, template="credit_analysis") -> dict
    persist_report(case_root, report) -> dict[str, Path]
"""

from __future__ import annotations

import json
import logging
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .docx_writer import write_report_docx
from .html_renderer import markdown_to_html
from .llm_caller import call_section_llm
from .template import SECTIONS_FS_ONLY, build_section_context


logger = logging.getLogger(__name__)


# ---- Context loader -----------------------------------------------------------

def load_case_context(case_root: Path) -> Dict[str, Any]:
    """
    Aggregate everything the section prompts might want into one dict:
        - analytics:        cases/<id>/features/fs_analytics.json
        - acra_profile:     cases/<id>/parsed/acra_profile.json
        - ingestion:        cases/<id>/parsed/sg_ingestion.json
        - documents:        cases/<id>/parsed/financials/<source_id>/document.json
        - merged:           cases/<id>/parsed/financials/merged/*.json
    Missing files are simply omitted from the returned dict.
    """
    case_root = Path(case_root)
    ctx: Dict[str, Any] = {}

    def _read_json(p: Path) -> Optional[Dict[str, Any]]:
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("invalid JSON at %s", p)
            return None

    if (data := _read_json(case_root / "features" / "fs_analytics.json")):
        ctx["analytics"] = data
    if (data := _read_json(case_root / "parsed" / "acra_profile.json")):
        ctx["acra_profile"] = data
    if (data := _read_json(case_root / "parsed" / "sg_ingestion.json")):
        ctx["ingestion"] = data

    fin_dir = case_root / "parsed" / "financials"
    docs: List[Dict[str, Any]] = []
    if fin_dir.exists():
        for src_dir in fin_dir.iterdir():
            if not src_dir.is_dir() or src_dir.name == "merged":
                continue
            d = _read_json(src_dir / "document.json")
            if d is not None:
                docs.append(d)
    ctx["documents"] = docs

    merged: Dict[str, Any] = {}
    merged_dir = fin_dir / "merged"
    if merged_dir.exists():
        for f in merged_dir.glob("*.json"):
            m = _read_json(f)
            if m is not None:
                merged[f.stem] = m
    ctx["merged"] = merged

    return ctx


# ---- Deterministic section: Financial Snapshot -------------------------------

_SNAPSHOT_METRICS: List[tuple] = [
    ("Revenue",                "revenue"),
    ("Cost of sales",          "cost_of_sales"),
    ("Gross profit",           "gross_profit"),
    ("EBITDA",                 "ebitda"),
    ("EBIT",                   "ebit"),
    ("PBT",                    "pbt"),
    ("PAT",                    "pat"),
    ("Total assets",           "total_assets"),
    ("Total equity",           "total_equity"),
    ("Total debt",             "total_debt"),
    ("Cash & equivalents",     "cash"),
    ("Operating cash flow",    "cfo"),
    ("Capex",                  "capex"),
    ("Free cash flow",         "fcf"),
]


def _fmt(v: Any) -> str:
    if v is None or v == "":
        return "—"
    if isinstance(v, float):
        if v != v:  # NaN
            return "—"
        if abs(v) >= 1000 or v.is_integer():
            return f"{v:,.0f}"
        return f"{v:,.2f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def render_financial_snapshot(context: Dict[str, Any]) -> str:
    """Generate the Financial Snapshot section as a markdown table."""
    analytics = context.get("analytics", {}) or {}
    fys = analytics.get("fys", []) or []
    by_fy = analytics.get("by_fy", {}) or {}

    if not fys:
        return "_No financial data available — analytics not generated for this case._"

    lines: List[str] = []
    currency = (
        (analytics.get("entity") or {}).get("currency")
        or (by_fy.get(fys[0]) or {}).get("currency")
        or "SGD"
    )
    lines.append(f"Selected line items in {currency} across the financial years reviewed.")
    lines.append("")

    header = "| Line item | " + " | ".join(fys) + " |"
    sep = "|---|" + "|".join(["---:"] * len(fys)) + "|"
    lines.append(header)
    lines.append(sep)
    for label, key in _SNAPSHOT_METRICS:
        cells = [_fmt(((by_fy.get(fy) or {}).get("raw") or {}).get(key)) for fy in fys]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")

    return "\n".join(lines)


# ---- Section generation ------------------------------------------------------

def _generate_section(section_def: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Generate one section (deterministic or LLM-backed)."""
    code = section_def["code"]
    base = {
        "code":   code,
        "number": section_def["number"],
        "title":  section_def["title"],
    }

    if section_def.get("type") == "deterministic_table":
        if code == "financial_snapshot":
            md = render_financial_snapshot(context)
        else:
            md = "_Deterministic generator not implemented for this section._"
        return {**base, "markdown": md, "html": markdown_to_html(md), "source": "deterministic"}

    prompt = build_section_context(section_def, context)
    try:
        md = call_section_llm(prompt)
    except Exception as e:
        logger.exception("LLM call failed for section %s", code)
        md = f"_Error generating section: {type(e).__name__}: {e}_"
        return {
            **base, "markdown": md, "html": markdown_to_html(md),
            "source": "error", "error": str(e),
        }
    return {**base, "markdown": md, "html": markdown_to_html(md), "source": "llm"}


# ---- Top-level orchestration -------------------------------------------------

def generate_report(
    case_id: str,
    case_root: Path,
    *,
    template: str = "credit_analysis",
    max_workers: int = 5,
) -> Dict[str, Any]:
    """
    Compose the full report. Runs LLM sections in parallel; deterministic
    sections (financial snapshot table) run inline.
    """
    case_root = Path(case_root)
    context = load_case_context(case_root)
    analytics = context.get("analytics", {}) or {}
    acra = context.get("acra_profile", {}) or {}
    ent = analytics.get("entity") or {}
    entity_name = (
        acra.get("entity_name") or ent.get("name") or "the Borrower"
    )

    sections_def = SECTIONS_FS_ONLY

    # Parallel section generation
    results: Dict[str, Dict[str, Any]] = {}
    started_at = datetime.utcnow()
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_generate_section, sec, context): sec["code"]
            for sec in sections_def
        }
        for fut in as_completed(futures):
            code = futures[fut]
            try:
                results[code] = fut.result()
            except Exception as e:
                logger.exception("Section future raised for %s", code)
                meta = next((s for s in sections_def if s["code"] == code), {})
                results[code] = {
                    "code":     code,
                    "number":   meta.get("number"),
                    "title":    meta.get("title", code),
                    "markdown": f"_Section future failed: {type(e).__name__}: {e}_",
                    "source":   "error",
                    "error":    str(e),
                }

    # Preserve canonical ordering
    sections_ordered = [results[s["code"]] for s in sections_def if s["code"] in results]
    finished_at = datetime.utcnow()

    return {
        "case_id":      case_id,
        "template":     template,
        "generated_at": finished_at.isoformat() + "Z",
        "duration_s":   round((finished_at - started_at).total_seconds(), 1),
        "entity_name":  entity_name,
        "fys":          analytics.get("fys", []) or [],
        "section_count": len(sections_ordered),
        "sections":     sections_ordered,
    }


def persist_report(case_root: Path, report: Dict[str, Any]) -> Dict[str, Path]:
    """Write the report to disk in both JSON and DOCX formats."""
    case_root = Path(case_root)
    reports_dir = case_root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    template = report.get("template", "credit_analysis")
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    json_path = reports_dir / f"{ts}_{template}.json"
    docx_path = reports_dir / f"{ts}_{template}.docx"

    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    write_report_docx(report, docx_path)

    # "Latest" copies for the API to serve without case-by-case lookup.
    latest_json = reports_dir / "latest.json"
    latest_docx = reports_dir / "latest.docx"
    latest_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if latest_docx.exists():
        latest_docx.unlink()
    shutil.copy(docx_path, latest_docx)

    return {
        "json_path":   json_path,
        "docx_path":   docx_path,
        "latest_json": latest_json,
        "latest_docx": latest_docx,
    }
