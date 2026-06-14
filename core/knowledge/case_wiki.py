"""Build a file-backed case wiki from parsed financial-statement artefacts.

The wiki is the Phase 1 knowledge-base layer. It turns the existing
`document.json`, source manifests, and merged statement blocks into stable
Markdown pages plus machine-readable chunk/evidence indexes. Later phases can
swap the search backend for pgvector without changing the upstream ingestion
contract.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


_SLUG_RE = re.compile(r"[^a-z0-9]+")
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./%-]*")
_NOTE_TOKEN_RE = re.compile(r"[^a-z0-9.]+")


def _slug(text: str, max_len: int = 80) -> str:
    slug = _SLUG_RE.sub("-", (text or "").lower()).strip("-")
    return (slug[:max_len].strip("-") or "untitled")


def _note_key(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("note", "").strip()
    raw = _NOTE_TOKEN_RE.sub("", raw)
    if raw.endswith(".0"):
        raw = raw[:-2]
    return raw


def _related_rows_for_note(wiki_doc: Dict[str, Any], note_key: str) -> List[Dict[str, Any]]:
    if not note_key:
        return []
    return [
        link for link in (wiki_doc.get("note_links") or [])
        if str(link.get("note_key") or "") == note_key
    ]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _frontmatter(meta: Dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in meta.items():
        if value is None or value == [] or value == {}:
            continue
        if isinstance(value, str):
            safe = value.replace('"', '\\"')
            lines.append(f'{key}: "{safe}"')
        else:
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def _plain_text(markdown: str) -> str:
    return re.sub(r"\s+", " ", markdown.replace("|", " ")).strip()


def _normalise_page_range(value: Any) -> Optional[List[int]]:
    if not isinstance(value, list) or not value:
        return None
    try:
        nums = [int(x) for x in value if x is not None]
    except (TypeError, ValueError):
        return None
    if not nums:
        return None
    if len(nums) == 1:
        return [nums[0], nums[0]]
    return [min(nums[0], nums[1]), max(nums[0], nums[1])]


def _extract_pdf_pages(pdf_path: Path) -> List[Dict[str, Any]]:
    """Extract page text for the knowledge lane.

    This deliberately lives outside the financial-table parser. The dashboard
    can stay focused on reviewed entries, while the knowledge base indexes the
    whole PDF page by page for analyst questions.
    """
    try:
        import pdfplumber
    except ImportError:
        return []

    pages: List[Dict[str, Any]] = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                pages.append({
                    "page": i,
                    "text": page.extract_text() or "",
                    "method": "pdfplumber",
                })
    except Exception:
        return []
    return pages


def _topic_tokens(text: str, limit: int = 12) -> List[str]:
    stop = {
        "the", "and", "for", "with", "from", "that", "this", "statement",
        "financial", "page", "pages", "source", "note", "total",
    }
    seen: List[str] = []
    for token in _TOKEN_RE.findall(text.lower()):
        if len(token) < 3 or token in stop or token.isdigit():
            continue
        if token not in seen:
            seen.append(token)
        if len(seen) >= limit:
            break
    return seen


class CaseWikiBuilder:
    def __init__(self, case_root: Path):
        self.case_root = Path(case_root)
        self.parsed_root = self.case_root / "parsed" / "financials"
        self.wiki_root = self.case_root / "wiki"
        self.pages: List[Dict[str, Any]] = []
        self.chunks: List[Dict[str, Any]] = []
        self.evidence: List[Dict[str, Any]] = []
        self.note_links: List[Dict[str, Any]] = []

    def build(self) -> Dict[str, Any]:
        self.wiki_root.mkdir(parents=True, exist_ok=True)
        (self.wiki_root / "sources").mkdir(parents=True, exist_ok=True)
        (self.wiki_root / "statements").mkdir(parents=True, exist_ok=True)
        (self.wiki_root / "notes").mkdir(parents=True, exist_ok=True)
        (self.wiki_root / "narrative").mkdir(parents=True, exist_ok=True)
        (self.wiki_root / "analytics").mkdir(parents=True, exist_ok=True)
        (self.wiki_root / "report").mkdir(parents=True, exist_ok=True)

        index = _read_json(self.parsed_root / "index.json", {})
        sources = index.get("sources") or []
        for source in sources:
            self._add_source(source)

        self._add_merged_statements(index.get("blocks") or [])
        self._add_fs_analytics()
        self._add_credit_memo()
        self._write_index_page(index)

        _write_jsonl(self.wiki_root / "chunks.jsonl", self.chunks)
        _write_jsonl(self.wiki_root / "evidence.jsonl", self.evidence)
        _write_jsonl(self.wiki_root / "note_links.jsonl", self.note_links)
        (self.wiki_root / "note_links.json").write_text(
            json.dumps({"links": self.note_links}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        summary = {
            "generated_at": _utc_now().isoformat().replace("+00:00", "Z"),
            "page_count": len(self.pages),
            "chunk_count": len(self.chunks),
            "evidence_count": len(self.evidence),
            "note_link_count": len(self.note_links),
            "wiki_root": str(self.wiki_root),
            "pages": self.pages,
        }
        (self.wiki_root / "manifest.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return summary

    def _record_page(
        self,
        *,
        rel_path: str,
        title: str,
        doc_type: str,
        body: str,
        source_id: Optional[str] = None,
        source_file: Optional[str] = None,
        page_range: Optional[List[int]] = None,
        topics: Optional[List[str]] = None,
        evidence_ids: Optional[List[str]] = None,
        extra_meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        rel_path = rel_path.replace("\\", "/")
        path = self.wiki_root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        meta = {
            "doc_type": doc_type,
            "title": title,
            "source_id": source_id,
            "source_file": source_file,
            "page_range": page_range,
            "topics": topics or _topic_tokens(title + " " + body),
            "evidence_ids": evidence_ids or [],
            "last_updated": _utc_now().date().isoformat(),
        }
        if extra_meta:
            meta.update(extra_meta)
        path.write_text(_frontmatter(meta) + body.rstrip() + "\n", encoding="utf-8")

        page = {
            "path": rel_path,
            "title": title,
            "doc_type": doc_type,
            "source_id": source_id,
            "source_file": source_file,
            "page_range": page_range,
            "topics": meta["topics"],
            "evidence_ids": evidence_ids or [],
        }
        self.pages.append(page)
        self.chunks.append({
            "chunk_id": f"chunk_{len(self.chunks) + 1:05d}",
            **page,
            "text": _plain_text(f"{title}\n\n{body}")[:8000],
        })

    def _add_evidence(self, evidence: Dict[str, Any]) -> str:
        evidence_id = evidence.get("evidence_id") or f"ev_{len(self.evidence) + 1:06d}"
        evidence["evidence_id"] = evidence_id
        self.evidence.append(evidence)
        return evidence_id

    def _add_source(self, source: Dict[str, Any]) -> None:
        source_id = source.get("source_id")
        if not source_id:
            return
        source_dir = self.parsed_root / source_id
        manifest = _read_json(source_dir / "manifest.json", {})
        document_rel = source.get("document_json") or "document.json"
        document = _read_json(source_dir / Path(document_rel).name, {})
        wiki_rel = source.get("wiki_document") or "wiki_document.json"
        wiki_doc = _read_json(source_dir / Path(wiki_rel).name, {})

        source_file = manifest.get("original_filename") or source.get("original_filename")
        source_pdf = self._resolve_original_path(manifest)
        title = f"Source: {source_file or source_id}"
        entity = manifest.get("entity") or {}
        body = [
            f"# {title}",
            "",
            f"- Source ID: `{source_id}`",
            f"- Entity: {entity.get('name') or 'Unknown'}",
            f"- Framework: {entity.get('framework') or 'Unknown'}",
            f"- Currency: {manifest.get('currency') or 'Unknown'}",
            f"- Audited: {manifest.get('audited')}",
            f"- Consolidated: {manifest.get('consolidated')}",
            "",
            "## Blocks",
        ]
        for block in document.get("blocks") or []:
            block_title = block.get("title") or block.get("type") or block.get("kind")
            page_range = block.get("page_range") or []
            body.append(f"- {block.get('kind')}: {block_title} pages {page_range}")
        self._record_page(
            rel_path=f"sources/{source_id}/overview.md",
            title=title,
            doc_type="source_overview",
            body="\n".join(body),
            source_id=source_id,
            source_file=source_file,
        )

        for i, block in enumerate(document.get("blocks") or [], start=1):
            self._add_document_block(source_id, source_file, i, block, wiki_doc)

        # Agentic extraction is the primary KB lane: it has already seen the
        # rendered PDF pages, including OCR/image-only filings, and emits
        # semantic blocks with page ranges. The page map below makes that
        # output queryable by page without relying on text-layer extraction.
        self._add_agentic_page_map(source_id, source_file, document, wiki_doc)
        self._add_note_links(source_id, wiki_doc)

        # Text-layer extraction is useful only as a cheap supplement for native
        # PDFs. It is deliberately marked as fallback so it does not become the
        # source of truth for OCR/image-only documents.
        raw_text = (source_dir / "raw.txt").read_text(encoding="utf-8") if (source_dir / "raw.txt").exists() else ""
        if raw_text.strip():
            self._record_page(
                rel_path=f"sources/{source_id}/raw-text.md",
                title=f"Raw text fallback: {source_file or source_id}",
                doc_type="source_text_fallback",
                body=f"# Raw text fallback\n\n```text\n{raw_text.strip()[:50000]}\n```",
                source_id=source_id,
                source_file=source_file,
                extra_meta={"kb_role": "fallback"},
            )

        self._add_source_pages(source_id, source_file, source_pdf)

    def _resolve_original_path(self, manifest: Dict[str, Any]) -> Optional[Path]:
        rel = manifest.get("original_path")
        if not rel:
            return None
        candidate = Path(rel)
        path = candidate if candidate.is_absolute() else (self.case_root / candidate)
        path = path.resolve()
        if path.exists() and path.is_file():
            return path
        return None

    def _add_source_pages(
        self,
        source_id: str,
        source_file: Optional[str],
        source_pdf: Optional[Path],
    ) -> None:
        if not source_pdf or source_pdf.suffix.lower() != ".pdf":
            return
        for page in _extract_pdf_pages(source_pdf):
            page_no = page["page"]
            text = page["text"].strip()
            evidence_id = self._add_evidence({
                "kind": "source_page",
                "source_id": source_id,
                "source_file": source_file,
                "page": page_no,
                "char_count": len(text),
                "extraction_method": page.get("method", "pdfplumber"),
            })
            title = f"{source_file or source_id} - page {page_no}"
            body = [
                f"# {title}",
                "",
                f"Source ID: `{source_id}`",
                f"Page: `{page_no}`",
                "",
                "```text",
                text or "[No text extracted from this page. It may require OCR or vision review.]",
                "```",
            ]
            self._record_page(
                rel_path=f"sources/{source_id}/page-{page_no:03d}.md",
                title=title,
                doc_type="source_page",
                body="\n".join(body),
                source_id=source_id,
                source_file=source_file,
                page_range=[page_no, page_no],
                evidence_ids=[evidence_id],
                extra_meta={
                    "page": page_no,
                    "extraction_method": page.get("method", "pdfplumber"),
                    "requires_ocr": not bool(text),
                    "kb_role": "fallback",
                },
            )

    def _add_agentic_page_map(
        self,
        source_id: str,
        source_file: Optional[str],
        document: Dict[str, Any],
        wiki_doc: Optional[Dict[str, Any]] = None,
    ) -> None:
        if wiki_doc and wiki_doc.get("pages"):
            for page in wiki_doc.get("pages") or []:
                page_no = int(page.get("page") or 0)
                if page_no <= 0:
                    continue
                sections = page.get("sections") or []
                joined = "\n\n---\n\n".join(
                    f"## {section.get('title') or section.get('kind')}\n\n{section.get('text') or ''}"
                    for section in sections
                    if (section.get("text") or "").strip()
                )
                if not joined.strip():
                    continue
                evidence_id = self._add_evidence({
                    "kind": "agentic_source_page",
                    "source_id": source_id,
                    "source_file": source_file,
                    "page": page_no,
                    "extraction_method": "agentic_wiki_document",
                })
                title = f"Agentic page knowledge: {source_file or source_id} page {page_no}"
                body = [
                    f"# {title}",
                    "",
                    "This page is built from `wiki_document.json`.",
                    "",
                    joined,
                ]
                self._record_page(
                    rel_path=f"sources/{source_id}/agentic-page-{page_no:03d}.md",
                    title=title,
                    doc_type="agentic_source_page",
                    body="\n".join(body),
                    source_id=source_id,
                    source_file=source_file,
                    page_range=[page_no, page_no],
                    evidence_ids=[evidence_id],
                    extra_meta={
                        "page": page_no,
                        "extraction_method": "agentic_wiki_document",
                        "kb_role": "primary",
                    },
                )
            return

        pages: Dict[int, List[str]] = {}
        for block in document.get("blocks") or []:
            page_range = _normalise_page_range(block.get("page_range"))
            if block.get("kind") == "notes":
                for item in block.get("items") or []:
                    item_pages = _normalise_page_range(item.get("page_range")) or page_range
                    if not item_pages:
                        continue
                    title = item.get("title") or f"Note {item.get('note_no') or ''}".strip()
                    text = f"Note {item.get('note_no') or ''}: {title}\n\n{item.get('markdown') or ''}".strip()
                    for page_no in range(item_pages[0], item_pages[1] + 1):
                        pages.setdefault(page_no, []).append(text)
                continue
            if not page_range:
                continue
            title = block.get("title") or block.get("type") or block.get("kind")
            if block.get("kind") == "statement":
                row_labels = [
                    r.get("label") or r.get("raw_label") or r.get("canonical_code") or ""
                    for r in block.get("rows") or []
                ]
                text = f"{title}\n\n" + "\n".join(x for x in row_labels if x)
            else:
                text = f"{title}\n\n{block.get('markdown') or ''}".strip()
            for page_no in range(page_range[0], page_range[1] + 1):
                pages.setdefault(page_no, []).append(text)

        for page_no, snippets in sorted(pages.items()):
            joined = "\n\n---\n\n".join(s for s in snippets if s.strip())
            if not joined.strip():
                continue
            evidence_id = self._add_evidence({
                "kind": "agentic_source_page",
                "source_id": source_id,
                "source_file": source_file,
                "page": page_no,
                "extraction_method": "agentic_vision_document",
            })
            title = f"Agentic page knowledge: {source_file or source_id} page {page_no}"
            body = [
                f"# {title}",
                "",
                "This page is built from the agentic vision extraction output.",
                "",
                joined,
            ]
            self._record_page(
                rel_path=f"sources/{source_id}/agentic-page-{page_no:03d}.md",
                title=title,
                doc_type="agentic_source_page",
                body="\n".join(body),
                source_id=source_id,
                source_file=source_file,
                page_range=[page_no, page_no],
                evidence_ids=[evidence_id],
                extra_meta={
                    "page": page_no,
                    "extraction_method": "agentic_vision_document",
                    "kb_role": "primary",
                },
            )

    def _add_note_links(self, source_id: str, wiki_doc: Dict[str, Any]) -> None:
        for link in wiki_doc.get("note_links") or []:
            enriched = dict(link)
            enriched.setdefault("source_id", source_id)
            note_key = enriched.get("note_key")
            if note_key and not enriched.get("note_wiki_path"):
                note_no = enriched.get("note_no") or note_key
                enriched["note_wiki_path"] = f"notes/{source_id}-note-{_slug(str(note_no))}.md"
            self.note_links.append(enriched)

    def _add_document_block(
        self,
        source_id: str,
        source_file: Optional[str],
        block_index: int,
        block: Dict[str, Any],
        wiki_doc: Optional[Dict[str, Any]] = None,
    ) -> None:
        kind = block.get("kind") or "block"
        page_range = block.get("page_range") or []
        if kind == "statement":
            self._add_statement_block(source_id, source_file, block_index, block)
            return
        if kind == "notes":
            for item in block.get("items") or []:
                self._add_note_item(source_id, source_file, item, wiki_doc)
            return

        title = block.get("title") or kind.replace("_", " ").title()
        markdown = block.get("markdown") or ""
        rel = f"narrative/{source_id}-{_slug(title)}.md"
        evidence_id = self._add_evidence({
            "kind": "narrative",
            "source_id": source_id,
            "source_file": source_file,
            "page_range": page_range,
            "title": title,
        })
        body = f"# {title}\n\n{markdown}".strip()
        self._record_page(
            rel_path=rel,
            title=title,
            doc_type=kind,
            body=body,
            source_id=source_id,
            source_file=source_file,
            page_range=page_range,
            evidence_ids=[evidence_id],
        )

    def _add_statement_block(
        self,
        source_id: str,
        source_file: Optional[str],
        block_index: int,
        block: Dict[str, Any],
    ) -> None:
        statement = block.get("type") or f"statement-{block_index}"
        title = block.get("title") or statement.replace("_", " ").title()
        columns = [c.get("label") or c.get("fy") or c.get("id") for c in block.get("columns") or []]
        rows = block.get("rows") or []
        evidence_ids: List[str] = []

        lines = [f"# {title}", "", f"Source: `{source_file or source_id}`", ""]
        if columns:
            lines.append("| Line item | " + " | ".join(str(c) for c in columns) + " |")
            lines.append("|---|" + "|".join(["---:"] * len(columns)) + "|")
        for row in rows:
            label = row.get("label") or row.get("raw_label") or row.get("canonical_code") or ""
            values = row.get("values") or {}
            if columns:
                rendered = []
                for col in block.get("columns") or []:
                    key = col.get("id") or col.get("fy") or col.get("label")
                    rendered.append(_format_value(values.get(key)))
                lines.append(f"| {label} | " + " | ".join(rendered) + " |")
            evidence_ids.append(self._add_evidence({
                "kind": "statement_row",
                "source_id": source_id,
                "source_file": source_file,
                "page": row.get("page"),
                "page_range": block.get("page_range"),
                "statement": statement,
                "label": label,
                "canonical_code": row.get("canonical_code"),
                "values": values,
                "confidence": row.get("confidence"),
            }))

        self._record_page(
            rel_path=f"statements/{source_id}-{_slug(statement)}.md",
            title=title,
            doc_type="statement",
            body="\n".join(lines),
            source_id=source_id,
            source_file=source_file,
            page_range=block.get("page_range"),
            evidence_ids=evidence_ids[:50],
            extra_meta={"statement": statement},
        )

    def _add_note_item(
        self,
        source_id: str,
        source_file: Optional[str],
        item: Dict[str, Any],
        wiki_doc: Optional[Dict[str, Any]] = None,
    ) -> None:
        note_no = item.get("note_no")
        title = item.get("title") or f"Note {note_no or ''}".strip()
        page_range = item.get("page_range") or []
        note_key = _note_key(note_no or title)
        related_rows = _related_rows_for_note(wiki_doc or {}, note_key)
        evidence_id = self._add_evidence({
            "kind": "note",
            "source_id": source_id,
            "source_file": source_file,
            "page_range": page_range,
            "note_no": note_no,
            "note_key": note_key,
            "title": title,
            "related_statement_rows": related_rows,
        })
        body = [f"# {title}", "", item.get("markdown") or ""]
        if related_rows:
            body.extend(["", "## Linked Financial Statement Rows", ""])
            for row in related_rows:
                location = row.get("statement_title") or row.get("statement") or "statement"
                if row.get("row_page"):
                    location += f", p.{row['row_page']}"
                body.append(f"- {row.get('row_label')} ({location})")
        for table in item.get("tables") or []:
            body.extend(["", f"## {table.get('title') or 'Table'}", ""])
            cols = table.get("columns") or []
            rows = table.get("rows") or []
            labels = [c.get("label") or c.get("id") for c in cols]
            if labels:
                body.append("| " + " | ".join(labels) + " |")
                body.append("|" + "|".join(["---"] * len(labels)) + "|")
                for row in rows:
                    body.append("| " + " | ".join(str(row.get(c.get("id"), "")) for c in cols) + " |")
        self._record_page(
            rel_path=f"notes/{source_id}-note-{_slug(str(note_no or title))}.md",
            title=title,
            doc_type="note",
            body="\n".join(body),
            source_id=source_id,
            source_file=source_file,
            page_range=page_range,
            evidence_ids=[evidence_id],
            extra_meta={
                "note_no": note_no,
                "note_key": note_key,
                "related_statement_rows": related_rows,
            },
        )

    def _add_merged_statements(self, blocks: List[Dict[str, Any]]) -> None:
        for block in blocks:
            if block.get("kind") != "merged_table":
                continue
            rel_json = block.get("json")
            if not rel_json:
                continue
            merged = _read_json(self.parsed_root / rel_json, {})
            title = f"Merged {block.get('statement_name') or block.get('statement')}"
            fys = merged.get("fys") or []
            evidence_ids: List[str] = []
            lines = [f"# {title}", "", f"Perimeter: `{merged.get('perimeter') or block.get('perimeter')}`", ""]
            if fys:
                lines.append("| Line item | " + " | ".join(fys) + " |")
                lines.append("|---|" + "|".join(["---:"] * len(fys)) + "|")
            for row in merged.get("rows") or []:
                label = row.get("label") or row.get("canonical_code") or ""
                values = row.get("values") or {}
                lines.append(f"| {label} | " + " | ".join(_format_value(values.get(fy)) for fy in fys) + " |")
                evidence_ids.append(self._add_evidence({
                    "kind": "merged_statement_row",
                    "statement": merged.get("statement"),
                    "perimeter": merged.get("perimeter"),
                    "label": label,
                    "canonical_code": row.get("canonical_code"),
                    "values": values,
                    "provenance": row.get("provenance") or {},
                }))
            self._record_page(
                rel_path=f"statements/merged-{_slug(block.get('statement') or title)}-{_slug(block.get('perimeter') or 'company')}.md",
                title=title,
                doc_type="merged_statement",
                body="\n".join(lines),
                evidence_ids=evidence_ids[:80],
                extra_meta={
                    "statement": merged.get("statement"),
                    "perimeter": merged.get("perimeter"),
                    "periods": fys,
                    "source_ids": block.get("source_ids") or [],
                },
            )

    def _add_fs_analytics(self) -> None:
        fs_data = _read_json(self.case_root / "features" / "fs_analytics.json", {})
        if not fs_data:
            return
        fys = fs_data.get("fys") or []
        by_fy = fs_data.get("by_fy") or {}
        lines = ["# Financial Analytics", ""]
        if fys:
            lines.append("| Metric | " + " | ".join(fys) + " |")
            lines.append("|---|" + "|".join(["---:"] * len(fys)) + "|")
            for metric in (
                "revenue", "gross_profit", "ebitda", "pat", "total_assets",
                "total_equity", "total_debt", "cfo",
            ):
                lines.append("| " + metric.replace("_", " ").title() + " | " + " | ".join(
                    _format_value((by_fy.get(fy) or {}).get("raw", {}).get(metric)) for fy in fys
                ) + " |")
            for metric in (
                "gross_margin", "ebitda_margin", "pat_margin", "current_ratio",
                "quick_ratio", "debt_equity", "debt_ebitda", "interest_coverage",
                "receivable_days", "payable_days",
            ):
                lines.append("| " + metric.replace("_", " ").title() + " | " + " | ".join(
                    _format_value((by_fy.get(fy) or {}).get("ratios", {}).get(metric)) for fy in fys
                ) + " |")
        self._record_page(
            rel_path="analytics/financial-analytics.md",
            title="Financial Analytics",
            doc_type="metric",
            body="\n".join(lines),
            extra_meta={"periods": fys},
        )

    def _add_credit_memo(self) -> None:
        memo_path = self.case_root / "credit_memo.md"
        if not memo_path.exists():
            return
        memo = memo_path.read_text(encoding="utf-8")
        self._record_page(
            rel_path="report/credit-memo.md",
            title="Credit Memo",
            doc_type="report_section",
            body=memo,
        )

    def _write_index_page(self, index: Dict[str, Any]) -> None:
        sources = index.get("sources") or []
        body = ["# Case Wiki", "", "## Sources"]
        for source in sources:
            source_id = source.get("source_id")
            filename = source.get("original_filename") or source_id
            body.append(f"- [[sources/{source_id}/overview]] - {filename}")
        body.extend(["", "## Generated Pages"])
        for page in self.pages:
            body.append(f"- `{page['doc_type']}` [{page['title']}]({page['path']})")
        self._record_page(
            rel_path="index.md",
            title="Case Wiki",
            doc_type="index",
            body="\n".join(body),
        )


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if abs(value) < 10:
            return f"{value:.2f}"
        return f"{value:,.0f}"
    return str(value)


def build_case_wiki(case_root: Path) -> Dict[str, Any]:
    """Build or rebuild the case wiki and return its manifest summary."""
    return CaseWikiBuilder(Path(case_root)).build()
