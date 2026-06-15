"""
Case storage and lifecycle management.
"""

import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.config import get_config


class CaseStore:
    """File-based case store for credit analysis workflows."""

    def __init__(self, base_dir: Optional[str] = None):
        config = get_config()
        self.base_dir = Path(base_dir or config.get("cases.base_dir", "cases"))
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _case_path(self, case_id: str) -> Path:
        return self.base_dir / case_id

    def create_case(
        self,
        company_name: str,
        industry_code: str = "generic",
        industry_hint: str = "generic",
        country: str = "Singapore",
        jurisdiction: str = "Singapore",
        uen: str = "",
        entity_type: str = "",
        company_status: str = "",
        incorporation_date: str = "",
        fiscal_year_end: str = "",
        primary_ssic_code: str = "",
        primary_ssic_desc: str = "",
        registered_address: str = "",
        currency: str = "SGD",
        facility_type: str = "",
        requested_limit: str = "",
        relationship_manager: str = "",
        priority: str = "normal",
        onboarding_stage: str = "entity_profile",
        cin: str = "",
        pan: str = "",
        fy_range: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        case_id = str(uuid.uuid4())[:8]
        case_path = self._case_path(case_id)
        for sub in ("raw", "parsed", "features", "agents", "chat"):
            (case_path / sub).mkdir(parents=True, exist_ok=True)

        manifest = {
            "case_id": case_id,
            "company_name": company_name,
            "country": country,
            "jurisdiction": jurisdiction,
            "uen": uen,
            "entity_type": entity_type,
            "company_status": company_status,
            "incorporation_date": incorporation_date,
            "fiscal_year_end": fiscal_year_end,
            "primary_ssic_code": primary_ssic_code,
            "primary_ssic_desc": primary_ssic_desc,
            "registered_address": registered_address,
            "currency": currency,
            "facility_type": facility_type,
            "requested_limit": requested_limit,
            "relationship_manager": relationship_manager,
            "priority": priority,
            "onboarding_stage": onboarding_stage,
            "cin": cin or uen,
            "pan": pan,
            "industry_code": industry_code,
            "industry_hint": industry_hint,
            "fy_range": fy_range or [],
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "status": "created",
            "progress": 0,
            "uploads": {},
            "errors": [],
        }
        self._save_manifest(case_id, manifest)
        return manifest

    def _save_manifest(self, case_id: str, manifest: Dict[str, Any]) -> None:
        manifest["updated_at"] = datetime.now().isoformat()
        path = self._case_path(case_id) / "manifest.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

    def get_manifest(self, case_id: str) -> Dict[str, Any]:
        path = self._case_path(case_id) / "manifest.json"
        if not path.exists():
            raise FileNotFoundError(f"Case not found: {case_id}")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def list_cases(self) -> List[Dict[str, Any]]:
        cases = []
        for p in self.base_dir.iterdir():
            if p.is_dir() and (p / "manifest.json").exists():
                try:
                    cases.append(self.get_manifest(p.name))
                except Exception:
                    pass
        return sorted(cases, key=lambda x: x.get("created_at", ""), reverse=True)

    def update_status(
        self,
        case_id: str,
        status: str,
        progress: int = 0,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        manifest = self.get_manifest(case_id)
        manifest["status"] = status
        manifest["progress"] = progress
        if error:
            manifest.setdefault("errors", []).append(
                {"at": datetime.now().isoformat(), "message": error}
            )
        self._save_manifest(case_id, manifest)
        return manifest

    def save_upload(
        self,
        case_id: str,
        source_type: str,
        filename: str,
        content: bytes,
    ) -> Path:
        manifest = self.get_manifest(case_id)
        dest_dir = self._case_path(case_id) / "raw" / source_type
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / filename
        dest_path.write_bytes(content)

        manifest.setdefault("uploads", {}).setdefault(source_type, []).append(
            {"filename": filename, "path": str(dest_path), "uploaded_at": datetime.now().isoformat()}
        )
        self._save_manifest(case_id, manifest)
        return dest_path

    def save_parsed(self, case_id: str, name: str, data: Dict[str, Any]) -> Path:
        path = self._case_path(case_id) / "parsed" / f"{name}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return path

    def load_parsed(self, case_id: str, name: str) -> Dict[str, Any]:
        path = self._case_path(case_id) / "parsed" / f"{name}.json"
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_features(self, case_id: str, name: str, data: Dict[str, Any]) -> Path:
        path = self._case_path(case_id) / "features" / f"{name}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return path

    def load_features(self, case_id: str, name: str) -> Dict[str, Any]:
        path = self._case_path(case_id) / "features" / f"{name}.json"
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_agent_result(self, case_id: str, agent_name: str, result: Dict[str, Any]) -> Path:
        path = self._case_path(case_id) / "agents" / f"{agent_name}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        return path

    def load_agent_result(self, case_id: str, agent_name: str) -> Dict[str, Any]:
        path = self._case_path(case_id) / "agents" / f"{agent_name}.json"
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_assessment_summary(self, case_id: str, summary: Dict[str, Any]) -> Path:
        path = self._case_path(case_id) / "assessment_summary.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        return path

    def load_assessment_summary(self, case_id: str) -> Dict[str, Any]:
        path = self._case_path(case_id) / "assessment_summary.json"
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_credit_memo(self, case_id: str, content: str) -> Path:
        path = self._case_path(case_id) / "credit_memo.md"
        path.write_text(content, encoding="utf-8")
        return path

    def load_credit_memo(self, case_id: str) -> str:
        path = self._case_path(case_id) / "credit_memo.md"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    # ---- Analyst notes (per-case persistent memory) ----

    def load_analyst_notes(self, case_id: str) -> str:
        """
        Return the markdown body of cases/<id>/analyst_notes.md, or '' if the
        analyst hasn't written any notes for this case yet.
        """
        path = self._case_path(case_id) / "analyst_notes.md"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def save_analyst_notes(self, case_id: str, content: str) -> Dict[str, Any]:
        """
        Replace the analyst-notes file. Returns metadata the API surfaces back
        to the editor (size, last_updated). Empty content is allowed and
        clears the file rather than deleting it, so the path stays stable.
        """
        # Ensure the case directory exists; raises if the case is unknown.
        self.get_manifest(case_id)
        path = self._case_path(case_id) / "analyst_notes.md"
        path.write_text(content or "", encoding="utf-8")
        return {
            "case_id": case_id,
            "length": len(content or ""),
            "last_updated": datetime.now().isoformat(),
        }

    def save_chat_message(self, case_id: str, role: str, content: str) -> None:
        history_path = self._case_path(case_id) / "chat" / "history.json"
        history = []
        if history_path.exists():
            with open(history_path, "r", encoding="utf-8") as f:
                history = json.load(f)
        history.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        })
        with open(history_path, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)

    def load_chat_history(self, case_id: str) -> List[Dict[str, str]]:
        history_path = self._case_path(case_id) / "chat" / "history.json"
        if not history_path.exists():
            return []
        with open(history_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_raw_files(self, case_id: str, source_type: str) -> List[Path]:
        raw_dir = self._case_path(case_id) / "raw" / source_type
        if not raw_dir.exists():
            return []
        return list(raw_dir.iterdir())
