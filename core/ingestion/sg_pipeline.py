"""
Singapore ingestion pipeline orchestrator.

Inputs:
    A folder or a list of files (typically `input/financials/<UEN>/...`
    or a case's `raw/financials/` directory).

Steps:
    1. Discover files and classify each.
    2. Optionally expand archives (`_Archives/*.zip`) to surface UFS PDFs.
    3. Route each file:
         - acra_annual_return / acra_filing_cover → CorporateProfile
         - fs_xbrl_render                         → FSExtraction (text)
         - fs_ufs                                 → FSExtraction (OCR)
         - fs_excel                               → existing fs_excel_parser
    4. Merge multi-year FS into a per-(perimeter, fy) periods array.
    5. Merge profiles + reconcile metadata.
    6. Emit a single IngestionResult that downstream agents consume.
"""

from __future__ import annotations

import io
import json
import logging
import os
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .classifier import (
    classify_file,
    discover_and_classify,
    ClassifiedFile,
)
from .acra_profile_extract import (
    CorporateProfile,
    extract_bm42a,
    extract_c223,
    merge_profiles,
)
from .fs_text_extract import (
    FSExtraction,
    extract_fs_z124,
    extraction_to_periods,
)
from .fs_ocr_extract import extract_fs_ufs, ocr_available
from .narrative_extract import extract_narrative_sections, NarrativeSection
from .block_writer import write_source_blocks, gc_stale_sources, write_merged_blocks
from .document_writer import (
    build_document,
    validate_document,
    write_document_json,
    parse_fs_pdf_to_document,
    document_to_fs_extraction,
    document_to_narrative_sections,
)
from core.knowledge.agentic_wiki import write_agentic_wiki_document

logger = logging.getLogger(__name__)


@dataclass
class IngestionResult:
    started_at: str = ""
    finished_at: str = ""
    root: str = ""
    classifications: List[Dict[str, Any]] = field(default_factory=list)
    profile: Dict[str, Any] = field(default_factory=dict)
    fs_extractions: List[Dict[str, Any]] = field(default_factory=list)
    periods: List[Dict[str, Any]] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)
    errors: List[Dict[str, str]] = field(default_factory=list)
    review_flags: List[Dict[str, Any]] = field(default_factory=list)
    blocks_index: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "root": self.root,
            "summary": self.summary,
            "classifications": self.classifications,
            "profile": self.profile,
            "fs_extractions": self.fs_extractions,
            "periods": self.periods,
            "review_flags": self.review_flags,
            "errors": self.errors,
            "blocks_index": self.blocks_index,
        }


class SGIngestionPipeline:
    """Orchestrate classification → extraction → consolidation for SG ACRA filings."""

    def __init__(
        self,
        expand_zips: bool = True,
        ocr_enabled: bool = True,
        ocr_dpi: int = 300,
        *,
        use_agentic: bool = True,
        agentic_model: Optional[str] = None,
    ):
        self.expand_zips = expand_zips
        self.ocr_enabled = ocr_enabled and ocr_available() and not os.getenv("OCR_DISABLED")
        self.ocr_dpi = ocr_dpi
        # When True (default) FS PDFs go through the vision-LLM extractor and
        # `extract_fs_z124` / `extract_fs_ufs` are bypassed. The agentic call
        # produces the canonical document dict; we re-derive an FSExtraction
        # from it so the existing block writer + merged-block path keep working.
        self.use_agentic = use_agentic
        self.agentic_model = agentic_model

    # ------------------------------------------------------------------ Public API

    def ingest_path(
        self,
        root: Path,
        *,
        parsed_root: Optional[Path] = None,
        case_root: Optional[Path] = None,
        on_file_processed: Optional[Any] = None,
        selected_filenames: Optional[List[str]] = None,
    ) -> IngestionResult:
        """
        Run the pipeline over `root`.

        When `parsed_root` is provided, the pipeline also writes per-source
        labelled blocks (tables + narrative MD) under it and emits a rollup
        `index.json` next to them — drive the UI off this index.

        `case_root` (defaults to `root`) is used to compute portable relative
        paths for the original PDFs in each manifest.

        `selected_filenames`, when provided, restricts extraction to the named
        files under `root`. This is used for targeted re-extraction while still
        keeping original-file paths in the generated manifests.
        """
        root = Path(root)
        case_root = Path(case_root) if case_root else root
        result = IngestionResult(
            root=str(root),
            started_at=datetime.utcnow().isoformat() + "Z",
        )

        # 1. Discover & classify
        classified = discover_and_classify(root, expand_zips=False)
        if selected_filenames:
            wanted = set(selected_filenames)
            classified = [c for c in classified if c.path.name in wanted]
        result.classifications = [c.to_dict() for c in classified]

        # 2. Expand UFS from zip archives when text-only FS coverage is incomplete
        ufs_paths_from_zip = self._maybe_extract_ufs_from_zips(root) if self.expand_zips else []
        for p in ufs_paths_from_zip:
            cf = classify_file(p)
            result.classifications.append(cf.to_dict())
            classified.append(cf)

        # 3. Route each file. Carry the agentic document dict alongside the
        # ClassifiedFile + derived FSExtraction so step 7 can write document.json
        # without re-extracting.
        profiles: List[CorporateProfile] = []
        fs_pairs: List[Tuple[ClassifiedFile, FSExtraction, Optional[Dict[str, Any]]]] = []
        for cf in classified:
            try:
                if cf.source_type == "acra_filing_cover":
                    profiles.append(extract_bm42a(cf.path))
                elif cf.source_type == "acra_annual_return":
                    profiles.append(extract_c223(cf.path))
                elif cf.source_type in ("fs_xbrl_render", "fs_ufs"):
                    try:
                        if self.use_agentic:
                            doc = parse_fs_pdf_to_document(
                                cf.path, strategy="agentic", model=self.agentic_model,
                            )
                            ext = document_to_fs_extraction(doc, cf.path)
                            fs_pairs.append((cf, ext, doc))
                        elif cf.source_type == "fs_xbrl_render":
                            fs_pairs.append((cf, extract_fs_z124(cf.path), None))
                        elif self.ocr_enabled:
                            fs_pairs.append((cf, extract_fs_ufs(cf.path, dpi=self.ocr_dpi), None))
                        else:
                            result.review_flags.append({
                                "severity": "high",
                                "message": "UFS PDF detected but OCR disabled — FS not extracted",
                                "source": cf.path.name,
                            })
                            if on_file_processed is not None:
                                on_file_processed(cf.path.name, "failed", "OCR disabled")
                            continue
                    except Exception as exc:
                        logger.exception("FS extraction failed for %s", cf.path.name)
                        if on_file_processed is not None:
                            on_file_processed(cf.path.name, "failed", f"{type(exc).__name__}: {exc}")
                        result.errors.append({
                            "file": cf.path.name,
                            "error": f"{type(exc).__name__}: {exc}",
                        })
                        continue
                    if on_file_processed is not None:
                        on_file_processed(cf.path.name, "ready", None)
                elif cf.source_type == "fs_excel":
                    # delegate to existing excel parser if available
                    try:
                        from features.fs_excel_parser import parse_fs_excel
                        data = parse_fs_excel(str(cf.path))
                        fs_pairs.append((cf, self._wrap_excel_as_extraction(cf.path, data), None))
                    except Exception as e:
                        result.errors.append({"file": cf.path.name, "error": f"fs_excel: {e}"})
                else:
                    # unknown / zip — already accounted for
                    pass
            except Exception as e:
                logger.exception("Ingestion failed for %s", cf.path)
                result.errors.append({"file": cf.path.name, "error": f"{type(e).__name__}: {e}"})

        fs_extractions = [fe for _, fe, _ in fs_pairs]

        # 4. Merge profiles + FS extractions
        merged_profile = merge_profiles(profiles)
        result.profile = merged_profile.to_dict()
        result.fs_extractions = [fe.to_dict() for fe in fs_extractions]

        # collapse all extractions into one periods array, de-duped on (perimeter, fy)
        periods = self._merge_periods(fs_extractions)
        result.periods = periods

        # 5. Roll review flags up
        for fe in fs_extractions:
            result.review_flags.extend(fe.review_flags)
        result.review_flags.extend(merged_profile.review_flags)

        # 6. Summary
        result.summary = self._build_summary(merged_profile, fs_extractions, periods, classified)

        # 7. Per-source labelled blocks + rollup index (only when parsed_root given)
        if parsed_root is not None:
            try:
                result.blocks_index = self._write_blocks(
                    fs_pairs, parsed_root=Path(parsed_root), case_root=case_root,
                    started_at=result.started_at,
                    merged_profile=merged_profile,
                )
                # Surface document-validation review flags on the IngestionResult
                # so the API + frontend see them next to extraction flags.
                result.review_flags.extend(result.blocks_index.get("validation_review_flags", []))
            except Exception as e:
                logger.exception("Block writing failed")
                result.errors.append({"file": "<blocks>", "error": f"{type(e).__name__}: {e}"})

        result.finished_at = datetime.utcnow().isoformat() + "Z"
        return result

    # ----------------------------------------------------------------- Internals

    def _maybe_extract_ufs_from_zips(self, root: Path) -> List[Path]:
        """If any zip in the tree contains a UFS PDF and no top-level UFS exists, extract it."""
        if not root.exists():
            return []
        existing = list(root.rglob("*UFS*.pdf"))
        if existing:
            return existing
        out_dir = root / "_extracted"
        extracted: List[Path] = []
        for zp in root.rglob("*.zip"):
            try:
                with zipfile.ZipFile(zp) as zf:
                    for name in zf.namelist():
                        if "UFS" in name and name.lower().endswith(".pdf"):
                            out_dir.mkdir(parents=True, exist_ok=True)
                            dst = out_dir / Path(name).name
                            if not dst.exists():
                                dst.write_bytes(zf.read(name))
                            extracted.append(dst)
            except Exception as e:
                logger.warning("zip read failed: %s — %s", zp, e)
        return extracted

    @staticmethod
    def _entity_extras_from_profile(merged_profile: Optional[CorporateProfile]) -> Dict[str, Any]:
        """Pull the ACRA-derived fields the document schema's entity block accepts."""
        if not merged_profile:
            return {"incorporation_country": "SG"}
        extras: Dict[str, Any] = {"incorporation_country": "SG"}
        if merged_profile.entity_name:
            extras["name"] = merged_profile.entity_name
        if merged_profile.uen:
            extras["uen"] = merged_profile.uen
        if merged_profile.primary_ssic_code:
            extras["ssic_code"] = merged_profile.primary_ssic_code
        if merged_profile.primary_ssic_desc:
            extras["ssic_description"] = merged_profile.primary_ssic_desc
        return extras

    @staticmethod
    def _write_blocks(
        fs_pairs: List[Tuple[ClassifiedFile, FSExtraction, Optional[Dict[str, Any]]]],
        *,
        parsed_root: Path,
        case_root: Path,
        started_at: str,
        merged_profile: Optional[CorporateProfile] = None,
    ) -> Dict[str, Any]:
        """
        Materialise each FSExtraction as a per-source block bundle on disk and
        return a rollup index ready to drive the financials UI.

        Per source PDF, this also emits a schema-conformant `document.json`
        (sg_fs_document_schema.json) and validates it. Validation errors are
        surfaced into the source's index entry AND into the run's review_flags
        — they don't abort the pipeline, but they're visible.
        """
        parsed_root = Path(parsed_root)
        parsed_root.mkdir(parents=True, exist_ok=True)

        entity_extras = SGIngestionPipeline._entity_extras_from_profile(merged_profile)
        validation_review_flags: List[Dict[str, Any]] = []

        sources: List[Dict[str, Any]] = []
        blocks_flat: List[Dict[str, Any]] = []
        sources_with_extractions: List[Tuple[str, FSExtraction]] = []
        for cf, ext, precomputed_doc in fs_pairs:
            # Narratives: prefer those derived from the agentic doc; fall back
            # to the rule-based slicer when only pages_text is available.
            if precomputed_doc is not None:
                narrative = document_to_narrative_sections(precomputed_doc)
                raw_text = ""
            else:
                narrative = extract_narrative_sections(ext.pages_text) if ext.pages_text else []
                raw_text = "\n\n".join(ext.pages_text) if ext.pages_text else ""
            manifest = write_source_blocks(
                extraction=ext,
                narrative_sections=narrative,
                raw_text=raw_text,
                source_pdf=cf.path,
                case_root=case_root,
                parsed_root=parsed_root,
            )
            source_id = manifest["source_id"]
            sources_with_extractions.append((source_id, ext))

            # Document.json: prefer the pre-computed agentic doc (already has
            # full block structure + notes + tables). Fall back to building
            # one from the FSExtraction when no doc was produced (rule-based
            # path or excel).
            doc_json_rel: Optional[str] = None
            wiki_doc_rel: Optional[str] = None
            doc_errors: List[str] = []
            try:
                if precomputed_doc is not None:
                    doc = precomputed_doc
                    # Overlay ACRA-derived entity fields (canonical name,
                    # ssic_code, ssic_description) so they take precedence
                    # over whatever the LLM read off the cover.
                    if entity_extras:
                        entity_block = doc.setdefault("document", {}).setdefault("entity", {})
                        for k, v in entity_extras.items():
                            if v not in (None, ""):
                                entity_block[k] = v
                else:
                    doc = build_document(
                        extraction=ext,
                        narrative_sections=narrative,
                        source_pdf=cf.path,
                        source_id=source_id,
                        entity_extras=entity_extras,
                    )
                doc_errors = validate_document(doc)
                write_document_json(doc, parsed_root / source_id / "document.json")
                doc_json_rel = f"{source_id}/document.json"
                write_agentic_wiki_document(
                    doc,
                    parsed_root / source_id / "wiki_document.json",
                    source_id=source_id,
                    source_file=cf.path.name,
                )
                wiki_doc_rel = f"{source_id}/wiki_document.json"
                if doc_errors:
                    validation_review_flags.append({
                        "severity": "high",
                        "message": (
                            f"document.json failed sg_fs_document_schema validation "
                            f"({len(doc_errors)} error(s))"
                        ),
                        "source": cf.path.name,
                        "errors": doc_errors[:10],
                    })
            except Exception as e:
                logger.exception("document build/write failed for %s", cf.path.name)
                doc_errors = [f"build/write failure: {type(e).__name__}: {e}"]
                validation_review_flags.append({
                    "severity": "high",
                    "message": f"document.json build/write threw {type(e).__name__}",
                    "source": cf.path.name,
                    "errors": doc_errors,
                })
            sources.append({
                "source_id": source_id,
                "source_type": cf.source_type,
                "original_filename": manifest["original_filename"],
                "original_path": manifest.get("original_path"),
                "manifest": f"{source_id}/manifest.json",
                "document_json": doc_json_rel,
                "wiki_document": wiki_doc_rel,
                "document_validation_errors": doc_errors,
                "entity": manifest["entity"]["name"],
                "uen": manifest["entity"]["uen"],
                "framework": manifest["entity"]["framework"],
                "audited": manifest["audited"],
                "consolidated": manifest["consolidated"],
                "extraction_method": manifest["extraction_method"],
                "fys": sorted({c["fy"] for c in manifest["columns"]}, reverse=True),
                "perimeters": sorted({c["perimeter"] for c in manifest["columns"]}),
                "block_count": len(manifest["blocks"]),
            })
            for b in manifest["blocks"]:
                flat = {"source_id": source_id, **b}
                blocks_flat.append(flat)

        # GC any source dirs from previous runs whose files are no longer present.
        removed = gc_stale_sources(parsed_root, [s["source_id"] for s in sources])

        # Cross-source merged blocks (case-level fused view, fed to the frontend
        # as a single statement table per perimeter spanning all uploaded FYs).
        merged_blocks: List[Dict[str, Any]] = []
        if sources_with_extractions:
            try:
                merged_blocks = write_merged_blocks(
                    sources=sources_with_extractions,
                    parsed_root=parsed_root,
                )
                blocks_flat.extend({"source_id": "merged", **b} for b in merged_blocks)
            except Exception:
                logger.exception("merged-block build failed")

        index = {
            "started_at": started_at,
            "finished_at": datetime.utcnow().isoformat() + "Z",
            "parsed_root": str(parsed_root),
            "source_count": len(sources),
            "block_count": len(blocks_flat),
            "merged_block_count": len(merged_blocks),
            "removed_stale": removed,
            "document_validation_error_count": sum(
                len(s.get("document_validation_errors", []) or []) for s in sources
            ),
            "validation_review_flags": validation_review_flags,
            "sources": sources,
            "blocks": blocks_flat,
        }
        (parsed_root / "index.json").write_text(
            json.dumps(index, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return index

    @staticmethod
    def _wrap_excel_as_extraction(path: Path, parsed: Dict[str, Any]) -> FSExtraction:
        """Shim the existing Excel parser output into our FSExtraction shape."""
        from .fs_text_extract import ExtractedLine, ColumnHeader
        ext = FSExtraction(source_file=str(path), extraction_method="excel")
        # Best-effort field copy (existing parser shape: meta + periods)
        meta = parsed.get("meta", {})
        ext.entity_name = meta.get("company_name", "")
        ext.currency = meta.get("currency", "SGD")
        for period in parsed.get("periods", []):
            raw_fy = str(period.get("fy") or period.get("period_end", ""))[:6]
            fy = raw_fy if raw_fy.upper().startswith("FY") else f"FY{raw_fy[:4]}"
            col = ColumnHeader(perimeter="company", fy=fy)
            ext.columns.append(col)
            for stmt_key in ("sofp", "soci", "socf", "bs", "pl", "cf"):
                for item in period.get(stmt_key, []) or []:
                    code = item.get("canonical_code")
                    ext.lines.append(ExtractedLine(
                        raw_label=item.get("label", ""),
                        canonical_code=code,
                        label=item.get("label"),
                        statement=stmt_key if stmt_key in ("sofp", "soci", "socf") else "sofp",
                        values={f"{col.perimeter}_{col.fy}": float(item.get("amount", 0))},
                        confidence=0.9,
                    ))
        return ext

    @staticmethod
    def _merge_periods(extractions: List[FSExtraction]) -> List[Dict[str, Any]]:
        merged: Dict[str, Dict[str, Any]] = {}
        for ext in extractions:
            for period in extraction_to_periods(ext):
                key = f"{period['perimeter']}_{period['fy']}"
                if key not in merged:
                    merged[key] = period
                    continue
                # Same (perimeter, fy) seen twice — prefer the entry with more lines
                existing_lines = sum(len(v) for v in merged[key]["statements"].values())
                new_lines = sum(len(v) for v in period["statements"].values())
                if new_lines > existing_lines:
                    merged[key] = period
        # stable sort: company first, then by fy desc
        def _sort_key(p):
            fy = p["fy"]
            year = int(fy[2:]) if fy[2:].isdigit() else 0
            return (0 if p["perimeter"] == "company" else 1, -year)
        return sorted(merged.values(), key=_sort_key)

    @staticmethod
    def _build_summary(
        profile: CorporateProfile,
        extractions: List[FSExtraction],
        periods: List[Dict[str, Any]],
        classified: List[ClassifiedFile],
    ) -> Dict[str, Any]:
        # quick pulled metrics for the demo dashboard
        get = lambda period, code: next(
            (l["amount"] for l in period["statements"]["sofp"] if l["canonical_code"] == code),
            None,
        )
        get_pl = lambda period, code: next(
            (l["amount"] for l in period["statements"]["soci"] if l["canonical_code"] == code),
            None,
        )
        fy_metrics = []
        for p in periods:
            fy_metrics.append({
                "perimeter": p["perimeter"],
                "fy": p["fy"],
                "total_assets":   get(p, "bs_total_assets"),
                "total_liab":     get(p, "bs_total_liab"),
                "total_equity":   get(p, "bs_total_equity"),
                "cash":           get(p, "bs_cash"),
                "revenue":        get_pl(p, "pl_revenue"),
                "pat":            get_pl(p, "pl_pat") or get_pl(p, "pl_pbt"),
                "lines_captured": sum(len(v) for v in p["statements"].values()),
            })
        return {
            "entity": profile.entity_name,
            "uen": profile.uen,
            "ssic": f"{profile.primary_ssic_code} — {profile.primary_ssic_desc}".strip(" —"),
            "audited": profile.audited,
            "small_co_exempt": profile.small_company_exemption,
            "paid_up_capital_sgd": profile.paid_up_capital_amount,
            "charges_count": len(profile.charges),
            "files": {
                "total": len(classified),
                "by_type": _count_by_type(classified),
            },
            "fs_periods_captured": len(periods),
            "fy_metrics": fy_metrics,
        }



def _count_by_type(classified: List[ClassifiedFile]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for c in classified:
        out[c.source_type] = out.get(c.source_type, 0) + 1
    return out
