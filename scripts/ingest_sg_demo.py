#!/usr/bin/env python3
"""
End-to-end demo of the Singapore ACRA / FS ingestion pipeline.

Usage:
    python scripts/ingest_sg_demo.py                    # uses input/financials/
    python scripts/ingest_sg_demo.py path/to/folder
    python scripts/ingest_sg_demo.py path/to/folder --output cases/demo/parsed/

Outputs a JSON summary on stdout and (optionally) the full result to disk.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Allow running from project root without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.ingestion import SGIngestionPipeline


def main():
    parser = argparse.ArgumentParser(description="Run the Singapore ACRA / FS ingestion pipeline.")
    parser.add_argument("path", nargs="?", default="input/financials",
                        help="Folder containing ACRA / FS files (default: input/financials)")
    parser.add_argument("--output", "-o", default=None,
                        help="If set, write the full IngestionResult JSON here")
    parser.add_argument("--no-ocr", action="store_true", help="Disable OCR fallback for UFS PDFs")
    parser.add_argument("--no-zip", action="store_true", help="Do not expand zip archives")
    parser.add_argument("--ocr-dpi", type=int, default=300, help="OCR rasterisation DPI (default 300)")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress progress logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    pipeline = SGIngestionPipeline(
        expand_zips=not args.no_zip,
        ocr_enabled=not args.no_ocr,
        ocr_dpi=args.ocr_dpi,
    )

    print(f"\n[ingest] Scanning: {args.path}")
    result = pipeline.ingest_path(Path(args.path))
    data = result.to_dict()

    _print_human_summary(data)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        print(f"\n[ingest] Full result written to {out_path}")

    return 0


def _print_human_summary(data: dict) -> None:
    summ = data.get("summary", {})
    print("\n" + "=" * 78)
    print(" INGESTION SUMMARY")
    print("=" * 78)
    print(f" Entity        : {summ.get('entity') or '—'}")
    print(f" UEN           : {summ.get('uen') or '—'}")
    print(f" SSIC          : {summ.get('ssic') or '—'}")
    print(f" Audited       : {summ.get('audited')}")
    print(f" Small co exempt: {summ.get('small_co_exempt')}")
    if summ.get("paid_up_capital_sgd") is not None:
        print(f" Paid-up cap.  : S${summ['paid_up_capital_sgd']:,.0f}")
    print(f" Charges       : {summ.get('charges_count', 0)}")
    files = summ.get("files", {})
    print(f" Files         : {files.get('total', 0)} → {files.get('by_type', {})}")

    print("\n FY METRICS (perimeter / FY) ----------------------------------")
    metrics = summ.get("fy_metrics", [])
    if metrics:
        print(f"  {'Perim':<8} {'FY':<8} {'Revenue':>14} {'PAT':>14} {'Tot.Assets':>14} {'Equity':>14}  Lines")
        for m in metrics:
            def fmt(v):
                return f"{v:>14,.0f}" if isinstance(v, (int, float)) and v is not None else " " * 14
            print(f"  {m['perimeter']:<8} {m['fy']:<8} "
                  f"{fmt(m.get('revenue'))} {fmt(m.get('pat'))} "
                  f"{fmt(m.get('total_assets'))} {fmt(m.get('total_equity'))} "
                  f" {m.get('lines_captured', 0)}")
    else:
        print("  (no FS periods captured)")

    flags = data.get("review_flags", [])
    if flags:
        print("\n REVIEW FLAGS -------------------------------------------------")
        for f in flags[:20]:
            print(f"  [{f.get('severity', '?'):>6}] {f.get('source', '?')}: {f.get('message')}")
        if len(flags) > 20:
            print(f"  ... +{len(flags) - 20} more")

    errs = data.get("errors", [])
    if errs:
        print("\n ERRORS -------------------------------------------------------")
        for e in errs:
            print(f"  {e.get('file')}: {e.get('error')}")
    print("=" * 78 + "\n")


if __name__ == "__main__":
    sys.exit(main())
