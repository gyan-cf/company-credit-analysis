"""Lightweight case-wiki search.

This is intentionally simple for Phase 1: lexical scoring over `chunks.jsonl`.
It is deterministic, fast, and good enough for analyst drill-down while the
case-wiki contract settles. A vector backend can consume the same chunks later.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List


_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./%-]*")


def _tokens(text: str) -> List[str]:
    stop = {
        "the", "and", "for", "with", "from", "that", "this", "what", "which",
        "show", "tell", "about", "does", "have", "case", "source",
    }
    return [
        t.lower()
        for t in _TOKEN_RE.findall(text or "")
        if len(t) >= 2 and t.lower() not in stop
    ]


def _load_chunks(wiki_root: Path) -> List[Dict[str, Any]]:
    chunks_path = wiki_root / "chunks.jsonl"
    if not chunks_path.exists():
        return []
    chunks = []
    with chunks_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                chunks.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return chunks


def search_case_wiki(case_root: Path, query: str, limit: int = 8) -> Dict[str, Any]:
    """Search `cases/<id>/wiki/chunks.jsonl` and return ranked chunks."""
    wiki_root = Path(case_root) / "wiki"
    chunks = _load_chunks(wiki_root)
    q_tokens = _tokens(query)
    if not q_tokens:
        return {"query": query, "count": 0, "results": []}

    doc_freq = Counter()
    chunk_tokens: List[List[str]] = []
    for chunk in chunks:
        toks = _tokens(" ".join([
            chunk.get("title") or "",
            chunk.get("doc_type") or "",
            " ".join(chunk.get("topics") or []),
            chunk.get("text") or "",
        ]))
        chunk_tokens.append(toks)
        doc_freq.update(set(toks))

    n_docs = max(len(chunks), 1)
    scored = []
    for chunk, toks in zip(chunks, chunk_tokens):
        if not toks:
            continue
        tf = Counter(toks)
        score = 0.0
        for token in q_tokens:
            if token not in tf:
                continue
            idf = math.log((n_docs + 1) / (doc_freq[token] + 0.5)) + 1
            score += (1 + math.log(tf[token])) * idf
        title = (chunk.get("title") or "").lower()
        if any(token in title for token in q_tokens):
            score *= 1.25
        if score <= 0:
            continue
        text = chunk.get("text") or ""
        scored.append({
            "score": round(score, 4),
            "chunk_id": chunk.get("chunk_id"),
            "title": chunk.get("title"),
            "doc_type": chunk.get("doc_type"),
            "path": chunk.get("path"),
            "source_id": chunk.get("source_id"),
            "source_file": chunk.get("source_file"),
            "page_range": chunk.get("page_range"),
            "topics": chunk.get("topics") or [],
            "evidence_ids": chunk.get("evidence_ids") or [],
            "snippet": _snippet(text, q_tokens),
        })

    scored.sort(key=lambda r: r["score"], reverse=True)
    return {"query": query, "count": len(scored[:limit]), "results": scored[:limit]}


def _snippet(text: str, q_tokens: List[str], size: int = 420) -> str:
    haystack = text or ""
    lower = haystack.lower()
    first = min((lower.find(t) for t in q_tokens if lower.find(t) >= 0), default=0)
    start = max(0, first - size // 3)
    end = min(len(haystack), start + size)
    snippet = haystack[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(haystack):
        snippet += "..."
    return snippet
