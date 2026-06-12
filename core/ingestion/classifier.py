"""
File classifier for Singapore ACRA / FS documents.

Routes by filename pattern first (fast & reliable for ACRA BizFile+ outputs),
then by first-page text content as a secondary check.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


# Source type taxonomy — wide enough to grow, narrow enough to route
SOURCE_TYPES = {
    "acra_annual_return": "ACRA C223 Annual Return (corporate profile)",
    "acra_filing_cover":  "ACRA BM42A cover sheet (filing declaration)",
    "fs_xbrl_render":     "z124 XBRL→PDF rendering of financial statements",
    "fs_ufs":             "Unaudited Full Set PDF (image-only, needs OCR)",
    "fs_excel":           "Excel financial spread (template / management accounts)",
    "zip":                "Archive — expand and reclassify children",
    "unknown":            "Unrecognised — needs manual review",
}


@dataclass
class ClassifiedFile:
    path: Path
    source_type: str
    uen: Optional[str] = None
    fy: Optional[str] = None  # e.g. "FY2023"
    confidence: float = 0.0
    reasons: List[str] = field(default_factory=list)
    children: List["ClassifiedFile"] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "filename": self.path.name,
            "source_type": self.source_type,
            "source_type_label": SOURCE_TYPES.get(self.source_type, self.source_type),
            "uen": self.uen,
            "fy": self.fy,
            "confidence": round(self.confidence, 2),
            "reasons": self.reasons,
            "children": [c.to_dict() for c in self.children],
        }


# ----- Filename pattern matchers -------------------------------------------------

_UEN_RE = re.compile(r"\b(\d{8,10}[A-Z])\b")

# ACRA naming conventions observed on BizFile+ exports:
#   EXT_<UEN>_C<docid>_C223_1.pdf       -> Annual Return for Local Company
#   EXT_<UEN>_T<docid>_BM42A_1.pdf      -> Filing cover sheet (small-co etc.)
#   c<docid>_z124_1.pdf                 -> XBRL → PDF financial statements
#   *_UFS_*FYE*.pdf                     -> Unaudited Full Set (image-only)
_PATTERNS = [
    (re.compile(r"_C\d+_C223_\d+\.pdf$", re.I),      "acra_annual_return",  0.95),
    (re.compile(r"_T\d+_BM42A_\d+\.pdf$", re.I),     "acra_filing_cover",   0.95),
    (re.compile(r"_z\d{3}_\d+\.pdf$", re.I),         "fs_xbrl_render",      0.92),
    (re.compile(r"UFS[^/]*FYE.*\.pdf$", re.I),       "fs_ufs",              0.90),
    (re.compile(r".*UFS.*\.pdf$", re.I),             "fs_ufs",              0.75),
    (re.compile(r".*\.xlsx?$", re.I),                "fs_excel",            0.80),
    (re.compile(r".*\.zip$", re.I),                  "zip",                 1.00),
]

# Phrases used as last-resort text disambiguation
_TEXT_HINTS = {
    "acra_annual_return": ["Filing of Annual Return", "Section A: Company Type"],
    "acra_filing_cover":  ["File Annual Returns", "small company exempt", "Lodged with Accounting and Corporate Regulatory"],
    "fs_xbrl_render":     ["system-generated from the full set", "Filed in XBRL", "STATEMENT OF FINANCIAL POSITION"],
    "fs_ufs":             ["UNAUDITED FINANCIAL STATEMENTS", "INDEPENDENT AUDITOR"],
}


def _peek_pdf_text(path: Path, max_chars: int = 3000) -> str:
    try:
        import pdfplumber
        text = []
        with pdfplumber.open(path) as pdf:
            for p in pdf.pages[:3]:
                t = p.extract_text() or ""
                text.append(t)
                if sum(len(x) for x in text) > max_chars:
                    break
        return "\n".join(text)[:max_chars]
    except Exception:
        return ""


def _detect_fy(path: Path, text: str = "") -> Optional[str]:
    # Folder-level hint (input/financials/FY2023/...)
    for part in path.parts:
        m = re.match(r"^FY(\d{4})$", part, re.I)
        if m:
            return f"FY{m.group(1)}"
    # Filename / text "31 Dec 2024", "FYE 31 Dec 2024", "December 2024"
    blob = f"{path.name} {text}"
    m = re.search(r"FYE\s*\d{1,2}\s+(?:Dec|Jan|Mar|Jun|Sep)[a-z]*\s*(\d{4})", blob, re.I)
    if m:
        return f"FY{m.group(1)}"
    m = re.search(r"(?:31\s+Dec(?:ember)?\s+|FOR\s+THE\s+FINANCIAL\s+YEAR\s+ENDED?\s+\d{1,2}\s+\w+\s+)(\d{4})", blob, re.I)
    if m:
        return f"FY{m.group(1)}"
    return None


def _detect_uen(path: Path, text: str = "") -> Optional[str]:
    for src in (path.name, text):
        m = _UEN_RE.search(src or "")
        if m:
            return m.group(1)
    return None


def classify_file(path: Path, peek_text: bool = True) -> ClassifiedFile:
    """Classify a single file. Recurses into zips."""
    path = Path(path)
    cf = ClassifiedFile(path=path, source_type="unknown")

    # 1) Filename pattern
    for pat, stype, conf in _PATTERNS:
        if pat.search(path.name):
            cf.source_type = stype
            cf.confidence = conf
            cf.reasons.append(f"filename matched /{pat.pattern}/")
            break

    # 2) Recurse into zips
    if cf.source_type == "zip" and path.exists():
        try:
            with zipfile.ZipFile(path) as zf:
                for name in zf.namelist():
                    if name.endswith("/"):
                        continue
                    child = ClassifiedFile(
                        path=Path(f"{path}::{name}"),
                        source_type="unknown",
                    )
                    for pat, stype, conf in _PATTERNS:
                        if pat.search(name):
                            child.source_type = stype
                            child.confidence = conf
                            child.reasons.append(f"zip entry name matched /{pat.pattern}/")
                            break
                    child.uen = _detect_uen(Path(name))
                    child.fy = _detect_fy(Path(name))
                    cf.children.append(child)
        except Exception as e:
            cf.reasons.append(f"zip read error: {e}")
        cf.uen = next((c.uen for c in cf.children if c.uen), None)
        cf.fy = next((c.fy for c in cf.children if c.fy), None)
        return cf

    # 3) Text-content disambiguation (only when filename was ambiguous or low conf)
    text = ""
    if peek_text and path.suffix.lower() == ".pdf" and path.exists():
        text = _peek_pdf_text(path)
        if cf.source_type in ("unknown", "fs_ufs") or cf.confidence < 0.85:
            best, best_score = cf.source_type, 0
            for stype, phrases in _TEXT_HINTS.items():
                score = sum(1 for ph in phrases if ph.lower() in text.lower())
                if score > best_score:
                    best, best_score = stype, score
            if best_score:
                cf.source_type = best
                cf.confidence = max(cf.confidence, 0.6 + 0.1 * best_score)
                cf.reasons.append(f"text hints → {best} (score={best_score})")

        # Promote fs_ufs to fs_xbrl_render if first pages look text-rich and contain XBRL marker
        if cf.source_type in ("fs_ufs", "unknown") and "XBRL" in text:
            cf.source_type = "fs_xbrl_render"
            cf.confidence = max(cf.confidence, 0.9)
            cf.reasons.append("XBRL marker found in body text")

    cf.uen = _detect_uen(path, text)
    cf.fy = _detect_fy(path, text)
    return cf


def discover_and_classify(root: Path, expand_zips: bool = False) -> List[ClassifiedFile]:
    """Walk a folder, classify every supported file. Skips ACRA archive zips by default."""
    root = Path(root)
    found: List[ClassifiedFile] = []
    if not root.exists():
        return found

    # Iterate stable order so demos are reproducible
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        # Skip hidden / mac noise
        if any(part.startswith(".") for part in p.parts):
            continue
        suf = p.suffix.lower()
        if suf not in (".pdf", ".xlsx", ".xls", ".zip", ".xml"):
            continue
        # Don't double-walk into _Archives by default (it's just re-packaged copies)
        if not expand_zips and "_Archives" in p.parts:
            continue
        found.append(classify_file(p))
    return found
