"""
Per-source review + approval endpoints — Phase 1 S4 / S5.

Drives the analyst review dashboard:

    PATCH  /cases/{id}/sources/{sid}/document   — apply a cell-level edit and
                                                  append an audit entry
    POST   /cases/{id}/sources/{sid}/approve    — mark source approved
    POST   /cases/{id}/sources/{sid}/reject     — mark source rejected
    POST   /cases/{id}/sources/{sid}/reset-status — clear approval back to pending
    GET    /cases/{id}/sources/{sid}/audits     — audit trail for this source

All edits land in `<source_id>/document.json`; the diff appears in
`<source_id>/document.audits.json`. Approval state lives in
`<source_id>/manifest.json` under `review` so the rollup index picks it up
without an extra read.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.cases.case_store import CaseStore


router = APIRouter(tags=["review"])
_store = CaseStore()


# ---- Path resolution / safety -------------------------------------------------

def _source_dir(case_id: str, source_id: str) -> Path:
    try:
        _store.get_manifest(case_id)
    except FileNotFoundError:
        raise HTTPException(404, "Case not found")
    src = _store._case_path(case_id) / "parsed" / "financials" / source_id
    if not src.exists() or not src.is_dir():
        raise HTTPException(404, "Source not found for this case")
    return src


def _walk_path(obj: Any, path: List[Any]) -> Any:
    """Walk `obj` along `path` (list of dict keys + array indices)."""
    cur = obj
    for step in path:
        if isinstance(cur, dict):
            if step not in cur:
                raise HTTPException(400, f"Path step {step!r} not in dict at {path}")
            cur = cur[step]
        elif isinstance(cur, list):
            try:
                idx = int(step)
            except (TypeError, ValueError):
                raise HTTPException(400, f"Path step {step!r} is not a list index at {path}")
            if idx < 0 or idx >= len(cur):
                raise HTTPException(400, f"Index {idx} out of range at {path}")
            cur = cur[idx]
        else:
            raise HTTPException(400, f"Cannot descend into {type(cur).__name__} at {path}")
    return cur


def _set_at_path(obj: Any, path: List[Any], value: Any) -> Any:
    """Return the old value, mutating `obj` in place to set `path` to `value`."""
    if not path:
        raise HTTPException(400, "Empty path")
    parent = _walk_path(obj, path[:-1])
    last = path[-1]
    if isinstance(parent, dict):
        old = parent.get(last)
        parent[last] = value
        return old
    if isinstance(parent, list):
        try:
            idx = int(last)
        except (TypeError, ValueError):
            raise HTTPException(400, f"Path tail {last!r} is not a list index")
        if idx < 0 or idx >= len(parent):
            raise HTTPException(400, f"Index {idx} out of range")
        old = parent[idx]
        parent[idx] = value
        return old
    raise HTTPException(400, f"Cannot set inside {type(parent).__name__}")


# ---- Request models -----------------------------------------------------------

class DocumentEditRequest(BaseModel):
    path: List[Union[str, int]]
    value: Any = None
    reason: Optional[str] = None
    user: Optional[str] = None


class ApprovalRequest(BaseModel):
    notes: Optional[str] = None
    user: Optional[str] = None


# ---- Audit log helpers --------------------------------------------------------

def _audit_path(src: Path) -> Path:
    return src / "document.audits.json"


def _load_audits(src: Path) -> List[Dict[str, Any]]:
    p = _audit_path(src)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _append_audit(src: Path, entry: Dict[str, Any]) -> None:
    audits = _load_audits(src)
    audits.append(entry)
    _audit_path(src).write_text(json.dumps(audits, indent=2, ensure_ascii=False), encoding="utf-8")


# ---- Approval state helpers ---------------------------------------------------

def _load_manifest(src: Path) -> Dict[str, Any]:
    mp = src / "manifest.json"
    if not mp.exists():
        raise HTTPException(404, "Source manifest missing")
    return json.loads(mp.read_text(encoding="utf-8"))


def _save_manifest(src: Path, manifest: Dict[str, Any]) -> None:
    (src / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _update_rollup_index(case_id: str, source_id: str, review: Dict[str, Any]) -> None:
    """Sync the per-source review state into parsed/financials/index.json."""
    case_root = _store._case_path(case_id)
    idx_path = case_root / "parsed" / "financials" / "index.json"
    if not idx_path.exists():
        return
    try:
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    for s in idx.get("sources", []) or []:
        if s.get("source_id") == source_id:
            s["review"] = review
            break
    idx_path.write_text(json.dumps(idx, indent=2, ensure_ascii=False), encoding="utf-8")


# ---- Endpoints ----------------------------------------------------------------

@router.patch("/cases/{case_id}/sources/{source_id}/document")
def edit_document(case_id: str, source_id: str, req: DocumentEditRequest):
    """Apply a targeted edit to document.json + append an audit entry."""
    src = _source_dir(case_id, source_id)
    doc_path = src / "document.json"
    if not doc_path.exists():
        raise HTTPException(404, "document.json missing for this source")
    doc = json.loads(doc_path.read_text(encoding="utf-8"))

    if not req.path:
        raise HTTPException(400, "path is required")

    old = _set_at_path(doc, list(req.path), req.value)
    doc_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

    entry = {
        "at":       datetime.now().isoformat(),
        "user":     req.user or "analyst",
        "path":     list(req.path),
        "old_value": old,
        "new_value": req.value,
        "reason":   req.reason,
    }
    _append_audit(src, entry)

    return JSONResponse({"ok": True, "audit": entry, "document": doc})


@router.get("/cases/{case_id}/sources/{source_id}/audits")
def get_audits(case_id: str, source_id: str):
    src = _source_dir(case_id, source_id)
    return JSONResponse({
        "case_id":   case_id,
        "source_id": source_id,
        "count":     len(_load_audits(src)),
        "audits":    _load_audits(src),
    })


@router.post("/cases/{case_id}/sources/{source_id}/approve")
def approve_source(case_id: str, source_id: str, req: ApprovalRequest):
    src = _source_dir(case_id, source_id)
    manifest = _load_manifest(src)
    review = {
        "status":      "approved",
        "approved_at": datetime.now().isoformat(),
        "approved_by": req.user or "analyst",
        "notes":       req.notes,
    }
    manifest["review"] = review
    _save_manifest(src, manifest)
    _update_rollup_index(case_id, source_id, review)
    return JSONResponse({"ok": True, "review": review})


@router.post("/cases/{case_id}/sources/{source_id}/reject")
def reject_source(case_id: str, source_id: str, req: ApprovalRequest):
    src = _source_dir(case_id, source_id)
    manifest = _load_manifest(src)
    review = {
        "status":      "rejected",
        "rejected_at": datetime.now().isoformat(),
        "rejected_by": req.user or "analyst",
        "notes":       req.notes,
    }
    manifest["review"] = review
    _save_manifest(src, manifest)
    _update_rollup_index(case_id, source_id, review)
    return JSONResponse({"ok": True, "review": review})


@router.post("/cases/{case_id}/sources/{source_id}/reset-status")
def reset_status(case_id: str, source_id: str):
    src = _source_dir(case_id, source_id)
    manifest = _load_manifest(src)
    manifest.pop("review", None)
    _save_manifest(src, manifest)
    _update_rollup_index(case_id, source_id, {"status": "pending"})
    return JSONResponse({"ok": True, "review": {"status": "pending"}})


# ---- Approval-gate summary (drives the Run-Analysis button) -------------------

def _load_review_summary(case_id: str) -> Dict[str, Any]:
    """
    Read parsed/financials/index.json and return per-source approval state +
    aggregate counts. Used by both `GET /review-status` and the `/analyze`
    approval gate.
    """
    try:
        _store.get_manifest(case_id)
    except FileNotFoundError:
        raise HTTPException(404, "Case not found")

    idx_path = _store._case_path(case_id) / "parsed" / "financials" / "index.json"
    if not idx_path.exists():
        return {
            "total": 0, "approved": 0, "pending": 0, "rejected": 0,
            "ready_to_analyze": False, "sources": [],
            "blocked_reason": "No extracted sources yet. Run extraction first.",
        }

    try:
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(500, "Rollup index is corrupt")

    sources = []
    counts = {"approved": 0, "pending": 0, "rejected": 0}
    for s in idx.get("sources", []) or []:
        review = s.get("review") or {}
        status = review.get("status", "pending")
        if status not in counts:
            status = "pending"
        counts[status] += 1
        sources.append({
            "source_id":      s.get("source_id"),
            "original_filename": s.get("original_filename"),
            "entity":         s.get("entity"),
            "fys":            s.get("fys", []),
            "status":         status,
            "notes":          review.get("notes"),
            "decided_at":     review.get("approved_at") or review.get("rejected_at"),
        })

    total = len(sources)
    ready = total > 0 and counts["approved"] == total

    blocked_reason: Optional[str] = None
    if total == 0:
        blocked_reason = "No extracted sources to analyse."
    elif not ready:
        not_approved = [s["original_filename"] for s in sources if s["status"] != "approved"]
        blocked_reason = (
            f"{len(not_approved)} of {total} source(s) still need approval: "
            + ", ".join(not_approved)
        )

    return {
        "total":            total,
        "approved":         counts["approved"],
        "pending":          counts["pending"],
        "rejected":         counts["rejected"],
        "ready_to_analyze": ready,
        "blocked_reason":   blocked_reason,
        "sources":          sources,
    }


@router.get("/cases/{case_id}/review-status")
def review_status(case_id: str):
    """Per-case approval summary; drives the Run-Analysis gate on the frontend."""
    return JSONResponse(_load_review_summary(case_id))
