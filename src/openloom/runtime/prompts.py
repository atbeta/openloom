"""
Runtime prompts / progress helpers — the YAGNI surface used by
``HarnessRunner`` and the OpenCode client.

The original 900-line module carried a YAML / markdown parser, a
step / acceptance protocol, a nudge lifecycle, and a stale-busy
detector. All of that lived behind ``openloom watch`` and the
file-inbox dispatch path, which 0.12 removes. This file now
exposes only the four primitives the harness + client still need:

  * ``TaskSpec`` — the minimum payload a webhook handler has to
    produce. Three fields (name, workspace, goal). Session binding
    is a parameter of ``HarnessRunner.add_task``, not a spec field.
  * ``detect_progress`` — does the latest assistant text contain
    a "task complete" marker? Webhook handlers can call this
    client-side if they want to short-circuit.
  * ``recent_assistant_activity`` / ``assistant_transcript`` —
    payload enrichment for the ``data.recent_activity`` block on
    ``TASK_UPDATED`` events. Trims and summarises the last N
    assistant turns so a webhook can fit on Slack/Discord.
  * ``messages_indicate_busy`` / ``permission_waiting_summary`` —
    used by the session monitor + OpenCode client to tell
    "the agent is in the middle of work" apart from "the agent
    is genuinely idle".
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Task-completion markers the agent is allowed to emit. The system
# prompt tells it to say "TASK COMPLETE" but LLMs are not strict
# about exact phrasing — they say "TASK DONE", "task is complete",
# "all steps done", "all checks pass", etc. The detector has to
# catch the common variants or webhook handlers short-circuit
# on a stale state. Each pattern is case-insensitive (the caller
# uppercases the text first) and bounded by non-letter on the
# left so we don't false-positive on negative phrases ("not yet
# done", "not complete").
_TASK_COMPLETE_RE = re.compile(
    r"(?:^|[\s\W])"
    r"(?:"
    r"task\s+(?:complete|done|finished|is\s+(?:complete|done|finished))"
    r"|all\s+(?:steps?|checks?)\s+(?:complete|done|pass)"
    r"|all\s+(?:steps?|checks?)\s+pass(?:ed)?"
    r"|(?:task|the\s+task)\s+(?:has\s+been|is\s+now)\s+(?:complete|done|finished)"
    r")"
    r"(?:[.!]|$)",
    re.I,
)


@dataclass
class TaskSpec:
    """The minimum task payload a webhook handler has to produce.

    ``name`` and ``workspace`` are display metadata; ``goal`` is the
    only field the agent sees — it is sent verbatim to OpenCode as
    the first user turn. Session binding lives on
    ``HarnessRunner.add_task``, not on the spec, so a TaskSpec is a
    plain data container safe to construct in any layer.

    Backward-compat note: ``from_dict`` accepts a dict and pulls
    out only the three known fields, ignoring everything else.
    This means old task records (with ``steps``, ``acceptance``,
    ``abort_session`` etc.) deserialise to a spec with empty
    extras — the harness will still run them, just without the
    protocol the old field names implied.
    """

    name: str = "Untitled task"
    workspace: str = ""
    goal: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "workspace": self.workspace,
            "goal": self.goal,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskSpec:
        name = str(data.get("name") or "Untitled task").strip() or "Untitled task"
        workspace = str(data.get("workspace") or "").strip()
        goal = str(data.get("goal") or "").strip()
        return cls(name=name, workspace=workspace, goal=goal)


def detect_progress(text: str, spec: TaskSpec | None = None) -> dict[str, Any]:
    """Backward-compat helper: returns whether the latest assistant
    text in ``text`` contains a task-completion marker. ``spec`` is
    ignored (kept for callers that have not been updated yet)."""
    upper = (text or "").upper()
    task_complete = _TASK_COMPLETE_RE.search(upper) is not None
    return {
        "task_complete": task_complete,
        "step_done": 0,
        "acceptance_checked": 0,
        "acceptance_total": 0,
        "acceptance_progress": 1.0 if task_complete else 0.0,
    }


def detect_progress_from_messages(
    messages: list[dict[str, Any]], spec: TaskSpec | None = None,
) -> dict[str, Any]:
    return detect_progress(assistant_transcript(messages), spec)


# Notification payload tuning knobs. Kept as module constants so
# tests can reference them by name and the env var
# OPENLOOM_NOTIFY_RECENT_MESSAGES can override the per-event N.
RECENT_ACTIVITY_DEFAULT_N = 3
ACTIVITY_TEXT_MAX_CHARS = 1_000
TOOL_INPUT_EXCERPT_CHARS = 80


def message_role(message: dict[str, Any]) -> str:
    info = message.get("info") if isinstance(message.get("info"), dict) else message
    if isinstance(info, dict) and info.get("role"):
        return str(info["role"]).lower()
    return str(message.get("role") or message.get("type") or "message").lower()


def _message_text(message: dict[str, Any]) -> str:
    if isinstance(message.get("text"), str):
        return message["text"]
    parts = message.get("parts") or message.get("content") or []
    if not isinstance(parts, list):
        return ""
    out: list[str] = []
    for part in parts:
        if isinstance(part, str):
            out.append(part)
        elif isinstance(part, dict):
            out.append(str(part.get("text") or part.get("content") or ""))
    return "".join(out)


# Public alias so the harness can read the latest assistant
# message without reaching into a private name. ``_message_text``
# stays so the existing tests can keep importing the underscore
# form if any do.
extract_assistant_text = _message_text


def assistant_transcript(
    messages: list[dict[str, Any]], limit: int | None = None,
) -> str:
    assistants = [m for m in messages if message_role(m) == "assistant"]
    if limit is None:
        return "\n\n".join(_message_text(m) for m in assistants if _message_text(m))
    return "\n\n".join(_message_text(m) for m in assistants[-limit:] if _message_text(m))


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _summarise_tools(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Render the tool-call summary for one assistant message:
    one entry per tool part, with the tool name, status, and the
    first 80 chars of the input. Output is intentionally omitted
    because tool output is unbounded and rarely the question a
    remote operator is asking ("what is the agent up to?" vs.
    "what did the tool return?")."""
    out: list[dict[str, Any]] = []
    for part in (message.get("parts") or []):
        if not isinstance(part, dict):
            continue
        if part.get("type") != "tool":
            continue
        state = part.get("state")
        if not isinstance(state, dict):
            continue
        tool_name = (
            str(state.get("tool") or part.get("tool") or "").strip()
            or "unknown"
        )
        status = str(state.get("status") or "").strip().lower() or "unknown"
        raw_input = (
            state.get("input")
            or part.get("input")
            or state.get("args")
            or part.get("args")
        )
        if isinstance(raw_input, dict):
            try:
                import json

                text_input = json.dumps(raw_input, ensure_ascii=False)
            except (TypeError, ValueError):
                text_input = str(raw_input)
        else:
            text_input = str(raw_input or "")
        out.append({
            "tool": tool_name,
            "status": status,
            "input_excerpt": _truncate(text_input, TOOL_INPUT_EXCERPT_CHARS),
        })
    return out


def recent_assistant_activity(
    messages: list[dict[str, Any]],
    *,
    n: int = RECENT_ACTIVITY_DEFAULT_N,
) -> list[dict[str, Any]]:
    """Return the last ``n`` assistant messages from ``messages``,
    each rendered as a compact dict suitable for embedding in a
    notify payload:

        {
          "text": "<truncated to 1000 chars>",
          "completed_at": <float epoch, 0 if unknown>,
          "tools": [{"tool": "bash", "status": "completed",
                     "input_excerpt": "..."}, ...]
        }

    The function is best-effort — fields it cannot determine
    (e.g. a missing ``time.completed``) are returned as empty
    strings or empty lists so the JSON shape is stable and
    webhook handlers do not have to special-case missing keys.
    """
    n = max(1, int(n))
    assistants = [m for m in messages if message_role(m) == "assistant"]
    selected = assistants[-n:]
    out: list[dict[str, Any]] = []
    for message in selected:
        info = (
            message.get("info") if isinstance(message.get("info"), dict)
            else message
        )
        completed_at = 0.0
        if isinstance(info, dict):
            t = info.get("time") or {}
            if isinstance(t, dict):
                completed = t.get("completed")
                if isinstance(completed, (int, float)):
                    completed_at = float(completed)
        text = _truncate(_message_text(message).strip(), ACTIVITY_TEXT_MAX_CHARS)
        out.append({
            "text": text,
            "completed_at": completed_at,
            "tools": _summarise_tools(message),
        })
    return out


def permission_waiting_summary(pending: list[dict[str, Any]]) -> str:
    """Render a one-line summary of the OpenCode pending-permission
    list, used by ``OpenCodeClient.resolve_session_permissions`` to
    decide between status=waiting and status=running."""
    if not pending:
        return "Waiting for permission approval"
    first = pending[0]
    tool = str(first.get("permission") or "tool")
    patterns = first.get("patterns") or []
    hint = str(patterns[0]) if patterns else ""
    base = f"Permission required: {tool}"
    if hint:
        base += f" ({hint})"
    extra = len(pending) - 1
    if extra > 0:
        base += f" (+{extra} more)"
    return base


def messages_indicate_busy(messages: list[dict[str, Any]]) -> bool:
    """Best-effort busy signal derived from a message list. The
    upstream OpenCode ``/session/status`` only emits an entry while
    the agent is actively responding; once the agent pauses the
    entry disappears and we would otherwise show "idle" forever.
    This helper inspects the most recent assistant message and
    returns True if (a) it is still open (no completed timestamp)
    or (b) any of its tool parts are still running."""
    for message in reversed(messages):
        info = message.get("info") if isinstance(message.get("info"), dict) else message
        if isinstance(info, dict) and info.get("role") in {"assistant", "agent"}:
            if _message_is_aborted(info):
                return False
            time_info = info.get("time") or {}
            if not time_info.get("completed"):
                return True
            for part in message.get("parts") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") != "tool":
                    continue
                state = part.get("state") or {}
                tool_status = str(state.get("status", "")).lower()
                if tool_status in {"running", "pending", "active"}:
                    return True
            break
    return False


def _message_is_aborted(info: dict[str, Any]) -> bool:
    error = info.get("error")
    if not isinstance(error, dict):
        return False
    name = str(error.get("name", "")).lower()
    if "abort" in name or "cancel" in name or "stop" in name:
        return True
    message = str(error.get("message", "")).lower()
    return "abort" in message or "cancel" in message or "stopped" in message


def session_total_tokens(session: dict[str, Any]) -> int:
    """Sum all model buckets reported in a session's ``tokens`` block.
    Used by ``HarnessRunner`` to enforce ``max_tokens`` budgets."""
    from .telemetry import parse_session_tokens

    tokens = parse_session_tokens(session)
    return int(sum(tokens.values()))
