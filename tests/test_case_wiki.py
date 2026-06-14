import json
from pathlib import Path

from core.knowledge import build_case_wiki, search_case_wiki
from core.knowledge.agentic_wiki import build_agentic_wiki_document


PDF_BYTES = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>
endobj
4 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
5 0 obj
<< /Length 84 >>
stream
BT
/F1 12 Tf
72 720 Td
(Revenue platform services and liquidity note) Tj
ET
endstream
endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000241 00000 n 
0000000311 00000 n 
trailer
<< /Size 6 /Root 1 0 R >>
startxref
445
%%EOF
"""


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_build_case_wiki_from_financial_document(tmp_path):
    case_root = tmp_path / "case"
    source_id = "src123"
    parsed = case_root / "parsed" / "financials"
    raw_pdf = case_root / "raw" / "financials" / "FY2024.pdf"
    raw_pdf.parent.mkdir(parents=True, exist_ok=True)
    raw_pdf.write_bytes(PDF_BYTES)

    _write_json(parsed / "index.json", {
        "sources": [{
            "source_id": source_id,
            "original_filename": "FY2024.pdf",
            "document_json": f"{source_id}/document.json",
            "wiki_document": f"{source_id}/wiki_document.json",
        }],
        "blocks": [],
    })
    _write_json(parsed / source_id / "manifest.json", {
        "source_id": source_id,
        "original_filename": "FY2024.pdf",
        "entity": {"name": "Demo Pte Ltd", "framework": "SFRS"},
        "currency": "SGD",
        "audited": True,
        "consolidated": False,
        "original_path": "raw/financials/FY2024.pdf",
    })
    _write_json(parsed / source_id / "document.json", {
        "document": {"source_id": source_id},
        "blocks": [
            {
                "kind": "statement",
                "type": "soci",
                "title": "Statement of Comprehensive Income",
                "page_range": [5, 6],
                "columns": [{"id": "company_FY2024", "label": "FY2024"}],
                "rows": [{
                    "label": "Revenue",
                    "canonical_code": "pl_revenue",
                    "note_ref": "3",
                    "values": {"company_FY2024": 1250000},
                    "page": 5,
                    "confidence": 0.98,
                }],
            },
            {
                "kind": "notes",
                "page_range": [10, 20],
                "items": [{
                    "note_no": "3",
                    "title": "Revenue",
                    "page_range": [12, 13],
                    "markdown": "Revenue is recognised over time for platform services.",
                }],
            },
        ],
    })
    doc = json.loads((parsed / source_id / "document.json").read_text(encoding="utf-8"))
    wiki_doc = build_agentic_wiki_document(doc, source_id=source_id, source_file="FY2024.pdf")
    _write_json(parsed / source_id / "wiki_document.json", wiki_doc)

    summary = build_case_wiki(case_root)

    assert summary["page_count"] >= 4
    assert summary["chunk_count"] == summary["page_count"]
    assert summary["evidence_count"] >= 2
    assert summary["note_link_count"] == 1
    assert (case_root / "wiki" / "sources" / "src123" / "agentic-page-005.md").exists()
    assert (case_root / "wiki" / "sources" / "src123" / "page-001.md").exists()
    assert (case_root / "wiki" / "statements" / "src123-soci.md").exists()
    assert (case_root / "wiki" / "notes" / "src123-note-3.md").exists()
    note_links = json.loads((case_root / "wiki" / "note_links.json").read_text(encoding="utf-8"))
    assert note_links["links"][0]["row_label"] == "Revenue"
    note_md = (case_root / "wiki" / "notes" / "src123-note-3.md").read_text(encoding="utf-8")
    assert "Linked Financial Statement Rows" in note_md

    results = search_case_wiki(case_root, "revenue platform services")
    assert results["count"] >= 1
    assert any("Revenue" in (r["title"] or "") for r in results["results"])
