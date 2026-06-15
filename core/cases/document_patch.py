"""
Shared helpers for editing the per-source `document.json` and recording an
audit trail. Used by both `api/review.py` (the HTTP PATCH route the
extraction-review page calls) and `core/coworker/pending_actions.py` (the
co-worker's `override_extracted_value` executor).

Why a separate module:
  - Path walking + edit + audit append is the same regardless of who's
    triggering it, but the HTTP route used to bake `HTTPException`s into
    the helpers — which couples them to FastAPI and makes them unusable
    from the executor (which needs to raise `ValueError` so the
    pending-actions dispatcher can record a clean error row).
  - These helpers now raise `DocumentPatchError` (a plain `ValueError`
    subclass). Callers can translate to whatever exception is right for
    their layer.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


class DocumentPatchError(ValueError):
    """Raised for any caller-facing problem (bad path, missing key, etc.)."""


# ---- path resolution --------------------------------------------------------

def walk_path(obj: Any, path: List[Any]) -> Any:
    """Walk `obj` along `path` (list of dict keys + array indices)."""
    cur = obj
    for step in path:
        if isinstance(cur, dict):
            if step not in cur:
                raise DocumentPatchError(f"Path step {step!r} not in dict at {path}")
            cur = cur[step]
        elif isinstance(cur, list):
            try:
                idx = int(step)
            except (TypeError, ValueError):
                raise DocumentPatchError(f"Path step {step!r} is not a list index at {path}")
            if idx < 0 or idx >= len(cur):
                raise DocumentPatchError(f"Index {idx} out of range at {path}")
            cur = cur[idx]
        else:
            raise DocumentPatchError(f"Cannot descend into {type(cur).__name__} at {path}")
    return cur


def set_at_path(obj: Any, path: List[Any], value: Any) -> Any:
    """Mutate `obj` in place to set `path` to `value`; return the previous value."""
    if not path:
        raise DocumentPatchError("Empty path")
    parent = walk_path(obj, path[:-1])
    last = path[-1]
    if isinstance(parent, dict):
        old = parent.get(last)
        parent[last] = value
        return old
    if isinstance(parent, list):
        try:
            idx = int(last)
        except (TypeError, ValueError):
            raise DocumentPatchError(f"Path tail {last!r} is not a list index")
        if idx < 0 or idx >= len(parent):
            raise DocumentPatchError(f"Index {idx} out of range")
        old = parent[idx]
        parent[idx] = value
        return old
    raise DocumentPatchError(f"Cannot set inside {type(parent).__name__}")


# ---- audit trail (per-source) -----------------------------------------------

def _audit_path(source_dir: Path) -> Path:
    return source_dir / "document.audits.json"


def load_audits(source_dir: Path) -> List[Dict[str, Any]]:
    p = _audit_path(source_dir)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def append_audit(source_dir: Path, entry: Dict[str, Any]) -> None:
    audits = load_audits(source_dir)
    audits.append(entry)
    _audit_path(source_dir).write_text(
        json.dumps(audits, indent=2, ensure_ascii=False), encoding="utf-8",
    )


# ---- combined: apply an edit + record the audit row -------------------------

def patch_document_cell(
    source_dir: Path,
    path: List[Any],
    new_value: Any,
    *,
    user: str = "analyst",
    reason: str | None = None,
) -> Dict[str, Any]:
    """
    Load `<source_dir>/document.json`, set `path` to `new_value`, persist the
    file, append an audit row with the old → new diff, and return the row.

    Raises `DocumentPatchError` for any path / value problem.
    Raises `FileNotFoundError` if `document.json` is absent (caller has
    misidentified the source).
    """
    doc_path = source_dir / "document.json"
    if not doc_path.exists():
        raise FileNotFoundError(f"document.json missing under {source_dir}")
    doc = json.loads(doc_path.read_text(encoding="utf-8"))

    old = set_at_path(doc, list(path), new_value)
    doc_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

    entry: Dict[str, Any] = {
        "at": datetime.now().isoformat(),
        "user": user,
        "path": list(path),
        "old_value": old,
        "new_value": new_value,
        "reason": reason,
    }
    append_audit(source_dir, entry)
    return entry
