"""
Singapore-aware ingestion pipeline.

Modules:
    classifier              — route an uploaded file to its ACRA / FS type
    canonical_map           — SFRS(I) line-item synonym → canonical code map
    acra_profile_extract    — parse C223 Annual Return + BM42A cover sheet
    fs_text_extract         — parse z124 XBRL→PDF financial statements (text path)
    fs_ocr_extract          — OCR fallback for image-only UFS PDFs
    sg_pipeline             — orchestrator: discover → classify → extract → consolidate
"""

from .classifier import classify_file, ClassifiedFile
from .sg_pipeline import SGIngestionPipeline, IngestionResult

__all__ = [
    "classify_file",
    "ClassifiedFile",
    "SGIngestionPipeline",
    "IngestionResult",
]
