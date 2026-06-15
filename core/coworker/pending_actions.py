"""
Preview-then-confirm pipeline for analyst-mutating co-worker tools.

Every write tool produces a `PendingAction` rather than executing inline.
The token is surfaced in the tool result, the frontend renders a confirm
card, and only when the analyst explicitly approves does
`execute_pending_action(case_id, token)` run the underlying mutation and
record an audit entry in `cases/<id>/coworker_audit.jsonl`.

Action lifecycle:

    1. Tool calls `create_pending_action(case_id, kind, payload, description)`
       and surfaces the returned token to the model + UI.
    2. `cases/<id>/pending_actions/<token>.json` lives on disk until
       (a) confirmed via `execute_pending_action`, (b) cancelled via
       `cancel_pending_action`, or (c) it expires (1 hour by default).
    3. On execute, the kind is dispatched to a `_execute_<kind>` function
       in this module. The pending file is deleted on success and one row
       is appended to `coworker_audit.jsonl`.

Why a separate file per pending action instead of a single queue:
file-per-token gives us natural atomicity (no concurrent-write races on
the queue), trivial expiry (filesystem mtime), and a stable URL per
action without needing a DB.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.cases.case_store import CaseStore


_TOKEN_LENGTH = 16
_DEFAULT_EXPIRY_SECONDS = 60 * 60  # 1 hour
_AUDIT_TAIL = 500  # max audit rows to keep in tail readers


# ---- model -----------------------------------------------------------------

@dataclass
class PendingAction:
    token: str
    case_id: str
    kind: str
    payload: Dict[str, Any]
    description: str
    created_at: str
    expires_at: str
    audit: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now()
        return datetime.fromisoformat(self.expires_at) < now


# ---- paths -----------------------------------------------------------------

def _pending_dir(store: CaseStore, case_id: str) -> Path:
    return store._case_path(case_id) / "pending_actions"


def _pending_path(store: CaseStore, case_id: str, token: str) -> Path:
    return _pending_dir(store, case_id) / f"{token}.json"


def _audit_path(store: CaseStore, case_id: str) -> Path:
    return store._case_path(case_id) / "coworker_audit.jsonl"


# ---- crud ------------------------------------------------------------------

def create_pending_action(
    case_id: str,
    kind: str,
    payload: Dict[str, Any],
    description: str,
    store: Optional[CaseStore] = None,
    expiry_seconds: int = _DEFAULT_EXPIRY_SECONDS,
) -> PendingAction:
    """Persist a pending action and return it (with token)."""
    store = store or CaseStore()
    # Guard against unknown cases — raises FileNotFoundError if case absent.
    store.get_manifest(case_id)

    now = datetime.now()
    token = _make_token(case_id, kind, payload, now)
    action = PendingAction(
        token=token,
        case_id=case_id,
        kind=kind,
        payload=payload,
        description=description,
        created_at=now.isoformat(),
        expires_at=(now + timedelta(seconds=expiry_seconds)).isoformat(),
    )

    dest = _pending_path(store, case_id, token)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(action.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return action


def load_pending_action(case_id: str, token: str, store: Optional[CaseStore] = None) -> Optional[PendingAction]:
    store = store or CaseStore()
    path = _pending_path(store, case_id, token)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return PendingAction(
        token=data.get("token", token),
        case_id=data.get("case_id", case_id),
        kind=data.get("kind", ""),
        payload=data.get("payload") or {},
        description=data.get("description", ""),
        created_at=data.get("created_at", ""),
        expires_at=data.get("expires_at", ""),
        audit=data.get("audit") or [],
    )


def list_pending_actions(case_id: str, store: Optional[CaseStore] = None) -> List[PendingAction]:
    store = store or CaseStore()
    pdir = _pending_dir(store, case_id)
    if not pdir.exists():
        return []
    out: List[PendingAction] = []
    for path in sorted(pdir.iterdir()):
        if not path.is_file() or path.suffix != ".json":
            continue
        loaded = load_pending_action(case_id, path.stem, store=store)
        if loaded:
            out.append(loaded)
    return out


def cancel_pending_action(case_id: str, token: str, store: Optional[CaseStore] = None) -> bool:
    """Returns True if the file existed and was removed."""
    store = store or CaseStore()
    path = _pending_path(store, case_id, token)
    if not path.exists():
        return False
    path.unlink()
    _record_audit(store, case_id, {
        "at": datetime.now().isoformat(),
        "token": token,
        "kind": "cancelled",
        "status": "cancelled",
    })
    return True


def execute_pending_action(
    case_id: str,
    token: str,
    store: Optional[CaseStore] = None,
) -> Dict[str, Any]:
    """
    Run the executor matching the pending action's kind. Returns
    {ok: bool, kind, result?, error?}. On success the pending file is
    removed and an audit row is appended.
    """
    store = store or CaseStore()
    action = load_pending_action(case_id, token, store=store)
    if action is None:
        return {"ok": False, "error": f"Pending action {token} not found or already consumed."}
    if action.is_expired():
        cancel_pending_action(case_id, token, store=store)
        return {"ok": False, "error": "Pending action expired. Re-issue the request."}

    executor = _EXECUTORS.get(action.kind)
    if executor is None:
        return {"ok": False, "error": f"No executor registered for kind '{action.kind}'."}

    try:
        result = executor(case_id, action.payload, store)
    except Exception as e:  # noqa: BLE001
        _record_audit(store, case_id, {
            "at": datetime.now().isoformat(),
            "token": token,
            "kind": action.kind,
            "status": "failed",
            "error": f"{type(e).__name__}: {e}",
        })
        return {"ok": False, "kind": action.kind, "error": f"{type(e).__name__}: {e}"}

    # Remove the pending file and log a success audit row.
    _pending_path(store, case_id, token).unlink(missing_ok=True)
    _record_audit(store, case_id, {
        "at": datetime.now().isoformat(),
        "token": token,
        "kind": action.kind,
        "status": "executed",
        "result_summary": result.get("summary") if isinstance(result, dict) else None,
    })
    return {"ok": True, "kind": action.kind, "result": result}


def load_audit_tail(case_id: str, limit: int = 50, store: Optional[CaseStore] = None) -> List[Dict[str, Any]]:
    """Return the last N audit rows (newest last). Best-effort — corrupt lines skipped."""
    store = store or CaseStore()
    path = _audit_path(store, case_id)
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-max(1, min(int(limit), _AUDIT_TAIL)):]


# ---- helpers ---------------------------------------------------------------

def _make_token(case_id: str, kind: str, payload: Dict[str, Any], now: datetime) -> str:
    seed = f"{case_id}|{kind}|{json.dumps(payload, sort_keys=True, default=str)}|{now.isoformat()}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:_TOKEN_LENGTH]


def _record_audit(store: CaseStore, case_id: str, row: Dict[str, Any]) -> None:
    path = _audit_path(store, case_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


# ---- executors (one per action kind) ---------------------------------------

def _execute_flag_for_committee(case_id: str, payload: Dict[str, Any], store: CaseStore) -> Dict[str, Any]:
    message = (payload.get("message") or "").strip()
    if not message:
        raise ValueError("flag_for_committee requires a non-empty 'message'.")
    path = store._case_path(case_id) / "committee_notes.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"- **{stamp}** — {message}\n"
    if not path.exists():
        path.write_text("# Committee notes\n\n" + entry, encoding="utf-8")
    else:
        # Append, ensuring a newline separator.
        existing = path.read_text(encoding="utf-8")
        sep = "" if existing.endswith("\n") else "\n"
        path.write_text(existing + sep + entry, encoding="utf-8")
    return {
        "summary": f"Flagged for committee: {message[:80]}",
        "file": "committee_notes.md",
        "appended_line": entry.strip(),
    }


def _execute_annotate_finding(case_id: str, payload: Dict[str, Any], store: CaseStore) -> Dict[str, Any]:
    card_id = (payload.get("card_id") or "").strip()
    risk_id = (payload.get("risk_id") or "").strip()
    comment = (payload.get("comment") or "").strip()
    if not card_id:
        raise ValueError("annotate_finding requires 'card_id'.")
    if not comment:
        raise ValueError("annotate_finding requires a non-empty 'comment'.")

    path = store._case_path(case_id) / "finding_annotations.json"
    annotations: List[Dict[str, Any]] = []
    if path.exists():
        try:
            annotations = json.loads(path.read_text(encoding="utf-8")) or []
        except json.JSONDecodeError:
            annotations = []

    annotations.append({
        "card_id": card_id,
        "risk_id": risk_id or None,
        "comment": comment,
        "created_at": datetime.now().isoformat(),
    })
    path.write_text(json.dumps(annotations, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "summary": f"Annotated {card_id}" + (f" / {risk_id}" if risk_id else "") + " — " + comment[:60],
        "file": "finding_annotations.json",
        "total_annotations": len(annotations),
    }


def _execute_regenerate_report_section(case_id: str, payload: Dict[str, Any], store: CaseStore) -> Dict[str, Any]:
    """
    Re-runs one section of the credit report via the existing pipeline.
    Imported lazily so cyclic imports stay clean during agent_loop init.
    """
    from core.report.generator import regenerate_one_section

    section_code = (payload.get("section_code") or "").strip()
    if not section_code:
        raise ValueError("regenerate_report_section requires 'section_code'.")
    instruction = (payload.get("instruction") or "").strip() or None

    case_root = store._case_path(case_id)
    section = regenerate_one_section(case_root, section_code, instruction)
    return {
        "summary": f"Regenerated section: {section.get('title') or section_code}",
        "section_code": section_code,
        "section_title": section.get("title"),
        "instruction": instruction,
    }


def _execute_override_extracted_value(
    case_id: str, payload: Dict[str, Any], store: CaseStore,
) -> Dict[str, Any]:
    """
    Resolve (source_id, statement, canonical_code, perimeter, fy) to a
    JSON-Pointer-ish path inside document.json and patch the cell in place,
    appending the audit row that the review dashboard already consumes.
    """
    import json as _json
    from core.cases.document_patch import patch_document_cell

    source_id = (payload.get("source_id") or "").strip()
    statement = (payload.get("statement") or "").strip().lower()
    canonical_code = (payload.get("canonical_code") or "").strip()
    perimeter = (payload.get("perimeter") or "company").strip().lower()
    fy = (payload.get("fy") or "").strip()
    new_value = payload.get("value")
    reason = (payload.get("reason") or "Overridden via co-worker").strip()

    if not source_id:
        raise ValueError("override_extracted_value requires 'source_id'.")
    if statement not in ("sofp", "soci", "socf"):
        raise ValueError("override_extracted_value 'statement' must be sofp / soci / socf.")
    if not canonical_code:
        raise ValueError("override_extracted_value requires 'canonical_code'.")
    if not fy:
        raise ValueError("override_extracted_value requires 'fy' (e.g. 'FY2024').")
    if perimeter not in ("company", "group"):
        raise ValueError("'perimeter' must be 'company' or 'group'.")
    if new_value is not None and not isinstance(new_value, (int, float)):
        raise ValueError("'value' must be numeric or null.")

    src_dir = store._case_path(case_id) / "parsed" / "financials" / source_id
    if not src_dir.exists():
        raise ValueError(f"Source '{source_id}' not found for case {case_id}.")
    doc_path = src_dir / "document.json"
    if not doc_path.exists():
        raise ValueError(f"document.json missing under {src_dir}.")
    doc = _json.loads(doc_path.read_text(encoding="utf-8"))

    blocks = doc.get("blocks") or []
    block_idx = next(
        (i for i, b in enumerate(blocks)
         if b.get("kind") == "statement" and (b.get("type") or "").lower() == statement),
        None,
    )
    if block_idx is None:
        raise ValueError(f"No {statement} statement block in source {source_id}.")
    block = blocks[block_idx]
    column_id = next(
        (c.get("id") for c in (block.get("columns") or [])
         if (c.get("perimeter") or "").lower() == perimeter and (c.get("fy") or "") == fy),
        None,
    )
    if column_id is None:
        available = [
            f"{(c.get('perimeter') or '')}/{c.get('fy') or ''}"
            for c in (block.get("columns") or [])
        ]
        raise ValueError(
            f"No column for perimeter={perimeter} fy={fy} in {statement}. "
            f"Available: {available}"
        )
    row_idx = next(
        (i for i, r in enumerate(block.get("rows") or [])
         if (r.get("canonical_code") or "") == canonical_code),
        None,
    )
    if row_idx is None:
        labels = [
            (r.get("canonical_code"), r.get("label"))
            for r in (block.get("rows") or [])
            if r.get("canonical_code")
        ][:20]
        raise ValueError(
            f"No row with canonical_code={canonical_code!r} in {statement} for source {source_id}. "
            f"Sample available: {labels}"
        )

    row = block["rows"][row_idx]
    row_label = row.get("label") or canonical_code
    path = ["blocks", block_idx, "rows", row_idx, "values", column_id]

    entry = patch_document_cell(
        src_dir, path, new_value, user="coworker", reason=reason,
    )

    return {
        "summary": (
            f"Overrode {row_label} ({canonical_code}) "
            f"[{perimeter}/{fy}] in {source_id}: "
            f"{entry['old_value']} → {entry['new_value']}"
        ),
        "source_id": source_id,
        "statement": statement,
        "canonical_code": canonical_code,
        "row_label": row_label,
        "perimeter": perimeter,
        "fy": fy,
        "column_id": column_id,
        "old_value": entry["old_value"],
        "new_value": entry["new_value"],
        "path": entry["path"],
    }


def _execute_rerun_analysis(
    case_id: str, payload: Dict[str, Any], store: CaseStore,
) -> Dict[str, Any]:
    """
    Re-run the full analysis pipeline for this case. Spawns the run in the
    shared ThreadPoolExecutor from api/main so the HTTP request returns
    quickly; the existing case-status indicator surfaces progress.

    Audit row records that the re-run was queued, not that it finished —
    completion happens off-thread and the analyst tracks it via the case
    status banner / poll loop.
    """
    reason = (payload.get("reason") or "").strip()

    # Lazy import to avoid pulling the FastAPI app into this module's import
    # graph (would cause a circular dependency at startup).
    from api.main import executor, pipeline

    store.update_status(case_id, "queued", 5)

    def _run() -> None:
        try:
            pipeline.run(case_id)
        except Exception as e:  # noqa: BLE001
            store.update_status(case_id, "failed", 0, error=str(e))

    executor.submit(_run)
    return {
        "summary": (
            "Re-analysis queued"
            + (f" — reason: {reason}" if reason else "")
            + ". Watch the case-status indicator for progress."
        ),
        "queued": True,
        "reason": reason or None,
    }


_EXECUTORS: Dict[str, Callable[[str, Dict[str, Any], CaseStore], Dict[str, Any]]] = {
    "flag_for_committee": _execute_flag_for_committee,
    "annotate_finding": _execute_annotate_finding,
    "regenerate_report_section": _execute_regenerate_report_section,
    "override_extracted_value": _execute_override_extracted_value,
    "rerun_analysis": _execute_rerun_analysis,
}


def supported_action_kinds() -> List[str]:
    return list(_EXECUTORS.keys())
