"""
AI Co-worker — case-scoped chat orchestrated through the tool-using agent loop.

This module is intentionally thin: all reasoning lives in
`core/coworker/agent_loop.run_agent_turn`. `CoworkerService` is the HTTP
adapter that:

  - drives the loop (synchronous aggregate or SSE stream),
  - persists the analyst-visible user/assistant text to the case chat history,
  - shapes the final response payload for the API layer.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterator, List, Optional

from core.cases.case_store import CaseStore
from core.coworker import run_agent_turn


class CoworkerService:
    """Drives the agent loop against a single case + analyst message."""

    def __init__(self, store: Optional[CaseStore] = None):
        self.store = store or CaseStore()

    # ---- non-streaming -------------------------------------------------------

    def chat(
        self,
        case_id: str,
        message: str,
        skill: Optional[str] = None,  # accepted for backwards compatibility; ignored
    ) -> Dict[str, Any]:
        """
        Synchronous wrapper that runs the agent loop to completion and returns
        one aggregated response. Used by the existing POST /chat endpoint.
        """
        history = self.store.load_chat_history(case_id)
        final_text = ""
        tool_calls: List[Dict[str, Any]] = []
        citations: List[Dict[str, Any]] = []
        error_message: Optional[str] = None

        for event in run_agent_turn(case_id, message, history):
            etype = event.get("type")
            if etype == "done":
                final_text = event.get("text", "")
                tool_calls = event.get("tool_calls", [])
                citations = event.get("citations", [])
            elif etype == "error":
                error_message = event.get("message", "unknown error")

        if error_message and not final_text:
            final_text = f"Error: {error_message}"

        self.store.save_chat_message(case_id, "user", message)
        self.store.save_chat_message(case_id, "assistant", final_text)

        return {
            "reply": final_text,
            "citations": citations,
            "tool_trace": tool_calls,
            "skill_used": "agent",  # back-compat: the model now picks tools itself
        }

    # ---- streaming -----------------------------------------------------------

    def chat_stream(self, case_id: str, message: str) -> Iterator[str]:
        """
        SSE generator yielding `data: <json>\\n\\n` frames. Frame payloads
        mirror the agent_loop event dicts.

        Persists the user message immediately, and the assistant text once the
        `done` event arrives. If the stream errors out, the partial text seen
        so far is still saved so the rail history stays consistent.
        """
        history = self.store.load_chat_history(case_id)
        self.store.save_chat_message(case_id, "user", message)

        assistant_text_parts: List[str] = []
        final_saved = False

        try:
            for event in run_agent_turn(case_id, message, history):
                etype = event.get("type")
                if etype == "delta":
                    assistant_text_parts.append(event.get("text", ""))
                elif etype == "done":
                    text = event.get("text", "") or "".join(assistant_text_parts)
                    self.store.save_chat_message(case_id, "assistant", text)
                    final_saved = True
                yield _sse_frame(event)
        except Exception as e:  # noqa: BLE001 — must not leak as a 500 mid-stream
            yield _sse_frame({"type": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            if not final_saved:
                partial = "".join(assistant_text_parts).strip()
                if partial:
                    self.store.save_chat_message(case_id, "assistant", partial)


def _sse_frame(event: Dict[str, Any]) -> str:
    return "data: " + json.dumps(event, default=str, ensure_ascii=False) + "\n\n"
