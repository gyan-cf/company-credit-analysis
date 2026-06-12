"""
Per-source block writer.

Takes one extracted financial-statement source (an `FSExtraction` plus the
narrative sections cut from the same text) and lays it out on disk as a
labelled-block bundle the frontend can render and the analyst can download:

    <out_dir>/<source_id>/
        manifest.json
        raw.txt
        tables/
            sofp__company.csv          # FYs as columns, statement-flow order
            sofp__company.json         # render-shaped sidecar (same data + structure)
            soci__company.csv
            soci__company.json
            socf__company.csv
            socf__company.json
            sofp__group.csv            # only when consolidated
            ...
        narrative/
            directors_statement.md
            auditor_report.md
        notes/
            note_01_corporate_information.md
            note_02_summary_of_significant_accounting_policies.md
            ...

The manifest indexes every block so the API can hand the frontend a single
JSON it can render the document tree off.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .canonical_map import CANONICAL, SOFP, SOCI, SOCF, STATEMENT_OF
from .fs_text_extract import FSExtraction, ColumnHeader, ExtractedLine
from .narrative_extract import NarrativeSection


# ---- Helpers -------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str, max_len: int = 60) -> str:
    s = _SLUG_RE.sub("_", text.lower()).strip("_")
    return s[:max_len] or "untitled"


def _content_hash(pdf_path: Path) -> str:
    h = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def source_id_for(pdf_path: Path) -> Tuple[str, str]:
    """Return (short source_id, full sha256) for a PDF — stable across reruns."""
    full = _content_hash(pdf_path)
    return full[:12], full


def _format_fy_values(line: ExtractedLine, fy_keys: List[str]) -> List[str]:
    """Render FY cells for a CSV row — blank for section headers."""
    if not line.values:
        return ["" for _ in fy_keys]
    out: List[str] = []
    for key in fy_keys:
        v = line.values.get(key)
        if v is None:
            out.append("")
        elif float(v).is_integer():
            out.append(str(int(v)))
        else:
            out.append(f"{v:.2f}")
    return out


# ---- Table block writers --------------------------------------------------------

def _columns_by_perimeter(columns: List[ColumnHeader]) -> Dict[str, List[ColumnHeader]]:
    grouped: Dict[str, List[ColumnHeader]] = {}
    for c in columns:
        grouped.setdefault(c.perimeter, []).append(c)
    return grouped


def _lines_for(extraction: FSExtraction, statement: str) -> List[ExtractedLine]:
    rows = [ln for ln in extraction.lines if ln.statement == statement]
    rows.sort(key=lambda r: (r.display_order, r.page))
    return rows


def _write_table_pair(
    *,
    extraction: FSExtraction,
    statement: str,
    perimeter: str,
    perimeter_cols: List[ColumnHeader],
    out_dir: Path,
) -> Optional[Dict[str, Any]]:
    rows = _lines_for(extraction, statement)
    if not rows:
        return None

    fys = [c.fy for c in perimeter_cols]
    fy_keys = [f"{perimeter}_{fy}" for fy in fys]
    name = f"{statement}__{perimeter}"
    csv_path = out_dir / "tables" / f"{name}.csv"
    json_path = out_dir / "tables" / f"{name}.json"
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    # Filter: keep a row if it carries any value for this perimeter OR it is a
    # structural marker (section_header / total / subtotal) — structural rows
    # give the frontend the as-filed flow even when blank for this perimeter.
    def keep(ln: ExtractedLine) -> bool:
        if ln.row_type in ("section_header", "subtotal", "total"):
            return True
        return any(k in ln.values for k in fy_keys)

    rows = [r for r in rows if keep(r)]
    if not rows:
        return None

    # CSV — human-friendly, FYs as columns, statement-flow order
    csv_header = [
        "display_order", "row_type", "indent_level", "section_path",
        "raw_label", "canonical_code", "note_ref",
        *fys,
        "confidence", "page", "flags",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(csv_header)
        for ln in rows:
            w.writerow([
                ln.display_order,
                ln.row_type,
                ln.indent_level,
                " > ".join(ln.section_path),
                ln.raw_label,
                ln.canonical_code or "",
                ln.note or "",
                *_format_fy_values(ln, fy_keys),
                f"{ln.confidence:.2f}",
                ln.page,
                ";".join(ln.flags),
            ])

    # JSON sidecar — render-shaped for the frontend
    json_rows: List[Dict[str, Any]] = []
    for ln in rows:
        json_rows.append({
            "display_order": ln.display_order,
            "row_type": ln.row_type,
            "indent_level": ln.indent_level,
            "section_path": ln.section_path,
            "label": ln.label or ln.raw_label,
            "raw_label": ln.raw_label,
            "canonical_code": ln.canonical_code,
            "note_ref": ln.note,
            "values": {fy: ln.values.get(f"{perimeter}_{fy}") for fy in fys},
            "page": ln.page,
            "confidence": round(ln.confidence, 3),
            "flags": ln.flags,
        })
    sidecar = {
        "statement": statement,
        "statement_name": STATEMENT_OF.get(statement, statement.upper()),
        "perimeter": perimeter,
        "fys": fys,
        "currency": extraction.currency,
        "rows": json_rows,
    }
    json_path.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False), encoding="utf-8")

    pages = sorted({ln.page for ln in rows if ln.page})
    return {
        "kind": "table",
        "statement": statement,
        "statement_name": STATEMENT_OF.get(statement, statement.upper()),
        "perimeter": perimeter,
        "fys": fys,
        "csv": csv_path.relative_to(out_dir).as_posix(),
        "json": json_path.relative_to(out_dir).as_posix(),
        "row_count": len(rows),
        "pages": pages,
    }


# ---- Narrative writers ---------------------------------------------------------

def _narrative_filename(section: NarrativeSection) -> Tuple[str, str]:
    """Return (folder, basename) for a narrative section."""
    if section.note_no is not None:
        slug = _slug(section.title)
        return "notes", f"note_{section.note_no:02d}_{slug}.md"
    # Non-numbered: directors_statement, auditor_report, other
    return "narrative", f"{section.kind}.md"


def _write_narrative(section: NarrativeSection, out_dir: Path) -> Dict[str, Any]:
    folder, basename = _narrative_filename(section)
    md_path = out_dir / folder / basename
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(section.markdown, encoding="utf-8")
    block: Dict[str, Any] = {
        "kind": "note" if section.note_no is not None else "narrative",
        "subkind": section.kind,
        "title": section.title,
        "md": md_path.relative_to(out_dir).as_posix(),
        "pages": section.pages,
    }
    if section.note_no is not None:
        block["note_no"] = section.note_no
    return block


# ---- Top-level entrypoint -------------------------------------------------------

def write_source_blocks(
    *,
    extraction: FSExtraction,
    narrative_sections: Iterable[NarrativeSection],
    raw_text: str,
    source_pdf: Path,
    case_root: Path,
    parsed_root: Path,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Write all blocks for one source PDF and return the manifest dict.

    Layout: <parsed_root>/<source_id>/{manifest.json, raw.txt, tables/, narrative/, notes/}
    Idempotent: re-running on the same PDF with `force=False` short-circuits when
    a manifest already exists whose content_sha256 matches.
    """
    source_pdf = Path(source_pdf)
    parsed_root = Path(parsed_root)
    case_root = Path(case_root)
    short_id, full_hash = source_id_for(source_pdf)
    out_dir = parsed_root / short_id

    manifest_path = out_dir / "manifest.json"
    if manifest_path.exists() and not force:
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            if existing.get("content_sha256") == full_hash:
                return existing
        except json.JSONDecodeError:
            pass

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "raw.txt").write_text(raw_text or "", encoding="utf-8")

    # Tables — one (statement, perimeter) pair per file
    table_blocks: List[Dict[str, Any]] = []
    by_perimeter = _columns_by_perimeter(extraction.columns)
    for perimeter, cols in by_perimeter.items():
        for statement in ("sofp", "soci", "socf"):
            block = _write_table_pair(
                extraction=extraction,
                statement=statement,
                perimeter=perimeter,
                perimeter_cols=cols,
                out_dir=out_dir,
            )
            if block:
                table_blocks.append(block)

    # Narrative + numbered notes
    narrative_blocks: List[Dict[str, Any]] = []
    for sec in narrative_sections:
        narrative_blocks.append(_write_narrative(sec, out_dir))

    # Path the API uses to stream the original PDF. Prefer a path relative to
    # the case root so the artifact stays portable; fall back to an absolute
    # path for sources outside the case dir (e.g. the demo input/ folder).
    try:
        original_rel = source_pdf.resolve().relative_to(case_root.resolve()).as_posix()
    except ValueError:
        original_rel = source_pdf.resolve().as_posix()

    manifest: Dict[str, Any] = {
        "source_id": short_id,
        "content_sha256": full_hash,
        "original_filename": source_pdf.name,
        "original_path": original_rel,
        "extraction_method": extraction.extraction_method,
        "entity": {
            "name": extraction.entity_name,
            "uen": extraction.uen,
            "framework": extraction.framework,
        },
        "audited": extraction.audited,
        "consolidated": extraction.consolidated,
        "currency": extraction.currency,
        "period_end_primary": extraction.period_end_primary,
        "columns": [
            {"perimeter": c.perimeter, "fy": c.fy, "period_end": c.period_end}
            for c in extraction.columns
        ],
        "blocks": table_blocks + narrative_blocks,
        "review_flags": list(extraction.review_flags),
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return manifest


# ---- Stale-source GC + cross-source merge -------------------------------------

def gc_stale_sources(parsed_root: Path, keep_source_ids: List[str]) -> List[str]:
    """
    Remove any `<source_id>/` directory whose ID isn't in `keep_source_ids`.

    Returns the list of removed source IDs. Files outside `<source_id>/` dirs
    (e.g. `index.json`, the `merged/` block dir) are left in place — they're
    rewritten on every pipeline run.
    """
    if not parsed_root.exists():
        return []
    keep = set(keep_source_ids)
    removed: List[str] = []
    for entry in parsed_root.iterdir():
        if not entry.is_dir():
            continue
        if entry.name == "merged":
            continue
        if entry.name in keep:
            continue
        import shutil as _shutil
        _shutil.rmtree(entry, ignore_errors=True)
        removed.append(entry.name)
    return removed


def _section_for_code(code: str) -> Tuple[str, str]:
    """Two-level section name for a canonical code (parent, child)."""
    if code in SOFP:
        if code.startswith("bs_total_ca") or code in (
            "bs_cash", "bs_trade_recv", "bs_other_recv", "bs_trade_other_recv",
            "bs_prepayments", "bs_inventory", "bs_amt_due_related",
        ):
            return ("Assets", "Current Assets")
        if code in ("bs_total_assets", "bs_net_assets"):
            return ("Assets", "")
        if code.startswith("bs_total_nca") or code in (
            "bs_ppe", "bs_rou_asset", "bs_intangibles",
            "bs_inv_subsidiary", "bs_inv_associate", "bs_deferred_tax_a",
        ):
            return ("Assets", "Non-current Assets")
        if code.startswith("bs_total_cl") or code in (
            "bs_trade_pay", "bs_other_pay", "bs_trade_other_pay",
            "bs_borrowings_st", "bs_lease_liab_st", "bs_current_tax",
        ):
            return ("Liabilities", "Current Liabilities")
        if code in ("bs_total_liab",):
            return ("Liabilities", "")
        if code.startswith("bs_total_ncl") or code in (
            "bs_borrowings_lt", "bs_lease_liab_lt", "bs_deferred_tax_l",
        ):
            return ("Liabilities", "Non-current Liabilities")
        # equity
        return ("Equity", "")
    if code in SOCI:
        return ("Profit & Loss", "")
    if code in SOCF:
        if code in ("cf_operating",) or code.startswith("cf_"):
            if code == "cf_operating":
                return ("Cash Flow", "Operating Activities")
            if code == "cf_investing" or code == "cf_capex":
                return ("Cash Flow", "Investing Activities")
            if code == "cf_financing" or code.startswith("cf_proceeds_") or code.startswith("cf_repay_") or code in ("cf_interest_paid", "cf_dividends_paid"):
                return ("Cash Flow", "Financing Activities")
            return ("Cash Flow", "")
    return ("", "")


def _row_type_for_code(code: str) -> str:
    if code in ("bs_total_assets", "bs_total_liab", "bs_total_equity", "bs_net_assets",
                "pl_pat", "pl_tci", "cf_cash_end"):
        return "total"
    if code in ("bs_total_ca", "bs_total_nca", "bs_total_cl", "bs_total_ncl",
                "pl_gross_profit", "pl_total_expenses", "pl_pbt",
                "cf_operating", "cf_investing", "cf_financing", "cf_net_change_cash"):
        return "subtotal"
    return "line"


def _fy_year(fy: str) -> int:
    if fy and len(fy) >= 6 and fy[2:].isdigit():
        return int(fy[2:])
    return 0


def _pick_cell_for_fy(
    fy: str,
    extractions_for_perimeter: List[Tuple[str, FSExtraction]],
    code: str,
) -> Optional[Dict[str, Any]]:
    """
    Pick the best (value, source_id, page) for one (canonical_code, fy) cell.

    Ranking:
      1. Source whose most-recent FY equals `fy` (the FS that primarily reports it)
      2. Highest line.confidence
      3. Most recent source filing year (tiebreaker)
    """
    candidates: List[Tuple[int, float, int, Dict[str, Any]]] = []
    for source_id, ext in extractions_for_perimeter:
        for line in ext.lines:
            if line.canonical_code != code:
                continue
            col_key = None
            for c in ext.columns:
                if c.fy == fy:
                    col_key = f"{c.perimeter}_{c.fy}"
                    break
            if not col_key or col_key not in line.values:
                continue
            primary_fy_match = 1 if any(
                c.fy == fy and c.perimeter == ext.columns[0].perimeter
                and ext.columns and c.fy == max((cc.fy for cc in ext.columns), key=_fy_year)
                for c in ext.columns
            ) else 0
            candidates.append((
                primary_fy_match,
                float(line.confidence or 0),
                _fy_year(max((c.fy for c in ext.columns), key=_fy_year, default="FY0000")),
                {
                    "value": float(line.values[col_key]),
                    "source_id": source_id,
                    "page": line.page,
                    "label": line.label or line.raw_label,
                    "confidence": float(line.confidence or 0),
                },
            ))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (-x[0], -x[1], -x[2]))
    return candidates[0][3]


def write_merged_blocks(
    *,
    sources: List[Tuple[str, FSExtraction]],
    parsed_root: Path,
) -> List[Dict[str, Any]]:
    """
    For each (statement, perimeter) seen across `sources`, write a merged
    block that fuses cells from all sources, with per-cell provenance.

    Returns the list of merged-block manifest entries (for the rollup index).
    """
    merged_dir = parsed_root / "merged"
    merged_dir.mkdir(parents=True, exist_ok=True)

    perimeters: Dict[str, List[Tuple[str, FSExtraction]]] = {}
    for source_id, ext in sources:
        for col in ext.columns:
            perimeters.setdefault(col.perimeter, [])
            if (source_id, ext) not in perimeters[col.perimeter]:
                perimeters[col.perimeter].append((source_id, ext))

    blocks: List[Dict[str, Any]] = []
    for perimeter, exts in perimeters.items():
        all_fys = sorted({c.fy for _, ext in exts for c in ext.columns if c.perimeter == perimeter},
                         key=_fy_year, reverse=True)
        if not all_fys:
            continue
        for statement, taxonomy in (("sofp", SOFP), ("soci", SOCI), ("socf", SOCF)):
            block = _build_merged_statement(
                statement=statement,
                taxonomy=taxonomy,
                perimeter=perimeter,
                fys=all_fys,
                exts=exts,
            )
            if not block["rows"]:
                continue
            name = f"{statement}__{perimeter}"
            json_path = merged_dir / f"{name}.json"
            csv_path = merged_dir / f"{name}.csv"
            json_path.write_text(
                json.dumps(block, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            # CSV: FYs as columns, provenance as a trailing "source" column listing per-cell source ids
            with csv_path.open("w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow([
                    "section", "subsection", "row_type",
                    "canonical_code", "label",
                    *all_fys,
                    "sources",
                ])
                for r in block["rows"]:
                    w.writerow([
                        r["section_path"][0] if r["section_path"] else "",
                        r["section_path"][1] if len(r["section_path"]) > 1 else "",
                        r["row_type"],
                        r["canonical_code"],
                        r["label"],
                        *[_fmt_cell(r["values"].get(fy)) for fy in all_fys],
                        ",".join(sorted({
                            r["provenance"].get(fy, {}).get("source_id", "")
                            for fy in all_fys if r["provenance"].get(fy)
                        })),
                    ])
            blocks.append({
                "kind": "merged_table",
                "statement": statement,
                "statement_name": STATEMENT_OF.get(statement, statement.upper()),
                "perimeter": perimeter,
                "fys": all_fys,
                "csv": f"merged/{name}.csv",
                "json": f"merged/{name}.json",
                "row_count": len(block["rows"]),
                "source_ids": sorted({source_id for source_id, _ in exts}),
            })
    return blocks


def _build_merged_statement(
    *,
    statement: str,
    taxonomy: Dict[str, str],
    perimeter: str,
    fys: List[str],
    exts: List[Tuple[str, FSExtraction]],
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for display_order, (code, default_label) in enumerate(taxonomy.items()):
        values: Dict[str, Optional[float]] = {}
        provenance: Dict[str, Dict[str, Any]] = {}
        chosen_label: Optional[str] = None
        for fy in fys:
            cell = _pick_cell_for_fy(fy, exts, code)
            if cell is None:
                values[fy] = None
                continue
            values[fy] = cell["value"]
            provenance[fy] = {
                "source_id": cell["source_id"],
                "page": cell["page"],
                "confidence": cell["confidence"],
            }
            chosen_label = chosen_label or cell.get("label")
        if not any(v is not None for v in values.values()):
            continue
        parent, child = _section_for_code(code)
        section_path = [p for p in (parent, child) if p]
        rows.append({
            "display_order": display_order,
            "canonical_code": code,
            "label": chosen_label or default_label,
            "row_type": _row_type_for_code(code),
            "section_path": section_path,
            "indent_level": 0 if _row_type_for_code(code) == "total" else (
                1 if _row_type_for_code(code) == "subtotal" else max(1, len(section_path))
            ),
            "values": values,
            "provenance": provenance,
        })
    return {
        "statement": statement,
        "statement_name": STATEMENT_OF.get(statement, statement.upper()),
        "perimeter": perimeter,
        "fys": fys,
        "rows": rows,
    }


def _fmt_cell(v: Optional[float]) -> str:
    if v is None:
        return ""
    if float(v).is_integer():
        return str(int(v))
    return f"{v:.2f}"
