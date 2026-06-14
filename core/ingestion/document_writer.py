"""
SG FS document writer.

Compose a single `document.json` per source PDF that conforms to
`schemas/sg_fs_document_schema.json` — the structural truth of one filing
in block order (directors' statement → auditor's report → SoFP → SoCI →
SoCE → SoCF → notes), with each statement carrying its full row spread.

Inputs are the artefacts the existing pipeline already produces:
    - `FSExtraction`  (statement rows + columns + entity meta)
    - `NarrativeSection[]` (auditor / directors / notes with markdown)
    - source PDF path + content-hash source_id

Output sits next to the existing `tables/`, `narrative/`, `notes/` block
bundles — same source-id directory, additional artefact.

This module does NOT extract anything new from PDFs. It only re-shapes
already-extracted data into the schema's block-ordered form.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .canonical_map import STATEMENT_OF
from .fs_text_extract import ColumnHeader, ExtractedLine, FSExtraction
from .narrative_extract import NarrativeSection


_YEAR_RE = re.compile(r"\b(20\d{2})\b")


# ---- helpers ------------------------------------------------------------------

def _derive_fy(fye: str) -> str:
    """'FY2024' from a free-text FYE like '31 December 2024'."""
    if not fye:
        return ""
    m = _YEAR_RE.search(fye)
    return f"FY{m.group(1)}" if m else ""


def _narrative_kind_to_block_kind(kind: str) -> Optional[str]:
    """Top-level NarrativeBlock kinds the schema accepts (not numbered notes)."""
    return {
        "directors_statement": "directors_statement",
        "auditor_report":      "auditor_report",
        "cover":               "cover",
        "corporate_info_page": "corporate_info",  # reserved — extractor doesn't emit yet
    }.get(kind)


def _narrative_kind_to_note_subkind(kind: str) -> str:
    return {
        "corporate_info": "corporate_info",
        "policies":       "policies",
        "note":           "note",
    }.get(kind, "note")


def _row_to_dict(line: ExtractedLine) -> Dict[str, Any]:
    """Serialise one ExtractedLine into the schema's StatementRow shape."""
    out: Dict[str, Any] = {
        "row_type":     line.row_type,
        "label":        line.label or line.raw_label,
        "indent_level": int(line.indent_level),
    }
    if line.raw_label and line.raw_label != (line.label or ""):
        out["raw_label"] = line.raw_label
    if line.canonical_code is not None:
        out["canonical_code"] = line.canonical_code
    if line.section_path:
        out["section_path"] = list(line.section_path)
    if line.values:
        out["values"] = {k: (None if v is None else float(v)) for k, v in line.values.items()}
    if line.note:
        out["note_ref"] = line.note
    out["display_order"] = int(line.display_order)
    if line.page:
        out["page"] = int(line.page)
    if line.confidence is not None:
        out["confidence"] = round(float(line.confidence), 3)
    if line.flags:
        out["flags"] = list(line.flags)
    return out


def _build_statement_block(ext: FSExtraction, statement: str) -> Optional[Dict[str, Any]]:
    rows = [ln for ln in ext.lines if ln.statement == statement]
    if not rows:
        return None
    pages = [r.page for r in rows if r.page]
    page_range = [min(pages), max(pages)] if pages else [1, 1]

    columns: List[Dict[str, Any]] = []
    for c in ext.columns:
        col: Dict[str, Any] = {
            "id":        f"{c.perimeter}_{c.fy}",
            "perimeter": c.perimeter,
            "fy":        c.fy,
        }
        if c.period_end:
            col["period_end"] = c.period_end
        if ext.currency:
            col["currency"] = ext.currency
        columns.append(col)

    rows_sorted = sorted(rows, key=lambda r: (r.display_order, r.page))
    return {
        "kind":       "statement",
        "type":       statement,
        "title":      STATEMENT_OF.get(statement, statement.upper()),
        "page_range": page_range,
        "columns":    columns,
        "rows":       [_row_to_dict(r) for r in rows_sorted],
    }


def _build_narrative_blocks(narratives: List[NarrativeSection]) -> List[Dict[str, Any]]:
    """Top-level narrative blocks — excludes numbered notes (those go in NotesBlock)."""
    blocks: List[Dict[str, Any]] = []
    for sec in narratives:
        if sec.note_no is not None:
            continue
        block_kind = _narrative_kind_to_block_kind(sec.kind)
        if not block_kind:
            continue
        pages = sec.pages or [1]
        block: Dict[str, Any] = {
            "kind":       block_kind,
            "page_range": [min(pages), max(pages)],
            "markdown":   (sec.markdown or "").rstrip() + "\n",
        }
        if sec.title:
            block["title"] = sec.title
        blocks.append(block)
    return blocks


def _build_notes_block(narratives: List[NarrativeSection]) -> Optional[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for sec in narratives:
        if sec.note_no is None:
            continue
        pages = sec.pages or [1]
        item: Dict[str, Any] = {
            "no":         sec.note_no,
            "title":      sec.title,
            "page_range": [min(pages), max(pages)],
            "subkind":    _narrative_kind_to_note_subkind(sec.kind),
        }
        if sec.markdown:
            item["markdown"] = sec.markdown.rstrip() + "\n"
        items.append(item)

    if not items:
        return None

    starts = [it["page_range"][0] for it in items]
    ends   = [it["page_range"][1] for it in items]
    return {
        "kind":       "notes",
        "page_range": [min(starts), max(ends)],
        "items":      items,
    }


# ---- top-level entrypoint -----------------------------------------------------

def build_document(
    *,
    extraction: FSExtraction,
    narrative_sections: List[NarrativeSection],
    source_pdf: Path,
    source_id: str,
    entity_extras: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Compose a schema-compliant SG FS document from already-extracted artefacts.

    `entity_extras` may carry ACRA-derived fields (`ssic_code`,
    `ssic_description`, `incorporation_country`) that the FS extraction itself
    doesn't see — the pipeline passes them in from the merged corporate profile.
    """
    entity: Dict[str, Any] = {"name": extraction.entity_name or ""}
    if extraction.uen:
        entity["uen"] = extraction.uen
    if entity_extras:
        for k, v in entity_extras.items():
            if v not in (None, ""):
                entity[k] = v
    # The schema requires entity.name to be non-empty. If the FS extractor
    # couldn't read it from the cover and the caller hasn't supplied one
    # via entity_extras, fall back to the source PDF stem so we still
    # produce a valid document for downstream review.
    if not entity.get("name"):
        entity["name"] = Path(source_pdf).stem.replace("_", " ").strip() or "Unknown"

    doc_meta: Dict[str, Any] = {
        "entity":            entity,
        "fye":               extraction.period_end_primary or "",
        "fy":                _derive_fy(extraction.period_end_primary or ""),
        "framework":         extraction.framework or "SFRS(I)",
        "audited":           bool(extraction.audited),
        "consolidated":      bool(extraction.consolidated),
        "currency":          extraction.currency or "SGD",
        "source_pdf":        Path(source_pdf).name,
        "source_id":         source_id,
        "extraction_method": extraction.extraction_method or "text",
    }

    blocks: List[Dict[str, Any]] = []
    blocks.extend(_build_narrative_blocks(narrative_sections))
    for stmt in ("sofp", "soci", "soce", "socf"):
        b = _build_statement_block(extraction, stmt)
        if b:
            blocks.append(b)
    notes_block = _build_notes_block(narrative_sections)
    if notes_block:
        blocks.append(notes_block)

    # Sort to PDF order — earliest first page wins. Blocks without a
    # known page (defensive) sink to the end.
    def _first_page(b: Dict[str, Any]) -> int:
        pr = b.get("page_range")
        if isinstance(pr, list) and pr:
            return int(pr[0])
        return 10_000_000

    blocks.sort(key=_first_page)

    return {"document": doc_meta, "blocks": blocks}


# ---- single-call facade --------------------------------------------------------

def parse_fs_pdf_to_document(
    pdf_path: Path,
    *,
    entity_extras: Optional[Dict[str, Any]] = None,
    strategy: str = "agentic",
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Render PDF → vision-LLM → schema-conformant document dict.

    POC default: ``strategy="agentic"`` runs the vision-LLM extractor
    (`agentic_extract.extract_document_via_llm`). The classifier still gates
    the file type — only FS PDFs (z124 / UFS) are accepted.

    `entity_extras` overlays document.entity after extraction so case-level
    facts from the merged ACRA profile (canonical name, SSIC) take precedence
    over what the model parsed off the cover.

    Raises:
        FileNotFoundError if `pdf_path` is missing.
        ValueError        if the file isn't an FS PDF the schema knows about.
        RuntimeError      if the LLM extractor fails (no tool call returned).
    """
    from .classifier import classify_file
    from .block_writer import source_id_for
    from .agentic_extract import extract_document_via_llm

    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    cf = classify_file(pdf_path)
    if cf.source_type not in ("fs_xbrl_render", "fs_ufs"):
        raise ValueError(
            f"{pdf_path.name} classified as {cf.source_type!r}; "
            f"parse_fs_pdf_to_document expects fs_xbrl_render or fs_ufs."
        )

    if strategy != "agentic":
        raise ValueError(
            f"Strategy {strategy!r} is not implemented in this POC. "
            f"Only 'agentic' is supported."
        )

    source_id, _ = source_id_for(pdf_path)
    doc = extract_document_via_llm(
        pdf_path,
        source_id=source_id,
        model=model,
    )

    if entity_extras:
        entity = doc.setdefault("document", {}).setdefault("entity", {})
        for k, v in entity_extras.items():
            if v not in (None, ""):
                entity[k] = v

    return doc


# ---- Adapter: document dict → FSExtraction + NarrativeSection[] ---------------

def _doc_pages(pr: Any) -> List[int]:
    if isinstance(pr, list) and len(pr) == 2 and all(isinstance(x, int) for x in pr):
        a, b = pr
        return list(range(a, b + 1))
    if isinstance(pr, list) and len(pr) == 1 and isinstance(pr[0], int):
        return [pr[0]]
    return [1]


def document_to_fs_extraction(doc: Dict[str, Any], source_pdf: Path) -> FSExtraction:
    """
    Re-derive an `FSExtraction` from an agentic document dict so the existing
    `block_writer` (per-source CSV + JSON sidecars) keeps working unchanged.

    The conversion is loss-less for the table layer: every statement-block
    row becomes an `ExtractedLine` with the same canonical_code, section_path,
    row_type, values, etc. Statement columns are deduplicated across blocks
    (a row that appears in both SoFP and SoCI shares the same column ids).
    """
    d = doc.get("document", {}) or {}
    ent = d.get("entity", {}) or {}
    ext = FSExtraction(
        source_file=str(source_pdf),
        entity_name=ent.get("name", "") or "",
        uen=ent.get("uen", "") or "",
        framework=d.get("framework", "SFRS"),
        currency=d.get("currency", "SGD"),
        audited=bool(d.get("audited", False)),
        consolidated=bool(d.get("consolidated", False)),
        period_end_primary=d.get("fye", "") or "",
        extraction_method=d.get("extraction_method", "agentic"),
    )

    seen_col_keys: set = set()
    order_per_stmt: Dict[str, int] = {}

    for block in doc.get("blocks", []) or []:
        if block.get("kind") != "statement":
            continue
        for c in block.get("columns", []) or []:
            perimeter = c.get("perimeter") or "company"
            fy = c.get("fy") or ""
            key = (perimeter, fy)
            if key in seen_col_keys or not fy:
                continue
            seen_col_keys.add(key)
            ext.columns.append(ColumnHeader(
                perimeter=perimeter, fy=fy,
                period_end=c.get("period_end"),
            ))
        statement = block.get("type") or ""
        for row in block.get("rows", []) or []:
            order = order_per_stmt.get(statement, 0)
            order_per_stmt[statement] = order + 1
            values_in = row.get("values") or {}
            values: Dict[str, float] = {}
            for k, v in values_in.items():
                if v is None:
                    continue
                try:
                    values[k] = float(v)
                except (TypeError, ValueError):
                    continue
            ext.lines.append(ExtractedLine(
                raw_label=row.get("raw_label") or row.get("label") or "",
                canonical_code=row.get("canonical_code"),
                label=row.get("label") or row.get("raw_label") or "",
                statement=statement,
                values=values,
                note=row.get("note_ref"),
                page=int(row.get("page") or 0),
                display_order=int(row.get("display_order", order)),
                section_path=list(row.get("section_path") or []),
                indent_level=int(row.get("indent_level", 0)),
                row_type=row.get("row_type", "line"),
                confidence=float(row.get("confidence", 1.0)),
                flags=list(row.get("flags") or []),
            ))
    return ext


def document_to_narrative_sections(doc: Dict[str, Any]) -> List[NarrativeSection]:
    """
    Re-derive a `NarrativeSection` list from an agentic document dict.

    Top-level narrative blocks (cover, corporate_info, directors_statement,
    auditor_report) map to non-numbered sections. NotesBlock items map to
    numbered sections — their `subkind` becomes the NarrativeSection.kind so
    the writer routes them under `notes/` not `narrative/`.
    """
    narratives: List[NarrativeSection] = []
    for block in doc.get("blocks", []) or []:
        kind = block.get("kind")
        if kind in ("cover", "corporate_info", "directors_statement", "auditor_report"):
            narratives.append(NarrativeSection(
                kind=kind,
                title=block.get("title") or kind.replace("_", " ").title(),
                pages=_doc_pages(block.get("page_range")),
                markdown=block.get("markdown") or "",
            ))
        elif kind == "notes":
            for item in block.get("items") or []:
                no = item.get("no")
                narratives.append(NarrativeSection(
                    kind=item.get("subkind") or "note",
                    title=item.get("title") or "",
                    note_no=no,
                    pages=_doc_pages(item.get("page_range")),
                    markdown=item.get("markdown") or "",
                ))
    return narratives


# ---- schema validation --------------------------------------------------------

_SCHEMA_CACHE: Optional[Dict[str, Any]] = None


def _load_schema() -> Dict[str, Any]:
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is not None:
        return _SCHEMA_CACHE
    schema_path = Path(__file__).resolve().parents[2] / "schemas" / "sg_fs_document_schema.json"
    _SCHEMA_CACHE = json.loads(schema_path.read_text(encoding="utf-8"))
    return _SCHEMA_CACHE


def validate_document(doc: Dict[str, Any]) -> List[str]:
    """Return readable validation errors against `sg_fs_document_schema.json`."""
    from jsonschema import Draft202012Validator
    v = Draft202012Validator(_load_schema())
    out: List[str] = []
    for e in v.iter_errors(doc):
        path = ".".join(str(p) for p in e.absolute_path) or "<root>"
        out.append(f"{path}: {e.message[:240]}")
    return out


def write_document_json(doc: Dict[str, Any], out_path: Path) -> Path:
    """Persist the document at `out_path`. Parent dirs are created."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path
