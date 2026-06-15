"""
Co-worker agent loop — Anthropic tool-use over case data.

`run_agent_turn(case_id, message, history)` is a generator that yields
streaming events for one analyst turn:

    {"type": "delta",       "text": str}
    {"type": "tool_use",    "id": str, "name": str, "input": dict}
    {"type": "tool_result", "id": str, "name": str,
                            "output": dict, "is_error": bool}
    {"type": "done",        "text": str, "tool_calls": list,
                            "citations": list, "usage": dict}
    {"type": "error",       "message": str}

It handles the multi-step loop: model → tool_use → dispatch → tool_result →
model, until the model emits a final `end_turn`. The loop is bounded by
`MAX_TOOL_ROUNDS` to prevent runaway tool calling.

When no Anthropic API key is configured, falls back to a deterministic mock
response so the chat UI keeps working without an LLM. The mock does not
attempt tool reasoning — it just acknowledges the message and lists what the
agent could do if an API key were set.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterator, List, Optional

from config.config import get_config
from core.cases.case_store import CaseStore
from prompts.coworker_system_prompt import (
    build_case_header,
    get_coworker_system_prompt,
)
from .tools import TOOL_SPECS, dispatch, tool_names


MAX_TOOL_ROUNDS = 6
MAX_HISTORY_TURNS = 10  # last N user/assistant pairs replayed into the model


_store = CaseStore()
_config = get_config()


# ---- public entry -------------------------------------------------------------

def run_agent_turn(
    case_id: str,
    message: str,
    history: Optional[List[Dict[str, str]]] = None,
) -> Iterator[Dict[str, Any]]:
    """
    Stream a single co-worker turn. Yields the event dicts described above.
    Caller is responsible for persisting the final text via CaseStore.
    """
    history = history or []
    try:
        manifest = _store.get_manifest(case_id)
    except FileNotFoundError:
        yield {"type": "error", "message": f"Case not found: {case_id}"}
        return

    fs_analytics = _store.load_features(case_id, "fs_analytics") or {}
    assessment = _store.load_assessment_summary(case_id) or {}
    analyst_notes = _store.load_analyst_notes(case_id)

    system_prompt = (
        get_coworker_system_prompt()
        + build_case_header(manifest, fs_analytics, assessment, analyst_notes)
    )

    if _no_api_key():
        yield from _mock_turn(message, manifest)
        return

    # Lazy import so a missing anthropic package doesn't break api startup
    try:
        from core.client.claude_client import ClaudeClient
        client = ClaudeClient()
    except Exception as e:  # noqa: BLE001
        yield {"type": "error", "message": f"Failed to init Claude client: {e}"}
        return

    messages = _build_initial_messages(history, message)
    collected_text: List[str] = []
    tool_call_trace: List[Dict[str, Any]] = []
    citations: List[Dict[str, Any]] = []
    usage_total = {"input_tokens": 0, "output_tokens": 0}

    for _round in range(MAX_TOOL_ROUNDS):
        round_text = ""
        round_tool_uses: List[Dict[str, Any]] = []
        stop_reason = None
        assistant_content: List[Dict[str, Any]] = []

        for event in client.stream_message(
            system=system_prompt,
            messages=messages,
            tools=TOOL_SPECS,
        ):
            etype = event.get("type")
            if etype == "text_delta":
                text = event.get("text", "")
                round_text += text
                yield {"type": "delta", "text": text}
            elif etype == "message_complete":
                stop_reason = event.get("stop_reason")
                round_tool_uses = event.get("tool_uses", []) or []
                assistant_content = event.get("content", []) or []
                u = event.get("usage", {}) or {}
                usage_total["input_tokens"] += int(u.get("input_tokens", 0) or 0)
                usage_total["output_tokens"] += int(u.get("output_tokens", 0) or 0)
            elif etype == "error":
                yield {"type": "error", "message": event.get("error", "Unknown LLM error")}
                return

        # Record the assistant's turn in the message list for the next round.
        if assistant_content:
            messages.append({"role": "assistant", "content": assistant_content})

        if stop_reason != "tool_use" or not round_tool_uses:
            # Final round — this round's text IS the answer the analyst sees.
            # Intermediate-round text (the "Let me look up..." narration the
            # model emits before calling tools) is discarded, so chat history
            # and the `done` event carry only the final answer.
            if round_text:
                collected_text.append(round_text)
            break

        # Dispatch each tool the model requested, in order, and append the
        # tool_result blocks as a single user message.
        tool_result_blocks: List[Dict[str, Any]] = []
        for tu in round_tool_uses:
            yield {
                "type": "tool_use",
                "id": tu["id"],
                "name": tu["name"],
                "input": tu["input"],
            }
            out = dispatch(tu["name"], case_id, tu["input"])
            is_error = bool(out.get("is_error"))
            tool_call_trace.append({
                "id": tu["id"],
                "name": tu["name"],
                "input": tu["input"],
                "output": out,
                "is_error": is_error,
            })
            if not is_error:
                for c in (out.get("citations") or []):
                    citations.append({"tool_id": tu["id"], "tool": tu["name"], **c})

            yield {
                "type": "tool_result",
                "id": tu["id"],
                "name": tu["name"],
                "output": out,
                "is_error": is_error,
            }
            tool_result_blocks.append({
                "type": "tool_result",
                "tool_use_id": tu["id"],
                "content": json.dumps(out, default=str)[:60_000],
                "is_error": is_error,
            })

        messages.append({"role": "user", "content": tool_result_blocks})
    else:
        # Loop exhausted MAX_TOOL_ROUNDS without the model finishing.
        yield {
            "type": "delta",
            "text": (
                f"\n\n_(Stopped after {MAX_TOOL_ROUNDS} tool rounds — "
                "ask a more specific follow-up if needed.)_"
            ),
        }

    final_text = "".join(collected_text).strip() or _fallback_final_text(tool_call_trace)
    yield {
        "type": "done",
        "text": final_text,
        "tool_calls": tool_call_trace,
        "citations": citations,
        "usage": usage_total,
    }


# ---- helpers ------------------------------------------------------------------

def _no_api_key() -> bool:
    anthropic_key = (
        _config.get("anthropic.api_key")
        or os.getenv("ANTHROPIC_API_KEY", "")
    )
    return not anthropic_key


def _build_initial_messages(
    history: List[Dict[str, str]],
    new_user_message: str,
) -> List[Dict[str, Any]]:
    """
    Replay the last MAX_HISTORY_TURNS of {role,content} as plain text
    messages, then append the new user turn. Tool use from past turns is not
    replayed — the model re-derives any data it needs.
    """
    recent = [
        m for m in history
        if m.get("role") in ("user", "assistant") and m.get("content")
    ][-(MAX_HISTORY_TURNS * 2):]
    msgs: List[Dict[str, Any]] = [
        {"role": m["role"], "content": m["content"]} for m in recent
    ]
    msgs.append({"role": "user", "content": new_user_message})
    return msgs


def _fallback_final_text(trace: List[Dict[str, Any]]) -> str:
    if not trace:
        return "(no response generated)"
    last = trace[-1]
    if last.get("is_error"):
        return f"Tool `{last['name']}` failed: {last['output'].get('error', '')}"
    return f"(Used {len(trace)} tool call(s) — see trace for details.)"


def _mock_turn(message: str, manifest: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    """
    Deterministic fallback when no API key is configured. Streams a short
    explanatory message so the rail still feels alive.
    """
    company = manifest.get("company_name") or "this case"
    text = (
        f"I'm running in mock mode (no Anthropic API key configured). "
        f"For case **{company}** I would normally call tools like "
        + ", ".join(f"`{n}`" for n in tool_names())
        + " to answer:\n\n"
        f"> {message.strip() or '(empty question)'}\n\n"
        "Set `ANTHROPIC_API_KEY` and reload to enable the live agent."
    )
    for chunk in _chunk_text(text, size=40):
        yield {"type": "delta", "text": chunk}
    yield {
        "type": "done",
        "text": text,
        "tool_calls": [],
        "citations": [],
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }


def _chunk_text(text: str, size: int = 40) -> Iterator[str]:
    for i in range(0, len(text), size):
        yield text[i:i + size]
