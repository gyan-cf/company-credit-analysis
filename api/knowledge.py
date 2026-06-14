"""Case knowledge-base endpoints."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from core.cases.case_store import CaseStore
from core.knowledge import build_case_wiki, search_case_wiki


router = APIRouter(tags=["knowledge"])
_store = CaseStore()


def _case_root(case_id: str) -> Path:
    try:
        _store.get_manifest(case_id)
    except FileNotFoundError:
        raise HTTPException(404, "Case not found")
    return _store._case_path(case_id)


def _wiki_root(case_id: str) -> Path:
    root = _case_root(case_id) / "wiki"
    if not root.exists():
        raise HTTPException(404, "Case wiki not generated yet")
    return root


def _resolve_wiki_path(root: Path, rel_path: str) -> Path:
    target = (root / rel_path).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        raise HTTPException(400, "Invalid wiki path")
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "Wiki page not found")
    return target


@router.post("/cases/{case_id}/knowledge/rebuild")
def rebuild_case_knowledge(case_id: str):
    summary = build_case_wiki(_case_root(case_id))
    return JSONResponse(summary)


@router.get("/cases/{case_id}/knowledge")
def get_case_knowledge_manifest(case_id: str):
    root = _wiki_root(case_id)
    manifest = root / "manifest.json"
    if not manifest.exists():
        raise HTTPException(404, "Case wiki manifest missing")
    return JSONResponse(json.loads(manifest.read_text(encoding="utf-8")))


@router.get("/cases/{case_id}/knowledge/search")
def search_knowledge(case_id: str, q: str = Query(..., min_length=1), limit: int = Query(8, ge=1, le=25)):
    return JSONResponse(search_case_wiki(_case_root(case_id), q, limit=limit))


@router.get("/cases/{case_id}/knowledge/note-links")
def get_note_links(case_id: str):
    root = _wiki_root(case_id)
    links_path = root / "note_links.json"
    if not links_path.exists():
        return JSONResponse({"links": []})
    return JSONResponse(json.loads(links_path.read_text(encoding="utf-8")))


@router.get("/cases/{case_id}/knowledge/notes/{source_id}/{note_ref:path}")
def get_linked_note(case_id: str, source_id: str, note_ref: str):
    root = _wiki_root(case_id)
    links_path = root / "note_links.json"
    links = []
    if links_path.exists():
        links = (json.loads(links_path.read_text(encoding="utf-8")).get("links") or [])
    key = _note_key(note_ref)
    matches = [
        link for link in links
        if link.get("source_id") == source_id and _note_key(link.get("note_ref") or link.get("note_no")) == key
    ]
    if not matches:
        raise HTTPException(404, "Linked note not found")
    note_path = matches[0].get("note_wiki_path")
    markdown = ""
    if note_path:
        path = _resolve_wiki_path(root, note_path)
        markdown = path.read_text(encoding="utf-8")
    return JSONResponse({
        "source_id": source_id,
        "note_ref": note_ref,
        "note_key": key,
        "note": {
            "note_no": matches[0].get("note_no"),
            "title": matches[0].get("note_title"),
            "page_range": matches[0].get("note_page_range"),
            "wiki_path": note_path,
            "markdown": markdown,
        },
        "related_rows": matches,
    })


@router.get("/cases/{case_id}/knowledge/page/{path:path}")
def get_knowledge_page(case_id: str, path: str):
    page = _resolve_wiki_path(_wiki_root(case_id), path)
    return FileResponse(path=str(page), media_type="text/markdown; charset=utf-8")


def _note_key(value: object) -> str:
    import re
    raw = str(value or "").strip().lower().replace("note", "").strip()
    raw = re.sub(r"[^a-z0-9.]+", "", raw)
    return raw[:-2] if raw.endswith(".0") else raw
