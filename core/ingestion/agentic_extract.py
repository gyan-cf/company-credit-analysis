"""
Vision-LLM extractor for SG financial-statement PDFs.

POC implementation: render every PDF page as an image, send the full page set
to Claude in one structured-output call, get back a `document.json` that
already conforms to `schemas/sg_fs_document_schema.json`.

Why this exists: the regex/heuristic pipeline (fs_text_extract + fs_ocr_extract
+ narrative_extract + canonical_map) is ~1500 lines of brittle code that has to
be patched for every new filing layout. A vision-LLM extractor unifies the
text and OCR paths, handles wrapping / mixed casing / OCR noise naturally,
and only has to be tuned via the prompt.

Public entrypoint: `extract_document_via_llm(pdf_path, ...) -> Dict[str, Any]`
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


DEFAULT_PROVIDER = "openai"

# Default per-provider models.
DEFAULT_MODELS: Dict[str, str] = {
    # GPT-4o has solid vision + JSON-tool output and is what the repo's
    # OpenAI credits cover.
    "openai":    "gpt-4o",
    # Sonnet 4.6 has the best vision + structured output at the moment;
    # used when ANTHROPIC_API_KEY is set and the account has credits.
    "anthropic": "claude-sonnet-4-6",
}

# Page-render scale. 2.0 ≈ 144 DPI — enough for clean table reading on z124
# renders and OCR'd UFS PDFs.
DEFAULT_SCALE = 2.0

# Max tokens for the model's structured response. 16384 is the gpt-4o output
# ceiling; Anthropic accepts up to 8192 on Sonnet without extended-output.
DEFAULT_MAX_TOKENS = 16384


# ---- PDF → page images --------------------------------------------------------

def _render_pdf_to_png_bytes(pdf_path: Path, scale: float = DEFAULT_SCALE) -> List[bytes]:
    """Render each PDF page to PNG bytes via pypdfium2."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(pdf_path))
    pages: List[bytes] = []
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            try:
                img = page.render(scale=scale).to_pil()
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                pages.append(buf.getvalue())
            finally:
                page.close()
    finally:
        pdf.close()
    return pages


# ---- Tool schema --------------------------------------------------------------

def _load_document_schema() -> Dict[str, Any]:
    schema_path = Path(__file__).resolve().parents[2] / "schemas" / "sg_fs_document_schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _build_tool_input_schema() -> Dict[str, Any]:
    """
    Build the JSON Schema passed to Anthropic's tool definition.

    We strip the meta fields the Anthropic API rejects (`$schema`, `$id`,
    top-level `title`/`description`) but keep `$defs` + `$ref` — modern
    Anthropic models handle those natively.
    """
    schema = _load_document_schema()
    schema = dict(schema)
    for key in ("$schema", "$id", "title", "description"):
        schema.pop(key, None)
    return schema


# ---- Prompt -------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an extraction agent for Singapore financial-statement filings — SFRS(I)
and SFRS, audited or unaudited. You receive every page of a single FS PDF as
images. You must extract the full document into one structured object using
the `emit_document` tool.

DOCUMENT STRUCTURE (extract in PDF order):
1. Cover page                          — kind: "cover"
2. Corporate information (if present)  — kind: "corporate_info"
3. Directors' Statement                — kind: "directors_statement"
4. Independent Auditor's Report        — kind: "auditor_report"   (omit for UFS)
5. Statement of Financial Position     — kind: "statement", type: "sofp"
6. Statement of Profit or Loss + OCI   — kind: "statement", type: "soci"
7. Statement of Changes in Equity      — kind: "statement", type: "soce"
8. Statement of Cash Flows             — kind: "statement", type: "socf"
9. Notes to the financial statements   — kind: "notes", with items[]

DOCUMENT META:
- entity.name: full legal name verbatim from the cover (e.g. "GOIMPACT CAPITAL PARTNERS (SINGAPORE) PTE. LTD.")
- entity.uen: SG UEN — number+letter (e.g. 202037175R) or LLP form (e.g. T15LP0001A)
- fye: full text from cover ("31 December 2024")
- fy: "FY" + 4-digit year of the primary FYE
- framework: "SFRS(I)" if you see "Singapore Financial Reporting Standards (International)" / "SFRS(I)"; "SFRS" if plain Singapore Financial Reporting Standards; "IFRS" otherwise
- audited: true if there's an Independent Auditor's Report; false if the cover says "UNAUDITED" or there's no auditor's report
- consolidated: true if the entity has subsidiaries ("AND ITS SUBSIDIARY/SUBSIDIARIES" on cover, or the statements have paired Group + Company columns)
- currency: 3-letter ISO code from the statements (typically SGD)
- currency_unit: verbatim presentation note (e.g. "S$", "S$'000", "SGD millions")
- extraction_method: always "agentic"

STATEMENT BLOCKS:
- title: verbatim section heading from the PDF
- page_range: [first_page, last_page] inclusive, 1-indexed
- columns: one entry per (perimeter, FY) pair seen in the table header
  - id: convention "<perimeter>_<fy>" (e.g. "group_FY2024")
  - perimeter: "group" or "company"
  - fy: "FY2024" etc.
  - period_end: full date string if present in the column header
- rows: every row visible in the table, in original order
  - row_type: one of "section_header" | "line" | "subtotal" | "total" | "spacer"
    * section_header — a label-only banner like "Assets", "Current Assets", "Liabilities"
    * subtotal       — within-section sum (e.g. "Total current assets", "Gross profit", "PBT", "Net cash from operating activities")
    * total          — top-level total (e.g. "Total assets", "Total liabilities", "Total equity", "PAT", "Cash at end of period")
    * line           — every other numbered row
  - label: human-friendly label (Title Case, no trailing punctuation)
  - canonical_code: pick from the reference list below if the row matches; otherwise null
  - values: keyed by column id; null for blank cells; numeric for cell values
    * NEGATIVE values: render as negative numbers (e.g. -1234.56) — do NOT use parentheses in the JSON; the source shows "(1,234.56)" but you emit -1234.56
    * Do not round — emit the exact figure from the PDF
  - indent_level: visual indent depth from the PDF, 0 = top-level
  - section_path: parent → child section names (e.g. ["Assets", "Current Assets"])
  - note_ref: the note number this row references, as a string ("4", "12"); null if none
  - page: the PDF page this row appears on

OUTPUT-SIZE RULES (CRITICAL — must stay under 16 000 output tokens):
  - Omit OPTIONAL fields when the value is null / unknown / derivable.
    Optional fields you SHOULD omit unless they add real info: raw_label,
    display_order, confidence, flags. The schema validator will accept rows
    without them.
  - Narrative markdown: keep auditor_report / directors_statement to ≤ 4
    short paragraphs each. Notes markdown: ≤ 3 short paragraphs per note.
    Capture the key facts, not the boilerplate.

STRUCTURAL RULES (will fail validation otherwise):
  - `page_range` MUST be a 2-element array: [first_page, last_page]. Use
    [N, N] for a single-page block. Never emit [N] alone.
  - Every numbered note belongs INSIDE the single `kind: "notes"` block's
    `items[]` array. Never emit note objects as top-level siblings of the
    NotesBlock. If you run out of room, prefer trimming markdown over
    spilling notes outside.
  - Note numbering: integer for plain notes ("1", "2"); string for sub-
    sections ("2a", "3a"); dotted decimals like "2.1" are acceptable.
  - Block ORDER is PDF order: cover → corporate_info → directors_statement
    → auditor_report → statements → notes.

CANONICAL CODE REFERENCE — assign when the row's label matches the meaning:

SoFP (`bs_*`):
  bs_cash               Cash and cash equivalents / cash at bank
  bs_trade_recv         Trade receivables / trade debtors
  bs_other_recv         Other receivables
  bs_trade_other_recv   Trade and other receivables (combined)
  bs_prepayments        Prepayments
  bs_inventory          Inventories
  bs_amt_due_related    Amounts due from related parties / holding co
  bs_total_ca           Total current assets
  bs_ppe                Property, plant and equipment
  bs_rou_asset          Right-of-use asset
  bs_intangibles        Intangible assets, goodwill
  bs_inv_subsidiary     Investment in subsidiary/subsidiaries
  bs_inv_associate      Investment in associate
  bs_deferred_tax_a     Deferred tax assets
  bs_total_nca          Total non-current assets
  bs_total_assets       Total assets
  bs_trade_pay          Trade payables
  bs_other_pay          Other payables
  bs_trade_other_pay    Trade and other payables (combined)
  bs_borrowings_st      Short-term/current borrowings
  bs_lease_liab_st      Current lease liabilities
  bs_current_tax        Current tax payable
  bs_total_cl           Total current liabilities
  bs_borrowings_lt      Non-current/long-term borrowings
  bs_lease_liab_lt      Non-current lease liabilities
  bs_deferred_tax_l     Deferred tax liabilities
  bs_total_ncl          Total non-current liabilities
  bs_total_liab         Total liabilities
  bs_net_assets         Net assets
  bs_share_capital      Share capital
  bs_capital_reserve    Capital reserve
  bs_translation_res    Translation / foreign currency reserve
  bs_other_reserves     Other reserves
  bs_retained           Retained earnings / retained profits
  bs_accum_losses       Accumulated losses / accumulated deficit
  bs_total_equity       Total equity / shareholders' equity

SoCI (`pl_*`):
  pl_revenue            Revenue / turnover
  pl_other_income       Other income
  pl_cost_of_sales      Cost of sales / cost of goods sold
  pl_gross_profit       Gross profit
  pl_employee_exp       Employee benefit / staff costs
  pl_professional_fees  Professional fees
  pl_service_fees       Service fees
  pl_rental_exp         Rental expense
  pl_depreciation       Depreciation
  pl_amortisation       Amortisation
  pl_other_op_exp       Other operating expenses
  pl_fx                 Exchange / FX gain or loss
  pl_finance_costs      Finance cost / interest expense
  pl_finance_income     Finance income / interest income
  pl_total_expenses     Total expenses
  pl_pbt                Profit / (loss) before income tax
  pl_tax                Income tax expense / (credit)
  pl_pat                Profit / (loss) for the (financial) year
  pl_oci                Other comprehensive income
  pl_tci                Total comprehensive income

SoCF (`cf_*`):
  cf_operating          Net cash from / (used in) operating activities
  cf_investing          Net cash from / (used in) investing activities
  cf_financing          Net cash from / (used in) financing activities
  cf_capex              Purchase of property, plant and equipment / capex
  cf_proceeds_borrow    Proceeds from borrowings
  cf_repay_borrow       Repayment of borrowings
  cf_interest_paid      Interest paid
  cf_tax_paid           Income tax paid
  cf_dividends_paid     Dividends paid
  cf_net_change_cash    Net change in cash / net increase / decrease in cash
  cf_cash_beg           Cash at beginning of period
  cf_cash_end           Cash at end of period

If you can't confidently match a row to a code, set canonical_code to null. Do
not stretch matches — a wrong canonical_code is worse than null.

NOTES:
- One NotesBlock with all numbered notes inside `items[]`
- Note 1 is conventionally "Corporate information" → subkind: "corporate_info"
- Note 2 is conventionally "Summary of significant accounting policies" → subkind: "policies"
- All other notes → subkind: "note"
- `no`: integer for "1", "2"; string for "3a", "12b"
- `title`: REQUIRED — verbatim heading from the PDF (e.g. "Revenue", "Property, Plant and Equipment", "Related Party Transactions"). Never omit.
- `markdown`: the note's prose, preserving paragraph breaks (use \\n\\n)
- `tables`: tabular schedules inside a note (PPE roll-forward, borrowings maturity,
  FX exposure, share-capital movement, related-party transactions, subsidiary list).
  STRICT SHAPE — follow this exactly:

  {
    "caption": "FX exposure by currency",
    "columns": [
      {"id": "group_FY2024", "label": "Group 2024 S$",   "type": "number"},
      {"id": "group_FY2023", "label": "Group 2023 S$",   "type": "number"},
      {"id": "company_FY2024", "label": "Company 2024 S$", "type": "number"},
      {"id": "company_FY2023", "label": "Company 2023 S$", "type": "number"},
      {"id": "currency",       "label": "Currency",         "type": "text"}
    ],
    "rows": [
      {"currency": "Hong Kong dollar",    "group_FY2024": 116215, "group_FY2023": 161365, "company_FY2024": 202381, "company_FY2023": 206530},
      {"currency": "Malaysian ringgit",   "group_FY2023": 4601,    "company_FY2023": 4601},
      {"currency": "Chinese yuan",        "group_FY2024": 902,     "group_FY2023": 898,    "company_FY2024": 902, "company_FY2023": 898}
    ]
  }

  - `columns[]` must be an array of OBJECTS, not strings. Each column object
    has `id` (short snake_case key), `label` (verbatim from PDF), `type`
    ("text" | "number" | "date").
  - `rows[]` must be an array of FLAT dicts whose keys exactly match column ids.
    Missing cells are simply omitted. Never nest dicts inside row values.
  - Numbers are JSON numbers (no commas, no currency symbols, no quotes).

SoFP balance identity (helps your own consistency):
- If the entity has no non-current liabilities, emit a `bs_total_liab` row equal to `bs_total_cl` so the accounting identity `total_assets == total_liab + total_equity` holds.
- Never label "Total Liabilities and Equity" as `bs_total_assets` — that line should have `canonical_code: null` (it's a presentational total, not a balance-sheet item).

GENERAL RULES:
- Extract every visible row — do not skip anything you can read
- Preserve exact numerical values from the PDF
- Never invent figures or labels
- Use null, not zero, for missing values
- The document MUST validate against the emit_document schema
"""


USER_PROMPT = (
    "Extract this Singapore financial-statement filing into the emit_document "
    "tool input. Read every page; emit every block in document order."
)


# ---- Provider routing ---------------------------------------------------------

def _resolve_provider(provider: Optional[str]) -> str:
    """If caller didn't specify, follow OPENAI_API_KEY / ANTHROPIC_API_KEY."""
    if provider:
        return provider.lower()
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    return DEFAULT_PROVIDER


# ---- OpenAI path --------------------------------------------------------------

def _openai_user_content(pages_png: List[bytes]) -> List[Dict[str, Any]]:
    """Mix page images (data URLs) + the user prompt into the chat content array."""
    content: List[Dict[str, Any]] = []
    for png in pages_png:
        data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        content.append({
            "type": "image_url",
            "image_url": {"url": data_url, "detail": "high"},
        })
    content.append({"type": "text", "text": USER_PROMPT})
    return content


def _extract_via_openai(
    pages_png: List[bytes],
    *,
    model: str,
    max_tokens: int,
    api_key: Optional[str],
) -> Dict[str, Any]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
    tool = {
        "type": "function",
        "function": {
            "name": "emit_document",
            "description": (
                "Emit the structured Singapore financial-statement document. "
                "Arguments must conform to the SG FS document schema."
            ),
            "parameters": _build_tool_input_schema(),
        },
    }
    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _openai_user_content(pages_png)},
        ],
        tools=[tool],
        tool_choice={"type": "function", "function": {"name": "emit_document"}},
    )
    choice = response.choices[0]
    tool_calls = (choice.message.tool_calls or []) if choice.message else []
    if not tool_calls:
        raise RuntimeError(
            f"OpenAI did not return an emit_document tool call. "
            f"finish_reason={choice.finish_reason}, "
            f"content={(choice.message.content or '')[:200]!r}"
        )
    args = tool_calls[0].function.arguments
    try:
        doc = json.loads(args)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"OpenAI tool arguments were not valid JSON: {e}") from e

    usage = getattr(response, "usage", None)
    if usage is not None:
        logger.info(
            "agentic_extract usage (openai): prompt=%s completion=%s total=%s",
            getattr(usage, "prompt_tokens", "?"),
            getattr(usage, "completion_tokens", "?"),
            getattr(usage, "total_tokens", "?"),
        )
    return doc


# ---- Anthropic path -----------------------------------------------------------

def _anthropic_user_content(pages_png: List[bytes]) -> List[Dict[str, Any]]:
    content: List[Dict[str, Any]] = []
    for png in pages_png:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.b64encode(png).decode("ascii"),
            },
        })
    content.append({"type": "text", "text": USER_PROMPT})
    return content


def _extract_via_anthropic(
    pages_png: List[bytes],
    *,
    model: str,
    max_tokens: int,
    api_key: Optional[str],
) -> Dict[str, Any]:
    from anthropic import Anthropic

    client = Anthropic(api_key=api_key or os.getenv("ANTHROPIC_API_KEY"))
    tool = {
        "name": "emit_document",
        "description": (
            "Emit the structured Singapore financial-statement document. "
            "The input must conform to the SG FS document schema."
        ),
        "input_schema": _build_tool_input_schema(),
    }
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=SYSTEM_PROMPT,
        tools=[tool],
        tool_choice={"type": "tool", "name": "emit_document"},
        messages=[{"role": "user", "content": _anthropic_user_content(pages_png)}],
    )
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "emit_document":
            doc = block.input
            usage = getattr(response, "usage", None)
            if usage is not None:
                logger.info(
                    "agentic_extract usage (anthropic): input=%s output=%s",
                    getattr(usage, "input_tokens", "?"),
                    getattr(usage, "output_tokens", "?"),
                )
            if isinstance(doc, dict):
                return doc
    raise RuntimeError(
        f"Anthropic did not return an emit_document tool call. "
        f"stop_reason={getattr(response, 'stop_reason', '?')}, "
        f"content types={[getattr(b, 'type', '?') for b in response.content]}"
    )


# ---- Public entrypoint --------------------------------------------------------

def extract_document_via_llm(
    pdf_path: Path,
    *,
    source_id: str,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    scale: float = DEFAULT_SCALE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Render PDF pages, call a vision LLM, return a schema-conformant document.

    `source_id` is pinned by the caller (content-hash) — the model is never
    asked to invent it. `source_pdf` is set to the file's basename and
    `extraction_method` is forced to "agentic".

    Provider defaults to whichever API key is configured (OPENAI first,
    ANTHROPIC second). Override via `provider=` if both are set.

    Raises:
        FileNotFoundError if `pdf_path` is missing.
        RuntimeError      if the model returns no tool call or the response
                          is structurally unusable.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    pages_png = _render_pdf_to_png_bytes(pdf_path, scale=scale)
    resolved_provider = _resolve_provider(provider)
    resolved_model = model or DEFAULT_MODELS.get(resolved_provider) or DEFAULT_MODELS[DEFAULT_PROVIDER]
    logger.info(
        "agentic_extract: %s -> %d page(s), provider=%s, model=%s",
        pdf_path.name, len(pages_png), resolved_provider, resolved_model,
    )

    if resolved_provider == "openai":
        doc = _extract_via_openai(
            pages_png, model=resolved_model, max_tokens=max_tokens, api_key=api_key,
        )
    elif resolved_provider == "anthropic":
        doc = _extract_via_anthropic(
            pages_png, model=resolved_model, max_tokens=max_tokens, api_key=api_key,
        )
    else:
        raise ValueError(f"Unknown provider: {resolved_provider!r}")

    # Pin caller-controlled metadata so the model can't drift on them.
    doc.setdefault("document", {})
    doc["document"]["source_id"] = source_id
    doc["document"]["source_pdf"] = pdf_path.name
    doc["document"]["extraction_method"] = "agentic"
    _backfill_schema_required(doc)
    return doc


def _looks_like_note_item(block: Dict[str, Any]) -> bool:
    """True if a top-level block is actually an orphan note item (model overflowed)."""
    if not isinstance(block, dict):
        return False
    if block.get("kind") in {"cover", "corporate_info", "directors_statement",
                              "auditor_report", "statement", "notes"}:
        return False
    return "no" in block and "title" in block


def _normalise_page_range(pr: Any) -> Optional[List[int]]:
    """Single-page [N] → [N, N]; pass [a, b] through; invalid → None."""
    if isinstance(pr, list) and len(pr) == 1 and isinstance(pr[0], int):
        return [pr[0], pr[0]]
    if isinstance(pr, list) and len(pr) == 2 and all(isinstance(p, int) for p in pr):
        return list(pr)
    return None


def _backfill_schema_required(doc: Dict[str, Any]) -> None:
    """
    Patch up the LLM output to keep it schema-valid:

    1. Normalise single-element `page_range` to a 2-element [N, N].
    2. Move any top-level orphan note-item blocks into the NotesBlock.items[]
       (the model sometimes overflows and emits later notes as siblings).
    3. Backfill `title` on note items when the model omits it.
    """
    blocks = doc.get("blocks") or []

    # 1. Normalise page_range on every block + note item.
    def _fix_pr(obj: Dict[str, Any]) -> None:
        pr = obj.get("page_range")
        norm = _normalise_page_range(pr)
        if norm:
            obj["page_range"] = norm
        elif pr is not None:
            obj.pop("page_range", None)

    for b in blocks:
        if isinstance(b, dict):
            _fix_pr(b)
            for it in (b.get("items") or []):
                if isinstance(it, dict):
                    _fix_pr(it)
            # Statement rows: indent_level is required by the schema but the
            # model frequently omits it for top-level rows. Default to 0.
            if b.get("kind") == "statement":
                for row in (b.get("rows") or []):
                    if isinstance(row, dict) and "indent_level" not in row:
                        row["indent_level"] = 0

    # 2. Move orphan note items into the NotesBlock.
    notes_block = next((b for b in blocks if isinstance(b, dict) and b.get("kind") == "notes"), None)
    orphans = [b for b in blocks if _looks_like_note_item(b)]
    if orphans:
        if notes_block is None:
            notes_block = {"kind": "notes", "items": []}
            blocks.append(notes_block)
        notes_block.setdefault("items", [])
        notes_block["items"].extend(orphans)
        doc["blocks"] = [b for b in blocks if b is notes_block or not _looks_like_note_item(b)]

    # 3. Backfill note titles + subkinds + normalise note tables.
    for block in doc.get("blocks") or []:
        if block.get("kind") != "notes":
            continue
        for item in block.get("items") or []:
            if not item.get("subkind"):
                no = item.get("no")
                if no == 1:
                    item["subkind"] = "corporate_info"
                elif no == 2:
                    item["subkind"] = "policies"
                else:
                    item["subkind"] = "note"
            if not item.get("title"):
                subkind = item.get("subkind", "note")
                no = item.get("no")
                if subkind == "corporate_info":
                    item["title"] = "Corporate information"
                elif subkind == "policies":
                    item["title"] = "Summary of significant accounting policies"
                else:
                    item["title"] = f"Note {no}" if no is not None else "Note"
            tables = item.get("tables")
            if isinstance(tables, list):
                item["tables"] = [_normalise_note_table(t) for t in tables if isinstance(t, dict)]


def _slugify(s: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")
    return out or "col"


def _coerce_cell(v: Any) -> Any:
    """String numbers like '1,234' or '(123.45)' → numeric; keep null/text/already-numeric."""
    if v is None or isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        s = v.strip()
        if not s or s == "-":
            return None
        neg = s.startswith("(") and s.endswith(")")
        if neg:
            s = s[1:-1]
        s2 = s.replace(",", "").replace("$", "").replace("S$", "").strip()
        try:
            n = float(s2)
            return -n if neg else n
        except ValueError:
            return v
    return v


def _normalise_note_table(table: Dict[str, Any]) -> Dict[str, Any]:
    """
    Coerce a model-emitted note table into the schema's expected shape:
      - `columns[]` becomes an array of {id, label, type} objects (string entries
        are upgraded to objects with a slug id and the string as the label)
      - `rows[]` becomes a flat list of {column_id: value} dicts. If the model
        emitted nested {row_label: {col: val, ...}}, we flatten and tuck the
        row label into a synthetic 'label' column.
      - Cell strings like "1,234" / "(987)" are coerced to numbers.
    """
    cols_in = table.get("columns") or []
    columns: List[Dict[str, Any]] = []
    for c in cols_in:
        if isinstance(c, str):
            columns.append({"id": _slugify(c), "label": c, "type": "text"})
        elif isinstance(c, dict):
            cid = c.get("id") or _slugify(c.get("label") or "col")
            columns.append({
                "id":    cid,
                "label": c.get("label") or cid,
                "type":  c.get("type") or "text",
            })

    rows_in = table.get("rows") or []
    rows: List[Dict[str, Any]] = []
    needs_label_col = False
    for r in rows_in:
        if not isinstance(r, dict):
            continue
        # Nested form: {row_label: {col_label: val, ...}} — flatten.
        flat: Dict[str, Any] = {}
        for k, v in r.items():
            if isinstance(v, dict):
                needs_label_col = True
                flat["__row_label__"] = k
                for ck, cv in v.items():
                    flat[ck] = cv
            else:
                flat[k] = v
        # Map any column-label keys to their slug ids.
        col_id_by_label = {c["label"]: c["id"] for c in columns}
        normalised: Dict[str, Any] = {}
        for k, v in flat.items():
            if k == "__row_label__":
                normalised["label"] = v
            elif k in col_id_by_label:
                normalised[col_id_by_label[k]] = _coerce_cell(v)
            else:
                normalised[_slugify(k)] = _coerce_cell(v)
        rows.append(normalised)

    if needs_label_col and not any(c["id"] == "label" for c in columns):
        columns.insert(0, {"id": "label", "label": "", "type": "text"})

    out: Dict[str, Any] = {"columns": columns, "rows": rows}
    if table.get("caption"):
        out["caption"] = table["caption"]
    if table.get("footnote"):
        out["footnote"] = table["footnote"]
    return out


# ---- Cross-check validators ---------------------------------------------------

def _row_value(row: Dict[str, Any], col_id: str) -> Optional[float]:
    v = (row.get("values") or {}).get(col_id)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def cross_check_document(doc: Dict[str, Any], tolerance: float = 1.0) -> List[Dict[str, Any]]:
    """
    Numeric sanity checks against a document dict. Returns review-flag dicts —
    one per identified inconsistency. Empty list = clean.

    Checks per statement / per column:
      - SoFP: total_assets ≈ sum(current_assets + non_current_assets) at the row level
      - SoFP: total_assets ≈ total_liab + total_equity (the accounting identity)

    Tolerance handles rounding differences when statements are presented in
    thousands or millions.
    """
    flags: List[Dict[str, Any]] = []
    blocks = doc.get("blocks", []) or []
    for block in blocks:
        if block.get("kind") != "statement" or block.get("type") != "sofp":
            continue
        rows = block.get("rows", []) or []
        columns = [c.get("id") for c in (block.get("columns") or []) if c.get("id")]
        codes_by_row: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            code = row.get("canonical_code")
            if code:
                codes_by_row[code] = row

        total_assets_row = codes_by_row.get("bs_total_assets")
        total_liab_row = codes_by_row.get("bs_total_liab")
        total_cl_row = codes_by_row.get("bs_total_cl")
        total_ncl_row = codes_by_row.get("bs_total_ncl")
        total_equity_row = codes_by_row.get("bs_total_equity") or codes_by_row.get("bs_net_assets")

        for col in columns:
            ta = _row_value(total_assets_row, col) if total_assets_row else None
            tl = _row_value(total_liab_row, col) if total_liab_row else None
            # If bs_total_liab is absent, treat sum(bs_total_cl, bs_total_ncl)
            # as the liabilities total — many SG SMEs without LT borrowings
            # only show a single Total Current Liabilities line.
            if tl is None:
                cl = _row_value(total_cl_row, col) if total_cl_row else None
                ncl = _row_value(total_ncl_row, col) if total_ncl_row else None
                if cl is not None or ncl is not None:
                    tl = (cl or 0.0) + (ncl or 0.0)
            te = _row_value(total_equity_row, col) if total_equity_row else None
            if ta is None or (tl is None and te is None):
                continue
            rhs = (tl or 0.0) + (te or 0.0)
            if abs(ta - rhs) > tolerance:
                flags.append({
                    "severity": "high",
                    "kind": "cross_check_sofp_identity",
                    "message": (
                        f"SoFP identity violated for {col}: "
                        f"total_assets={ta:,.2f}, total_liab+total_equity={rhs:,.2f}, "
                        f"delta={ta - rhs:,.2f}"
                    ),
                    "column": col,
                    "total_assets": ta,
                    "total_liab": tl,
                    "total_equity": te,
                })
    return flags
