"""Agentic financial-document output -> wiki-specific relationship artifact.

The vision LLM already reads every PDF page and emits `document.json`. This
module creates a second, wiki-oriented artifact from that same agentic output:
`wiki_document.json`. It preserves semantic blocks, page maps, and explicit
statement-row-to-note relationships for dashboard drill-down and Co-Pilot RAG.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


_NOTE_TOKEN_RE = re.compile(r"[^a-z0-9.]+")


def _note_key(value: Any) -> str:
    raw = str(value or "").strip().lower()
    raw = raw.replace("note", "").strip()
    raw = _NOTE_TOKEN_RE.sub("", raw)
    if raw.endswith(".0"):
        raw = raw[:-2]
    return raw


def _page_range(value: Any) -> Optional[List[int]]:
    if not isinstance(value, list) or not value:
        return None
    try:
        nums = [int(v) for v in value if v is not None]
    except (TypeError, ValueError):
        return None
    if not nums:
        return None
    if len(nums) == 1:
        return [nums[0], nums[0]]
    return [min(nums[0], nums[1]), max(nums[0], nums[1])]


def build_agentic_wiki_document(
    document: Dict[str, Any],
    *,
    source_id: str,
    source_file: str,
) -> Dict[str, Any]:
    """Build a wiki-specific artifact from one source `document.json`."""
    notes = _collect_notes(document)
    notes_by_key = {n["note_key"]: n for n in notes if n.get("note_key")}

    statements: List[Dict[str, Any]] = []
    note_links: List[Dict[str, Any]] = []
    pages: Dict[int, Dict[str, Any]] = {}

    def add_pages(page_range: Optional[List[int]], kind: str, title: str, text: str) -> None:
        if not page_range:
            return
        for page_no in range(page_range[0], page_range[1] + 1):
            page = pages.setdefault(page_no, {
                "page": page_no,
                "source_id": source_id,
                "source_file": source_file,
                "sections": [],
            })
            page["sections"].append({
                "kind": kind,
                "title": title,
                "text": text[:4000],
            })

    for block_idx, block in enumerate(document.get("blocks") or []):
        kind = block.get("kind")
        title = block.get("title") or block.get("type") or kind or f"Block {block_idx + 1}"
        pr = _page_range(block.get("page_range"))

        if kind == "statement":
            statement = block.get("type") or ""
            rows_out = []
            row_labels = []
            for row_idx, row in enumerate(block.get("rows") or []):
                row_id = f"{source_id}:{statement}:{row_idx}"
                note_ref = row.get("note_ref")
                note = notes_by_key.get(_note_key(note_ref)) if note_ref else None
                row_out = {
                    "row_id": row_id,
                    "statement": statement,
                    "statement_title": title,
                    "row_index": row_idx,
                    "label": row.get("label") or row.get("raw_label") or row.get("canonical_code") or "",
                    "canonical_code": row.get("canonical_code"),
                    "note_ref": note_ref,
                    "note_key": _note_key(note_ref) if note_ref else None,
                    "note_id": note.get("note_id") if note else None,
                    "page": row.get("page"),
                    "values": row.get("values") or {},
                }
                rows_out.append(row_out)
                row_labels.append(row_out["label"])
                if note:
                    note_links.append({
                        "source_id": source_id,
                        "source_file": source_file,
                        "statement": statement,
                        "statement_title": title,
                        "row_id": row_id,
                        "row_label": row_out["label"],
                        "canonical_code": row_out["canonical_code"],
                        "row_page": row_out["page"],
                        "note_ref": note_ref,
                        "note_key": note["note_key"],
                        "note_id": note["note_id"],
                        "note_no": note.get("note_no"),
                        "note_title": note.get("title"),
                        "note_page_range": note.get("page_range"),
                        "note_wiki_path": note.get("wiki_path"),
                    })
            statements.append({
                "statement": statement,
                "title": title,
                "page_range": pr,
                "rows": rows_out,
            })
            add_pages(pr, "statement", title, "\n".join(row_labels))
        elif kind == "notes":
            for note in notes:
                add_pages(note.get("page_range"), "note", note.get("title") or "", note.get("markdown") or "")
        else:
            add_pages(pr, kind or "narrative", title, block.get("markdown") or "")

    return {
        "source_id": source_id,
        "source_file": source_file,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "generated_from": "agentic_document_json",
        "notes": notes,
        "statements": statements,
        "note_links": note_links,
        "pages": [pages[k] for k in sorted(pages)],
    }


def _collect_notes(document: Dict[str, Any]) -> List[Dict[str, Any]]:
    notes: List[Dict[str, Any]] = []
    for block in document.get("blocks") or []:
        if block.get("kind") != "notes":
            continue
        for idx, item in enumerate(block.get("items") or []):
            note_no = item.get("note_no", item.get("no"))
            title = item.get("title") or f"Note {note_no or idx + 1}"
            key = _note_key(note_no or title)
            note_id = f"note-{key or idx + 1}"
            notes.append({
                "note_id": note_id,
                "note_no": note_no,
                "note_key": key,
                "title": title,
                "page_range": _page_range(item.get("page_range")),
                "subkind": item.get("subkind"),
                "markdown": item.get("markdown") or "",
                "tables": item.get("tables") or [],
                "wiki_path": None,
            })
    return notes


def write_agentic_wiki_document(
    document: Dict[str, Any],
    out_path: Path,
    *,
    source_id: str,
    source_file: str,
) -> Dict[str, Any]:
    artifact = build_agentic_wiki_document(
        document,
        source_id=source_id,
        source_file=source_file,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
    return artifact
