"""
Corporate profile extractor for ACRA documents.

Handles:
    EXT_<UEN>_C<docid>_C223_1.pdf   (Annual Return Form C223)
    EXT_<UEN>_T<docid>_BM42A_1.pdf  (Annual filing cover sheet)

These are short, text-extractable PDFs.  We pull entity profile, directors,
shareholders, secretaries, auditors, registered charges, paid-up capital,
audit-exemption status, AGM info — everything the credit narrative needs that
isn't in the FS itself.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)
logging.getLogger("pdfminer").setLevel(logging.ERROR)

UEN_RE = re.compile(r"\b(\d{8,10}[A-Z])\b")
DATE_RE = re.compile(r"(\d{1,2}\s+\w+\s+\d{4}|\d{2}/\d{2}/\d{4})")
SSIC_RE = re.compile(r"\b(\d{5})\b(?:\s*-\s*|\s+)([A-Za-z][^\n]{2,80})")


@dataclass
class CorporateProfile:
    uen: str = ""
    entity_name: str = ""
    entity_type: str = ""
    entity_status: str = ""
    incorporation_date: str = ""
    fye: str = ""
    primary_ssic_code: str = ""
    primary_ssic_desc: str = ""
    registered_address: str = ""
    company_type: str = ""
    company_status: str = ""
    small_company_exemption: Optional[bool] = None
    audited: Optional[bool] = None
    paid_up_capital_amount: Optional[float] = None
    paid_up_capital_currency: str = ""
    paid_up_share_class: str = ""
    consolidated_level: str = ""
    agm_required: Optional[bool] = None
    agm_date: str = ""
    accounting_standards: str = ""
    directors: List[Dict[str, str]] = field(default_factory=list)
    shareholders: List[Dict[str, str]] = field(default_factory=list)
    secretaries: List[Dict[str, str]] = field(default_factory=list)
    auditors: List[Dict[str, str]] = field(default_factory=list)
    charges: List[Dict[str, str]] = field(default_factory=list)
    source_files: List[str] = field(default_factory=list)
    review_flags: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uen": self.uen,
            "entity_name": self.entity_name,
            "entity_type": self.entity_type,
            "entity_status": self.entity_status,
            "incorporation_date": self.incorporation_date,
            "fye": self.fye,
            "primary_ssic_code": self.primary_ssic_code,
            "primary_ssic_desc": self.primary_ssic_desc,
            "registered_address": self.registered_address,
            "company_type": self.company_type,
            "company_status": self.company_status,
            "small_company_exemption": self.small_company_exemption,
            "audited": self.audited,
            "paid_up_capital": {
                "amount": self.paid_up_capital_amount,
                "currency": self.paid_up_capital_currency,
                "share_class": self.paid_up_share_class,
            } if self.paid_up_capital_amount is not None else {},
            "consolidated_level": self.consolidated_level,
            "agm": {"required": self.agm_required, "date": self.agm_date},
            "accounting_standards": self.accounting_standards,
            "directors": self.directors,
            "shareholders": self.shareholders,
            "secretaries": self.secretaries,
            "auditors": self.auditors,
            "charges": self.charges,
            "source_files": self.source_files,
            "review_flags": self.review_flags,
        }


def _read_pdf(path: Path) -> str:
    import pdfplumber
    out: List[str] = []
    with pdfplumber.open(path) as pdf:
        for p in pdf.pages:
            out.append(p.extract_text() or "")
    return "\n".join(out)


# ---- Field extractors ---------------------------------------------------------

def _grab(text: str, label: str, until: str = r"\n", flags=re.I) -> Optional[str]:
    m = re.search(rf"{re.escape(label)}\s*[:\n]+\s*([^\n]+)", text, flags)
    return m.group(1).strip() if m else None


def _grab_block_after(text: str, header: str, max_lines: int = 8) -> List[str]:
    """Return the next non-empty lines after a header line."""
    idx = text.lower().find(header.lower())
    if idx < 0:
        return []
    rest = text[idx:].split("\n")[1:]
    block: List[str] = []
    for ln in rest:
        s = ln.strip()
        if not s:
            if block:
                break
            continue
        if re.match(r"^(Authentication|File Annual|Page \d+|GOIMPACT|Section [A-Z]:)", s):
            continue
        block.append(s)
        if len(block) >= max_lines:
            break
    return block


def _parse_position_holders(text: str) -> Dict[str, List[Dict[str, str]]]:
    """
    Parse the 'Position holders' section from a BM42A cover sheet.
    Each holder block looks like:
        1. NAME - IDENTIFIER
        Position held    Date of appointment
        <position>       <date>
        Personal information
        ...
    """
    holders: Dict[str, List[Dict[str, str]]] = {
        "directors": [], "shareholders": [], "secretaries": [], "auditors": []
    }
    # Split into numbered chunks
    chunks = re.split(r"\n\s*\d+\.\s+", text)
    for chunk in chunks[1:]:  # first chunk is preamble
        first_line = chunk.split("\n", 1)[0].strip()
        m = re.match(r"^([A-Z][A-Z0-9'&.\- ]+?)\s+-\s+([A-Z0-9]+)", first_line)
        if not m:
            continue
        name = m.group(1).strip()
        ident = m.group(2).strip()
        # find Position held
        pos_m = re.search(r"Position held\s*\n([^\n]+)", chunk, re.I)
        position = pos_m.group(1).strip() if pos_m else ""
        appoint_m = re.search(r"Date of appointment\s*\n?([^\n]+)?", chunk, re.I)
        appointed = ""
        if appoint_m:
            # appointment date is usually on the *line after* "Date of appointment"
            # but may be inline; look at the chunk for the first date after the marker
            after = chunk[appoint_m.start():]
            dm = DATE_RE.search(after)
            appointed = dm.group(1) if dm else ""
        record = {"name": name, "id": ident, "position": position, "appointed": appointed}

        plow = position.lower()
        if "director" in plow:
            holders["directors"].append(record)
        elif "shareholder" in plow:
            holders["shareholders"].append(record)
        elif "secretary" in plow:
            holders["secretaries"].append(record)
        elif "auditor" in plow:
            holders["auditors"].append(record)
        elif "LLP" in name or "LLC" in name or "PTE" in name.upper():
            # treat corporate entity with no position keyword as auditor (BM42A pattern)
            holders["auditors"].append(record)
    return holders


def _parse_charges(text: str) -> List[Dict[str, str]]:
    """Parse 'Charges' block."""
    out: List[Dict[str, str]] = []
    idx = text.find("Charges")
    if idx < 0:
        return out
    region = text[idx:idx + 2000]
    # patterns:
    #   Charge Number     Date of Charge
    #   C202204733        10 Oct 2010
    #   1. THE HONGKONG AND SHANGHAI BANKING CORPORATION LIMITED
    for m in re.finditer(
        r"(C\d{5,12})\s+(\d{1,2}\s+\w+\s+\d{4})[\s\S]{0,200}?\n\s*\d+\.\s+([A-Z][A-Z0-9 &.,\-]+)",
        region,
    ):
        out.append({
            "charge_number": m.group(1).strip(),
            "date": m.group(2).strip(),
            "chargee": m.group(3).strip(),
        })
    return out


def _parse_paid_up_capital(text: str) -> Dict[str, Any]:
    """Find 'Paid-up share capital' block and pull amount/currency/class."""
    idx = text.find("Paid-up share capital")
    if idx < 0:
        return {}
    region = text[idx:idx + 600]
    # Layout:   Ordinary
    #           4,750
    m = re.search(r"(Ordinary|Preference|Treasury)\s*\n\s*([\d,]+(?:\.\d+)?)", region)
    if m:
        amt = m.group(2).replace(",", "")
        try:
            return {"share_class": m.group(1), "amount": float(amt), "currency": "SGD"}
        except ValueError:
            return {}
    return {}


def _split_address(text: str) -> str:
    idx = text.find("Registered office address")
    if idx < 0:
        return ""
    region = text[idx:idx + 600]
    # take 2 lines after the header that look like SG address
    lines = [ln.strip() for ln in region.split("\n") if ln.strip()]
    addr: List[str] = []
    for ln in lines[1:]:
        if "Office hours" in ln or "Authentication" in ln:
            break
        if re.search(r"SINGAPORE\s+\d{5,6}", ln, re.I):
            addr.append(ln)
            break
        if re.match(r"^[\d#A-Z]", ln):
            addr.append(ln)
    return ", ".join(addr).strip(", ")


# ---- Top-level extractors -----------------------------------------------------

def extract_bm42a(pdf_path: Path) -> CorporateProfile:
    """Extract from a BM42A annual-filing cover sheet."""
    pdf_path = Path(pdf_path)
    text = _read_pdf(pdf_path)
    prof = CorporateProfile(source_files=[str(pdf_path)])

    m = re.search(r"UEN\s*Entity name\s*\n([0-9A-Z]+)\s+(.+?)(?:\n|$)", text)
    if m:
        prof.uen = m.group(1).strip()
        prof.entity_name = m.group(2).strip()
    else:
        um = UEN_RE.search(pdf_path.name) or UEN_RE.search(text)
        if um:
            prof.uen = um.group(1)
        nm = re.search(r"([A-Z0-9][A-Z0-9 &'.,\-]{3,}PTE\.?\s*LTD\.?)", text)
        if nm:
            prof.entity_name = nm.group(1).strip()

    if v := _grab(text, "Entity type"):       prof.entity_type = v
    if v := _grab(text, "Entity status"):     prof.entity_status = v
    if v := _grab(text, "Date of incorporation"):
        prof.incorporation_date = v
    if v := _grab(text, "Financial year end date"):
        prof.fye = v
    elif v := _grab(text, "Financial year end (FYE)"):
        prof.fye = v
    if v := _grab(text, "Company type for the relevant financial period"):
        prof.company_type = v
    if v := _grab(text, "Company status"):    prof.company_status = v

    sm = SSIC_RE.search(text)
    if sm:
        prof.primary_ssic_code = sm.group(1)
        prof.primary_ssic_desc = sm.group(2).strip().rstrip(".")

    prof.registered_address = _split_address(text)

    if "small company exempt from audit" in text.lower():
        # explicit Yes/No follows the question
        m = re.search(r"small company exempt from audit\s*requirements\?\s*\n([A-Za-z]+)", text, re.I)
        if m:
            prof.small_company_exemption = m.group(1).strip().lower() == "yes"
    if m := re.search(r"financial statements been\s*audited\?\s*\n([A-Za-z]+)", text, re.I):
        prof.audited = m.group(1).strip().lower() == "yes"
    if m := re.search(r"Accounting standards used to prepare financial\s*statements\s*\n([^\n]+)", text, re.I):
        prof.accounting_standards = m.group(1).strip()
    if m := re.search(r"Consolidated Level\s*\n([^\n]+)", text, re.I):
        prof.consolidated_level = m.group(1).strip()
    if m := re.search(r"Did the company hold its AGM\?\s*\n([^\n]+)", text, re.I):
        prof.agm_required = "yes" in m.group(1).lower()
    if m := re.search(r"Date of AGM\s*\n([^\n]+)", text, re.I):
        prof.agm_date = m.group(1).strip()

    cap = _parse_paid_up_capital(text)
    if cap:
        prof.paid_up_capital_amount = cap.get("amount")
        prof.paid_up_capital_currency = cap.get("currency", "SGD")
        prof.paid_up_share_class = cap.get("share_class", "")

    holders = _parse_position_holders(text)
    prof.directors = holders["directors"]
    prof.shareholders = holders["shareholders"]
    prof.secretaries = holders["secretaries"]
    prof.auditors = holders["auditors"]

    prof.charges = _parse_charges(text)

    return prof


def extract_c223(pdf_path: Path) -> CorporateProfile:
    """Extract from a Form C223 Annual Return."""
    pdf_path = Path(pdf_path)
    text = _read_pdf(pdf_path)
    prof = CorporateProfile(source_files=[str(pdf_path)])

    um = UEN_RE.search(text) or UEN_RE.search(pdf_path.name)
    if um:
        prof.uen = um.group(1)

    # Primary activity in C223
    m = re.search(r"Primary Activity\s*\n([A-Z][^()\n]*?)\s*\((\d{5})\)", text)
    if m:
        prof.primary_ssic_desc = m.group(1).strip()
        prof.primary_ssic_code = m.group(2)

    m = re.search(r"Financial Year End for this Annual Return\s+Date of Annual Return\s*\n(\d{2}/\d{2}/\d{4})", text)
    if m:
        prof.fye = m.group(1)

    if "Active" in text:
        prof.company_status = "Active"

    # Registered address
    m = re.search(r"Registered Office Address\s*\n(.+?)(?=\n[A-Z][a-z])", text, re.S)
    if m:
        prof.registered_address = re.sub(r"\s+", " ", m.group(1)).strip()

    # Auditor / officer disclosure isn't typically in C223 in the same structure as BM42A
    return prof


def merge_profiles(profiles: List[CorporateProfile]) -> CorporateProfile:
    """Combine multiple year filings — BM42A wins on richer fields, C223 fills gaps."""
    if not profiles:
        return CorporateProfile()
    merged = CorporateProfile()
    # priority: BM42A entries (have most fields) then C223
    profiles_sorted = sorted(profiles, key=lambda p: -len(p.directors) - len(p.charges))
    for p in profiles_sorted:
        for field_name in (
            "uen", "entity_name", "entity_type", "entity_status", "incorporation_date",
            "fye", "primary_ssic_code", "primary_ssic_desc", "registered_address",
            "company_type", "company_status", "consolidated_level", "accounting_standards",
            "agm_date",
        ):
            if not getattr(merged, field_name) and getattr(p, field_name):
                setattr(merged, field_name, getattr(p, field_name))
        for bool_field in ("small_company_exemption", "audited", "agm_required"):
            if getattr(merged, bool_field) is None and getattr(p, bool_field) is not None:
                setattr(merged, bool_field, getattr(p, bool_field))
        if merged.paid_up_capital_amount is None and p.paid_up_capital_amount is not None:
            merged.paid_up_capital_amount = p.paid_up_capital_amount
            merged.paid_up_capital_currency = p.paid_up_capital_currency
            merged.paid_up_share_class = p.paid_up_share_class
        # unions
        for list_field in ("directors", "shareholders", "secretaries", "auditors", "charges"):
            existing = getattr(merged, list_field)
            existing_keys = {(r.get("name", ""), r.get("id", "")) for r in existing}
            for r in getattr(p, list_field):
                key = (r.get("name", ""), r.get("id", ""))
                if key not in existing_keys:
                    existing.append(r)
                    existing_keys.add(key)
        merged.source_files.extend(p.source_files)
    merged.source_files = list(dict.fromkeys(merged.source_files))
    return merged
