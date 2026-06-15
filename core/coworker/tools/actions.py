"""
Write-side tools for the co-worker agent.

Every tool here is preview-only: it creates a `PendingAction` on disk and
returns the token. The mutation does not happen until the analyst clicks
Approve in the rail, which fires
`POST /cases/{id}/pending-actions/{token}/confirm`. This keeps the agent
loop from inadvertently mutating case state.

Tools registered:
    - flag_for_committee(message)         → appends to committee_notes.md
    - annotate_finding(card_id, ...)      → writes to finding_annotations.json
    - regenerate_report_section(code, …)  → re-runs one report section

The agent should describe what it's about to do (referencing the
description in the tool result) so the analyst has context before
clicking Approve. The frontend renders the pending action as a
confirmation card with both buttons.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.cases.case_store import CaseStore
from core.coworker.pending_actions import create_pending_action


_store = CaseStore()


def _preview(case_id: str, kind: str, payload: Dict[str, Any], description: str) -> Dict[str, Any]:
    """Helper — turns a pending action into a tool-result payload."""
    action = create_pending_action(case_id, kind, payload, description, store=_store)
    return {
        "result": {
            "preview": True,
            "token": action.token,
            "kind": kind,
            "description": description,
            "expires_at": action.expires_at,
            "payload": payload,
            "next_step": (
                "The analyst must click Approve in the rail to apply this change. "
                "Tell them what you've proposed and wait."
            ),
        },
        "citations": [{
            "kind": "pending_action",
            "token": action.token,
            "action_kind": kind,
        }],
    }


# ---- flag_for_committee ---------------------------------------------------

FLAG_FOR_COMMITTEE_SPEC: Dict[str, Any] = {
    "name": "flag_for_committee",
    "description": (
        "Stage a one-line note for the committee-notes file. Call this — do "
        "NOT just describe the action in text — when the analyst says 'flag "
        "X for committee', 'add to the committee pack', or 'we should call "
        "this out at credit committee'. The tool stages a pending action; "
        "the analyst then clicks Approve in the rail to apply it. The "
        "underlying file (cases/<id>/committee_notes.md) is mutated only "
        "after approval."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": (
                    "The exact line to add. Keep it concise and concrete — "
                    "this goes verbatim into the markdown bullet."
                ),
            },
        },
        "required": ["message"],
    },
}


def flag_for_committee(case_id: str, message: str) -> Dict[str, Any]:
    message = (message or "").strip()
    if not message:
        return {
            "is_error": True,
            "error": "flag_for_committee requires a non-empty 'message'.",
        }
    description = f"Flag for committee: \"{message}\""
    return _preview(case_id, "flag_for_committee", {"message": message}, description)


# ---- annotate_finding ------------------------------------------------------

ANNOTATE_FINDING_SPEC: Dict[str, Any] = {
    "name": "annotate_finding",
    "description": (
        "Stage an analyst comment on a specific assessment card (or a "
        "specific risk within it). Call this — do NOT just describe it in "
        "text — when the analyst says 'add a note on the FS card', "
        "'annotate the EBITDA risk', or 'note for the record that …'. "
        "Stages a pending action; the analyst clicks Approve in the rail "
        "to write it to cases/<id>/finding_annotations.json."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "card_id": {
                "type": "string",
                "description": (
                    "The card_type (e.g. 'FS', 'INDUSTRY', 'QUALITATIVE') "
                    "the comment attaches to. Use list_red_flags to look "
                    "this up if you don't know it."
                ),
            },
            "risk_id": {
                "type": "string",
                "description": (
                    "Optional risk id within the card (e.g. 'r1'). Omit "
                    "when the comment is about the card as a whole."
                ),
            },
            "comment": {
                "type": "string",
                "description": "The annotation body. Plain text or markdown.",
            },
        },
        "required": ["card_id", "comment"],
    },
}


def annotate_finding(
    case_id: str,
    card_id: str,
    comment: str,
    risk_id: Optional[str] = None,
) -> Dict[str, Any]:
    card_id = (card_id or "").strip()
    comment = (comment or "").strip()
    if not card_id:
        return {"is_error": True, "error": "annotate_finding requires 'card_id'."}
    if not comment:
        return {"is_error": True, "error": "annotate_finding requires a non-empty 'comment'."}

    description_parts = [f"Annotate {card_id}"]
    if risk_id:
        description_parts.append(f"/ {risk_id}")
    description_parts.append(f": \"{comment[:80]}{'…' if len(comment) > 80 else ''}\"")
    description = " ".join(description_parts)
    payload = {"card_id": card_id, "risk_id": (risk_id or "").strip(), "comment": comment}
    return _preview(case_id, "annotate_finding", payload, description)


# ---- regenerate_report_section --------------------------------------------

REGENERATE_REPORT_SECTION_SPEC: Dict[str, Any] = {
    "name": "regenerate_report_section",
    "description": (
        "Stage a re-run of one section of the credit report. Call this — do "
        "NOT just describe the regeneration in text — when the analyst says "
        "'redo Section 4 with focus on FX', 'tighten the executive view', "
        "'expand on liquidity', or similar. Stages a pending action that, "
        "when the analyst clicks Approve, calls the section regeneration "
        "pipeline and updates both latest.json and latest.docx. Look up "
        "the section code via list_report_sections first if you don't know "
        "it. The 'instruction' is free-form prose or one of the shortcuts "
        "'tighten' / 'expand'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "section_code": {
                "type": "string",
                "description": (
                    "Section code from list_report_sections, e.g. "
                    "'executive_credit_view', 'liquidity_assessment'."
                ),
            },
            "instruction": {
                "type": "string",
                "description": (
                    "Optional. 'tighten' or 'expand' for shortcut behaviour, "
                    "or free-form prose like 'focus on FX exposure'. Omit "
                    "for a fresh regenerate."
                ),
            },
        },
        "required": ["section_code"],
    },
}


def regenerate_report_section(
    case_id: str,
    section_code: str,
    instruction: Optional[str] = None,
) -> Dict[str, Any]:
    section_code = (section_code or "").strip()
    if not section_code:
        return {"is_error": True, "error": "regenerate_report_section requires 'section_code'."}
    instruction = (instruction or "").strip() or None

    description = f"Regenerate report section '{section_code}'"
    if instruction:
        description += f" — instruction: \"{instruction[:80]}{'…' if len(instruction) > 80 else ''}\""
    payload = {"section_code": section_code, "instruction": instruction}
    return _preview(case_id, "regenerate_report_section", payload, description)


# ---- override_extracted_value ----------------------------------------------

OVERRIDE_EXTRACTED_VALUE_SPEC: Dict[str, Any] = {
    "name": "override_extracted_value",
    "description": (
        "Stage a correction to a single extracted financial-statement cell "
        "(e.g. 'fix FY22 Cash and Cash Equivalents to 1,566,827 in source "
        "<id>'). Call this — do NOT just describe the override in text — "
        "when the analyst spots a misread value during review and wants "
        "the corrected number written back. Stages a pending action; on "
        "Approve, the cell is patched in the per-source document.json and "
        "an audit row is appended to document.audits.json (same audit "
        "trail the review dashboard uses). The analyst's downstream "
        "analytics will not auto-refresh — use rerun_analysis afterwards "
        "if the override is material."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "source_id": {
                "type": "string",
                "description": (
                    "Source PDF id — first 12 hex chars of the SHA-256. "
                    "Look it up via search_knowledge citations or "
                    "list_red_flags if you don't know it."
                ),
            },
            "statement": {
                "type": "string",
                "enum": ["sofp", "soci", "socf"],
                "description": (
                    "sofp = balance sheet, soci = P&L / OCI, socf = cash flow."
                ),
            },
            "canonical_code": {
                "type": "string",
                "description": (
                    "Canonical line-item code, e.g. bs_cash, "
                    "bs_trade_other_recv, pl_revenue. Use get_statement to "
                    "look up the code when the analyst names the line in "
                    "human terms."
                ),
            },
            "fy": {
                "type": "string",
                "description": "Financial year tag, e.g. 'FY2024'.",
            },
            "value": {
                "type": ["number", "null"],
                "description": (
                    "The corrected numeric value. Pass null to clear the "
                    "cell (rare — usually the analyst wants a new number)."
                ),
            },
            "perimeter": {
                "type": "string",
                "enum": ["company", "group"],
                "description": "Reporting perimeter. Defaults to company.",
            },
            "reason": {
                "type": "string",
                "description": (
                    "Short reason for the override (goes into the audit "
                    "row). E.g. 'misread — confirmed with sponsor', "
                    "'OCR misalignment'."
                ),
            },
        },
        "required": ["source_id", "statement", "canonical_code", "fy", "value"],
    },
}


def override_extracted_value(
    case_id: str,
    source_id: str,
    statement: str,
    canonical_code: str,
    fy: str,
    value: Optional[float],
    perimeter: str = "company",
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    source_id = (source_id or "").strip()
    statement = (statement or "").strip().lower()
    canonical_code = (canonical_code or "").strip()
    fy = (fy or "").strip()
    perimeter = (perimeter or "company").strip().lower()
    reason = (reason or "").strip() or None

    if not source_id:
        return {"is_error": True, "error": "override_extracted_value requires 'source_id'."}
    if statement not in ("sofp", "soci", "socf"):
        return {"is_error": True, "error": "'statement' must be sofp / soci / socf."}
    if not canonical_code:
        return {"is_error": True, "error": "override_extracted_value requires 'canonical_code'."}
    if not fy:
        return {"is_error": True, "error": "override_extracted_value requires 'fy'."}
    if perimeter not in ("company", "group"):
        return {"is_error": True, "error": "'perimeter' must be 'company' or 'group'."}

    description = (
        f"Override {canonical_code} [{perimeter}/{fy}] in source {source_id} → "
        f"{value if value is not None else 'null'}"
    )
    payload = {
        "source_id": source_id,
        "statement": statement,
        "canonical_code": canonical_code,
        "perimeter": perimeter,
        "fy": fy,
        "value": value,
        "reason": reason,
    }
    return _preview(case_id, "override_extracted_value", payload, description)


# ---- rerun_analysis -------------------------------------------------------

RERUN_ANALYSIS_SPEC: Dict[str, Any] = {
    "name": "rerun_analysis",
    "description": (
        "Stage a re-run of the full analysis pipeline for this case (ingest "
        "→ analytics → FS / Industry / Qualitative agents → assessment + "
        "memo). Call this — do NOT just describe the rerun in text — when "
        "the analyst has materially changed input data (e.g. after an "
        "override_extracted_value) and wants the downstream cards / memo / "
        "report to reflect it. Takes ~30-60 seconds; the executor returns "
        "immediately and the analyst tracks progress via the case-status "
        "indicator. Use sparingly — only when something material has "
        "changed since the last run."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": (
                    "Short reason for the rerun (recorded in the audit "
                    "row). E.g. 'after FY22 receivables override', 'sponsor "
                    "supplied revised FY24 SoFP'."
                ),
            },
        },
    },
}


def rerun_analysis(case_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
    reason = (reason or "").strip() or None
    description = "Re-run the full analysis pipeline"
    if reason:
        description += f" — reason: \"{reason[:80]}{'…' if len(reason) > 80 else ''}\""
    description += " (~30-60s, runs in the background)"
    return _preview(case_id, "rerun_analysis", {"reason": reason}, description)
