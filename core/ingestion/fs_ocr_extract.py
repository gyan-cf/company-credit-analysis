"""
OCR fallback for image-only Unaudited Full Set (UFS) PDFs.

ACRA-filed UFS PDFs are sometimes rendered as page images (e.g. when the
filing agent uploads a scanned set). pdfplumber will return near-empty text;
this module uses pdf2image + tesseract to recover the financial statements.

Once OCR'd, lines are passed through the same canonical resolver used by the
text path so downstream consumers don't care which route the data took.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .fs_text_extract import (
    FSExtraction,
    STATEMENT_ANCHORS,
    END_OF_STATEMENT,
    _detect_columns,
    _detect_meta,
    _emit_lines_from_block,
)

logger = logging.getLogger(__name__)


def ocr_available() -> bool:
    """Quick capability check."""
    return shutil.which("tesseract") is not None and shutil.which("pdftoppm") is not None


def _pdf_to_text_via_ocr(
    pdf_path: Path,
    dpi: int = 300,
    page_range: Optional[Tuple[int, int]] = None,
) -> List[str]:
    """
    Convert a PDF to one OCR text string per page.

    Strategy:
        1. pdftoppm <pdf> <prefix> -png -r <dpi> [-f F] [-l L]   (one PNG per page)
        2. tesseract <png> stdout
    Both are pre-installed in the workspace; we shell out rather than pull extra deps.

    Args:
        page_range: optional (first, last) inclusive 1-indexed page range. Default = all.
    """
    if not ocr_available():
        raise RuntimeError(
            "OCR tools not found. Install tesseract + poppler-utils, "
            "or set OCR_DISABLED=1 to skip image-only PDFs."
        )
    pages: List[str] = []
    with tempfile.TemporaryDirectory(prefix="ufs_ocr_") as tmp:
        tmp_path = Path(tmp)
        prefix = tmp_path / "page"
        cmd = ["pdftoppm", str(pdf_path), str(prefix), "-png", "-r", str(dpi)]
        if page_range:
            cmd += ["-f", str(page_range[0]), "-l", str(page_range[1])]
        subprocess.run(cmd, check=True, capture_output=True)
        for png in sorted(tmp_path.glob("page-*.png")):
            r = subprocess.run(
                ["tesseract", str(png), "stdout", "-l", "eng", "--psm", "6"],
                check=True, capture_output=True, text=True,
            )
            pages.append(r.stdout)
    return pages


def extract_fs_ufs(pdf_path: Path, dpi: int = 300) -> FSExtraction:
    """
    OCR an image-only UFS PDF and parse it using the same logic as z124.
    """
    pdf_path = Path(pdf_path)
    pages_text = _pdf_to_text_via_ocr(pdf_path, dpi=dpi)

    ext = FSExtraction(source_file=str(pdf_path), extraction_method="ocr")
    cover_text = "\n".join(pages_text[:3])
    meta = _detect_meta(cover_text)
    ext.entity_name = meta.get("entity_name", "")
    ext.uen = meta.get("uen", "")
    ext.framework = meta.get("framework", "SFRS")
    ext.audited = meta.get("audited", False)
    ext.consolidated = meta.get("consolidated", False)
    ext.period_end_primary = meta.get("period_end_primary", "")
    ext.pages_text = pages_text

    current_stmt: Optional[str] = None
    current_columns: List = []
    block_buffer: List[Tuple[int, str]] = []
    order_per_stmt: Dict[str, int] = {}

    def flush_block():
        nonlocal current_stmt, current_columns, block_buffer
        if not current_stmt or not block_buffer:
            block_buffer = []
            return
        first_lines = [ln for _, ln in block_buffer[:10]]
        cols = _detect_columns(first_lines)
        if cols and not current_columns:
            current_columns = cols
            if not ext.columns:
                ext.columns = cols
            else:
                for c in cols:
                    if not any(x.perimeter == c.perimeter and x.fy == c.fy for x in ext.columns):
                        ext.columns.append(c)
        if not current_columns:
            ext.review_flags.append({
                "severity": "medium",
                "message": f"OCR: column detection failed for {current_stmt}",
                "source": pdf_path.name,
            })
            block_buffer = []
            return
        start_order = order_per_stmt.get(current_stmt, 0)
        next_order = _emit_lines_from_block(
            block_buffer=block_buffer,
            current_columns=current_columns,
            current_stmt=current_stmt,
            ext=ext,
            start_order=start_order,
            ocr_mode=True,
        )
        order_per_stmt[current_stmt] = next_order
        block_buffer = []
        current_columns = []

    for page_no, text in enumerate(pages_text, start=1):
        for raw_line in text.split("\n"):
            line = raw_line.rstrip()
            if not line.strip():
                continue
            new_stmt = None
            for code, pat in STATEMENT_ANCHORS:
                if pat.search(line):
                    new_stmt = code
                    break
            if new_stmt:
                flush_block()
                current_stmt = new_stmt if new_stmt in ("sofp", "soci", "socf") else None
                current_columns = []
                continue
            if current_stmt and END_OF_STATEMENT.search(line):
                flush_block()
                current_stmt = None
            if current_stmt:
                block_buffer.append((page_no, line))
    flush_block()

    ext.review_flags.append({
        "severity": "info",
        "message": "Extraction via OCR — values require analyst review",
        "source": pdf_path.name,
    })
    return ext
