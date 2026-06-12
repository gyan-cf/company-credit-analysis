"""
Narrative-section extractor for SFRS(I) financial-statement PDFs.

Sits next to `fs_text_extract` and `fs_ocr_extract` but takes a different cut
of the same per-page text: instead of statement tables it pulls out prose
blocks — Directors' Statement, Independent Auditor's Report, Significant
Accounting Policies, and the individually numbered Notes that follow the
statements.

Each section is emitted as a NarrativeSection with a markdown body so the
frontend can render it inline next to the per-statement tables.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .fs_text_extract import STATEMENT_ANCHORS, END_OF_STATEMENT


# ---- Markers -------------------------------------------------------------------

AUDITOR_RE = re.compile(r"\bINDEPENDENT\s+AUDITOR[''']?S?\s+REPORT\b", re.I)
DIRECTORS_RE = re.compile(r"(?:DIRECTORS[''']?\s+STATEMENT|STATEMENT\s+BY\s+DIRECTORS)\b", re.I)
NOTES_HEADER_RE = re.compile(r"NOTES\s+TO\s+(?:THE\s+)?FINANCIAL\s+STATEMENTS", re.I)
POLICIES_TITLE_RE = re.compile(r"(?:SUMMARY\s+OF\s+)?SIGNIFICANT\s+ACCOUNTING\s+POLICIES", re.I)
CORPORATE_INFO_RE = re.compile(r"\b(GENERAL|CORPORATE)\s+INFORMATION\b", re.I)

# Numbered note headings — "1. Corporate information", "12 Revenue", "3a Property",
# usually on their own line and Title-cased / sentence-cased.
NOTE_HEADING_RE = re.compile(
    r"^\s*(\d{1,2})[\.\)]?\s+([A-Z][A-Za-z][A-Za-z\s\-,/&'()]{2,80}?)\s*$"
)

# Things we don't want to treat as a new note heading even if they pattern-match.
_NOT_A_NOTE_TITLE = {
    "the company", "the group", "to the year", "for the year",
}


# ---- Data model ----------------------------------------------------------------

@dataclass
class NarrativeSection:
    kind: str                     # auditor_report | directors_statement | policies | note | corporate_info
    title: str
    note_no: Optional[int] = None
    pages: List[int] = field(default_factory=list)
    markdown: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "note_no": self.note_no,
            "pages": self.pages,
            "markdown": self.markdown,
        }


# ---- Extractor ------------------------------------------------------------------

def _classify_note_title(title: str, note_no: Optional[int]) -> str:
    """Map a note heading to a kind."""
    low = title.lower()
    if POLICIES_TITLE_RE.search(title) or "accounting policies" in low:
        return "policies"
    if CORPORATE_INFO_RE.search(title) or "corporate information" in low or "general information" in low:
        return "corporate_info"
    return "note"


def extract_narrative_sections(pages_text: List[str]) -> List[NarrativeSection]:
    """
    Walk the per-page text and slice out narrative sections.

    Returns sections in document order. Each section's `markdown` body is the
    raw extracted text under that heading — CommonMark soft-wraps line breaks
    so the wrapped PDF text renders as proper paragraphs.
    """
    sections: List[NarrativeSection] = []
    current: Optional[NarrativeSection] = None
    in_table = False
    in_notes = False

    def finalize() -> None:
        nonlocal current
        if current and current.markdown.strip():
            current.markdown = current.markdown.rstrip() + "\n"
            sections.append(current)
        current = None

    def start(kind: str, title: str, page_no: int, note_no: Optional[int] = None) -> None:
        nonlocal current
        finalize()
        body = f"# {note_no}. {title}\n\n" if note_no is not None else f"# {title}\n\n"
        current = NarrativeSection(
            kind=kind, title=title, note_no=note_no,
            pages=[page_no], markdown=body,
        )

    for page_idx, page in enumerate(pages_text, start=1):
        for raw_line in page.split("\n"):
            line = raw_line.rstrip()
            stripped = line.strip()
            if not stripped:
                if current is not None:
                    current.markdown += "\n"
                continue

            # FS table title — stops any active narrative block and enters table mode.
            hit_anchor = any(pat.search(stripped) for _, pat in STATEMENT_ANCHORS)
            if hit_anchor:
                finalize()
                in_table = True
                in_notes = False
                continue

            # End-of-statement marker — exit table mode. Often the same line
            # mentions "Notes to the financial statements", which we treat as
            # the gateway into the numbered-notes phase.
            if in_table and END_OF_STATEMENT.search(stripped):
                in_table = False
                if NOTES_HEADER_RE.search(stripped):
                    in_notes = True
                continue
            if NOTES_HEADER_RE.search(stripped):
                finalize()
                in_notes = True
                in_table = False
                continue

            if in_table:
                continue

            # Narrative section starts. Directors' / auditor's reports can appear
            # before the FS tables; they should not be confused with note headings.
            if AUDITOR_RE.search(stripped):
                start("auditor_report", "Independent Auditor's Report", page_idx)
                continue
            if DIRECTORS_RE.search(stripped):
                start("directors_statement", "Directors' Statement", page_idx)
                continue

            # Numbered note heading (only meaningful after the notes section header).
            if in_notes:
                m = NOTE_HEADING_RE.match(line)
                if m:
                    title = m.group(2).strip().rstrip(":").rstrip()
                    if title.lower() not in _NOT_A_NOTE_TITLE:
                        note_no = int(m.group(1))
                        kind = _classify_note_title(title, note_no)
                        start(kind, title, page_idx, note_no=note_no)
                        continue

            if current is not None:
                if page_idx not in current.pages:
                    current.pages.append(page_idx)
                current.markdown += line + "\n"

    finalize()
    return sections
