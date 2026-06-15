"""
Tests for Phase 4 — write-side tools, pending-action lifecycle, executors.

Covers:
  - Each write tool returns a preview payload and stages a pending action.
  - Pending actions persist to disk, load back round-trip, and expire.
  - Cancel removes the file and writes a cancelled audit row.
  - Execute runs the right executor, deletes the pending file, audits.
  - flag_for_committee appends to committee_notes.md.
  - annotate_finding writes to finding_annotations.json with expected shape.
  - regenerate_report_section refactor is honoured by the API path (smoke).
  - Tools that fail upstream don't leave stale pending files dangling.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _seed_case(tmp_path: Path, case_id: str = "case01") -> Path:
    case_root = tmp_path / case_id
    for sub in ("raw", "parsed", "features", "agents", "chat", "reports"):
        (case_root / sub).mkdir(parents=True, exist_ok=True)
    (case_root / "manifest.json").write_text(json.dumps({
        "case_id": case_id, "company_name": "Acme Pte Ltd",
        "industry_hint": "logistics", "status": "completed",
    }), encoding="utf-8")
    return case_root


def _store_at(tmp_path: Path):
    from core.cases.case_store import CaseStore
    return CaseStore(base_dir=str(tmp_path))


def _point_tools_at(tmp_path: Path):
    """Point the actions tool module's CaseStore singleton at the tmp dir."""
    from core.cases.case_store import CaseStore
    import core.coworker.tools.actions as actions_tool
    actions_tool._store = CaseStore(base_dir=str(tmp_path))


# ---- write tools return preview payloads --------------------------------------

def test_flag_for_committee_returns_preview_and_stages_action(tmp_path):
    _seed_case(tmp_path)
    _point_tools_at(tmp_path)
    from core.coworker.tools import dispatch

    out = dispatch("flag_for_committee", "case01",
                   {"message": "FY24 EBITDA decline is one-off restructuring."})
    assert not out.get("is_error"), out
    result = out["result"]
    assert result["preview"] is True
    assert result["kind"] == "flag_for_committee"
    assert result["payload"]["message"].startswith("FY24 EBITDA")
    token = result["token"]
    assert token and len(token) >= 8

    # Committee notes file should NOT exist yet — only confirm should create it.
    assert not (tmp_path / "case01" / "committee_notes.md").exists()
    pending = tmp_path / "case01" / "pending_actions" / f"{token}.json"
    assert pending.exists()


def test_flag_for_committee_empty_message_errors_without_staging(tmp_path):
    _seed_case(tmp_path)
    _point_tools_at(tmp_path)
    from core.coworker.tools import dispatch
    out = dispatch("flag_for_committee", "case01", {"message": "   "})
    assert out.get("is_error") is True
    pending_dir = tmp_path / "case01" / "pending_actions"
    assert not pending_dir.exists() or not any(pending_dir.iterdir())


def test_annotate_finding_preview(tmp_path):
    _seed_case(tmp_path)
    _point_tools_at(tmp_path)
    from core.coworker.tools import dispatch
    out = dispatch("annotate_finding", "case01", {
        "card_id": "FS", "risk_id": "r1",
        "comment": "Mgmt confirmed this is non-recurring.",
    })
    result = out["result"]
    assert result["preview"] is True
    assert result["payload"]["card_id"] == "FS"
    assert result["payload"]["risk_id"] == "r1"
    assert "non-recurring" in result["payload"]["comment"]


def test_annotate_finding_missing_card_errors(tmp_path):
    _seed_case(tmp_path)
    _point_tools_at(tmp_path)
    from core.coworker.tools import dispatch
    out = dispatch("annotate_finding", "case01", {"card_id": "", "comment": "hi"})
    assert out.get("is_error") is True


def test_regenerate_report_section_preview(tmp_path):
    _seed_case(tmp_path)
    _point_tools_at(tmp_path)
    from core.coworker.tools import dispatch
    out = dispatch("regenerate_report_section", "case01", {
        "section_code": "executive_credit_view", "instruction": "tighten",
    })
    result = out["result"]
    assert result["preview"] is True
    assert result["payload"]["section_code"] == "executive_credit_view"
    assert result["payload"]["instruction"] == "tighten"


# ---- pending-action lifecycle --------------------------------------------------

def test_pending_action_round_trip(tmp_path):
    _seed_case(tmp_path)
    store = _store_at(tmp_path)
    from core.coworker.pending_actions import (
        create_pending_action, load_pending_action, list_pending_actions,
    )

    action = create_pending_action("case01", "flag_for_committee",
                                   {"message": "x"}, "Flag: x", store=store)
    loaded = load_pending_action("case01", action.token, store=store)
    assert loaded is not None
    assert loaded.kind == "flag_for_committee"
    assert loaded.payload["message"] == "x"
    assert loaded.description == "Flag: x"

    listed = list_pending_actions("case01", store=store)
    assert len(listed) == 1 and listed[0].token == action.token


def test_pending_action_cancel(tmp_path):
    _seed_case(tmp_path)
    store = _store_at(tmp_path)
    from core.coworker.pending_actions import (
        create_pending_action, cancel_pending_action, load_pending_action,
        load_audit_tail,
    )
    action = create_pending_action("case01", "flag_for_committee",
                                   {"message": "x"}, "Flag: x", store=store)
    assert cancel_pending_action("case01", action.token, store=store) is True
    assert load_pending_action("case01", action.token, store=store) is None
    # Cancel writes one audit row marked cancelled.
    audit = load_audit_tail("case01", store=store)
    assert any(r.get("status") == "cancelled" and r.get("token") == action.token
               for r in audit)


def test_pending_action_execute_runs_flag_executor(tmp_path):
    _seed_case(tmp_path)
    store = _store_at(tmp_path)
    from core.coworker.pending_actions import (
        create_pending_action, execute_pending_action, load_pending_action,
        load_audit_tail,
    )
    action = create_pending_action("case01", "flag_for_committee",
                                   {"message": "Risk to monitor: leverage"},
                                   "desc", store=store)
    out = execute_pending_action("case01", action.token, store=store)
    assert out["ok"] is True
    assert out["kind"] == "flag_for_committee"
    # Pending file cleaned up after success.
    assert load_pending_action("case01", action.token, store=store) is None
    # Notes file written with the message.
    notes = (tmp_path / "case01" / "committee_notes.md").read_text(encoding="utf-8")
    assert "Risk to monitor: leverage" in notes
    # Audit row.
    audit = load_audit_tail("case01", store=store)
    assert any(r.get("status") == "executed" and r.get("kind") == "flag_for_committee"
               for r in audit)


def test_pending_action_execute_unknown_token(tmp_path):
    _seed_case(tmp_path)
    store = _store_at(tmp_path)
    from core.coworker.pending_actions import execute_pending_action
    out = execute_pending_action("case01", "deadbeef" * 2, store=store)
    assert out["ok"] is False
    assert "not found" in out["error"].lower() or "consumed" in out["error"].lower()


def test_pending_action_execute_expired(tmp_path, monkeypatch):
    _seed_case(tmp_path)
    store = _store_at(tmp_path)
    from core.coworker.pending_actions import (
        create_pending_action, execute_pending_action, load_pending_action,
    )
    action = create_pending_action("case01", "flag_for_committee",
                                   {"message": "x"}, "desc", store=store,
                                   expiry_seconds=-1)  # already expired
    out = execute_pending_action("case01", action.token, store=store)
    assert out["ok"] is False
    assert "expired" in out["error"].lower()
    # Expired actions are cleaned up.
    assert load_pending_action("case01", action.token, store=store) is None


def test_annotate_finding_executor_writes_json(tmp_path):
    _seed_case(tmp_path)
    store = _store_at(tmp_path)
    from core.coworker.pending_actions import (
        create_pending_action, execute_pending_action,
    )
    action = create_pending_action("case01", "annotate_finding", {
        "card_id": "FS", "risk_id": "r1",
        "comment": "Confirmed non-recurring per mgmt.",
    }, "desc", store=store)
    out = execute_pending_action("case01", action.token, store=store)
    assert out["ok"] is True
    data = json.loads((tmp_path / "case01" / "finding_annotations.json").read_text(encoding="utf-8"))
    assert len(data) == 1
    row = data[0]
    assert row["card_id"] == "FS"
    assert row["risk_id"] == "r1"
    assert "non-recurring" in row["comment"]
    assert "created_at" in row


def test_annotate_finding_executor_appends_to_existing(tmp_path):
    _seed_case(tmp_path)
    store = _store_at(tmp_path)
    (tmp_path / "case01" / "finding_annotations.json").write_text(
        json.dumps([{"card_id": "OLD", "comment": "old", "created_at": "x"}]),
        encoding="utf-8",
    )
    from core.coworker.pending_actions import (
        create_pending_action, execute_pending_action,
    )
    action = create_pending_action("case01", "annotate_finding",
                                   {"card_id": "FS", "comment": "new"},
                                   "desc", store=store)
    out = execute_pending_action("case01", action.token, store=store)
    assert out["ok"] is True
    data = json.loads((tmp_path / "case01" / "finding_annotations.json").read_text(encoding="utf-8"))
    assert len(data) == 2
    assert data[0]["card_id"] == "OLD"
    assert data[1]["card_id"] == "FS"


def test_committee_notes_appends_idempotently_distinct_lines(tmp_path):
    _seed_case(tmp_path)
    store = _store_at(tmp_path)
    from core.coworker.pending_actions import (
        create_pending_action, execute_pending_action,
    )
    for msg in ("Line one", "Line two"):
        a = create_pending_action("case01", "flag_for_committee",
                                  {"message": msg}, "d", store=store)
        execute_pending_action("case01", a.token, store=store)
    notes = (tmp_path / "case01" / "committee_notes.md").read_text(encoding="utf-8")
    assert "Line one" in notes
    assert "Line two" in notes
    assert notes.startswith("# Committee notes")


def test_create_pending_action_for_unknown_case_raises(tmp_path):
    store = _store_at(tmp_path)
    from core.coworker.pending_actions import create_pending_action
    with pytest.raises(FileNotFoundError):
        create_pending_action("ghost", "flag_for_committee",
                              {"message": "x"}, "d", store=store)


def test_registry_includes_new_write_tools():
    from core.coworker.tools import tool_names
    names = tool_names()
    for expected in ("flag_for_committee", "annotate_finding",
                     "regenerate_report_section"):
        assert expected in names, f"missing tool: {expected}"
