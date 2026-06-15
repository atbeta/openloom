from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

MIN_CHECK_INTERVAL_SECONDS = 300  # 5 minutes
MAX_IDLE_NUDGES = 0  # 0 = disabled; set >0 to auto-pause after N idle checks without progress


def normalize_check_interval_seconds(
    value: int | None = None,
    *,
    minutes: int | None = None,
    default: int = MIN_CHECK_INTERVAL_SECONDS,
) -> int:
    if minutes is not None:
        return max(MIN_CHECK_INTERVAL_SECONDS, int(minutes) * 60)
    if value is None:
        return default
    return max(MIN_CHECK_INTERVAL_SECONDS, int(value))


@dataclass
class TaskSpec:
    name: str
    workspace: str
    goal: str
    steps: list[str] = field(default_factory=list)
    step_acceptance: list[list[str]] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)
    mode: str = "normal"
    agent: str = "opencode"
    check_interval_seconds: int = 300
    initial_prompt: str | None = None
    auto_accept_permissions: bool = False
    max_tokens: int | None = None
    max_runtime_minutes: int | None = None
    # When set on a session-bound task, the harness aborts any
    # in-flight agent loop on the target session *before* sending
    # the new prompt. Used by the inbox / webhook dispatch path to
    # recover from a stuck session (typically one that just fired a
    # SESSION_STALE_BUSY notification). Default False — ordinary
    # watch dispatches should never abort.
    abort_session: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = {
            "name": self.name,
            "workspace": self.workspace,
            "goal": self.goal,
            "steps": self.steps,
            "step_acceptance": self.step_acceptance,
            "acceptance": self.acceptance,
            "mode": self.mode,
            "agent": self.agent,
            "check_interval_seconds": self.check_interval_seconds,
            "initial_prompt": self.initial_prompt,
            "auto_accept_permissions": self.auto_accept_permissions,
            "abort_session": self.abort_session,
        }
        if self.max_tokens is not None:
            data["max_tokens"] = self.max_tokens
        if self.max_runtime_minutes is not None:
            data["max_runtime_minutes"] = self.max_runtime_minutes
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskSpec:
        steps, step_acceptance, global_acceptance = _parse_steps_and_acceptance(data)
        interval = _parse_interval_seconds(data)
        return cls(
            name=str(data.get("name") or "Untitled task").strip() or "Untitled task",
            workspace=str(data.get("workspace") or "").strip(),
            goal=str(data.get("goal") or "").strip(),
            steps=steps,
            step_acceptance=step_acceptance,
            acceptance=global_acceptance,
            mode=str(data.get("mode") or "normal").strip() or "normal",
            agent=str(data.get("agent") or "opencode").strip() or "opencode",
            check_interval_seconds=interval,
            initial_prompt=(str(data["initial_prompt"]).strip() if data.get("initial_prompt") else None),
            auto_accept_permissions=bool(data.get("auto_accept_permissions", False)),
            max_tokens=_optional_positive_int(data.get("max_tokens")),
            max_runtime_minutes=_optional_positive_int(data.get("max_runtime_minutes")),
            abort_session=bool(data.get("abort_session", False)),
        )


def permission_waiting_summary(pending: list[dict[str, Any]]) -> str:
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


def _parse_steps_and_acceptance(data: dict[str, Any]) -> tuple[list[str], list[list[str]], list[str]]:
    raw_steps = data.get("steps") or []
    steps: list[str] = []
    step_acceptance: list[list[str]] = []

    if raw_steps and isinstance(raw_steps[0], dict):
        for item in raw_steps:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("name") or "").strip()
            if not title:
                continue
            acc = [str(a).strip() for a in (item.get("acceptance") or []) if str(a).strip()]
            steps.append(title)
            step_acceptance.append(acc)
    else:
        steps = [str(s).strip() for s in raw_steps if str(s).strip()]
        step_acceptance = [[] for _ in steps]

    if data.get("step_acceptance") and isinstance(data["step_acceptance"], list):
        parsed = []
        for row in data["step_acceptance"]:
            if isinstance(row, list):
                parsed.append([str(a).strip() for a in row if str(a).strip()])
            else:
                parsed.append([])
        if len(parsed) == len(steps):
            step_acceptance = parsed

    global_acceptance = [
        str(a).strip()
        for a in (data.get("global_acceptance") or data.get("acceptance") or [])
        if str(a).strip()
    ]
    if not steps and global_acceptance and not step_acceptance:
        step_acceptance = [[] for _ in steps]
    while len(step_acceptance) < len(steps):
        step_acceptance.append([])
    return steps, step_acceptance[: len(steps)], global_acceptance


def _interval_from_text(value: str, *, default_seconds: int = MIN_CHECK_INTERVAL_SECONDS) -> int:
    raw = value.strip().lower()
    digits = int(re.sub(r"[^0-9]", "", raw) or "0")
    if not digits:
        return default_seconds
    if raw.endswith("m"):
        return normalize_check_interval_seconds(minutes=digits)
    if raw.endswith("s"):
        return normalize_check_interval_seconds(value=digits)
    return normalize_check_interval_seconds(minutes=digits)


def _optional_positive_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _parse_interval_seconds(data: dict[str, Any]) -> int:
    if data.get("check_interval_minutes") is not None:
        return normalize_check_interval_seconds(minutes=int(data["check_interval_minutes"]))
    if data.get("check_interval_seconds") is not None:
        return normalize_check_interval_seconds(value=int(data["check_interval_seconds"]))
    if data.get("check_interval") is not None:
        value = data["check_interval"]
        if isinstance(value, str):
            return _interval_from_text(value)
        return normalize_check_interval_seconds(minutes=int(value))
    return MIN_CHECK_INTERVAL_SECONDS


def _title_from_prompt(prompt: str, max_len: int = 60) -> str:
    line = prompt.strip().splitlines()[0] if prompt.strip() else "Task"
    if len(line) > max_len:
        return line[: max_len - 1] + "…"
    return line or "Task"


def task_spec_from_prompt(
    prompt: str,
    workspace: str,
    *,
    check_interval_seconds: int | None = None,
    agent: str = "opencode",
    mode: str = "normal",
    name: str | None = None,
) -> TaskSpec:
    text = prompt.strip()
    if not text:
        raise ValueError("prompt is required")
    interval = normalize_check_interval_seconds(value=check_interval_seconds)
    return TaskSpec(
        name=name or _title_from_prompt(text),
        workspace=workspace.strip(),
        goal=text,
        steps=[],
        acceptance=[],
        mode=mode.strip() or "normal",
        agent=agent.strip() or "opencode",
        check_interval_seconds=interval,
        initial_prompt=text,
    )


def parse_task_spec(text: str, fmt: str = "yaml") -> TaskSpec:
    normalized = (fmt or "yaml").strip().lower()
    if normalized == "markdown":
        return _parse_markdown(text)
    return TaskSpec.from_dict(_parse_yaml(text))


def _parse_yaml(text: str) -> dict[str, Any]:
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("Task spec must be a YAML mapping")
    return data


_SESSION_META_RE = re.compile(r"^session(?:\s|_id)?\s*:\s*(.+)$", re.I)
_ABORT_META_RE = re.compile(r"^abort(?:\s+session)?\s*:\s*(.+)$", re.I)
# Task-completion markers the agent is allowed to emit. The system
# prompt tells it to say "TASK COMPLETE" but LLMs are not strict
# about exact phrasing — they say "TASK DONE", "task is complete",
# "all steps done", "all checks pass", etc. The detector has to
# catch the common variants or the harness nudges the agent
# forever and auto-pauses the task. Each pattern is case-insensitive
# (the caller uppercases the text first) and whole-line (so we
# don't false-positive on "the task is not complete yet" — the
# lookbehind requires a non-letter boundary on the left).
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


def extract_session_id_from_markdown(text: str) -> str:
    """Return the ``session: <id>`` (or ``session_id: <id>``) value from
    the first 20 lines of a markdown task spec, or an empty string.

    Lives in ``runtime.prompts`` because it operates on the same
    frontmatter the markdown parser already handles; both the
    inbox watcher and the ``/api/inbox/trigger`` HTTP route need
    it, and ``runtime`` is the shared layer the others import.
    """
    for line in text.splitlines()[:20]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _SESSION_META_RE.match(stripped)
        if m:
            return m.group(1).strip().strip("`\"' ")
    return ""


def _parse_markdown(text: str) -> TaskSpec:
    lines = text.splitlines()
    name = "Untitled task"
    workspace = ""
    mode = "normal"
    agent = "opencode"
    check_interval_seconds = 300
    goal = ""
    acceptance: list[str] = []
    steps: list[str] = []
    abort_session = False

    meta_re = re.compile(r"^(workspace|mode|agent|check_interval(?:_seconds)?)\s*:\s*(.+)$", re.I)
    for line in lines[:20]:
        if line.startswith("# "):
            name = line[2:].strip() or name
            continue
        stripped = line.strip()
        m = meta_re.match(stripped)
        if m:
            key, value = m.group(1).lower(), m.group(2).strip()
            if key == "workspace":
                workspace = value.strip("` ")
            elif key == "mode":
                mode = value
            elif key == "agent":
                agent = value
            elif key.startswith("check_interval"):
                check_interval_seconds = _interval_from_text(value)
            continue
        m_abort = _ABORT_META_RE.match(stripped)
        if m_abort:
            abort_session = _truthy(m_abort.group(1))

    section = ""
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("## goal"):
            section = "goal"
            continue
        if stripped.lower().startswith("## acceptance"):
            section = "acceptance"
            continue
        if stripped.lower().startswith("## steps"):
            section = "steps"
            continue
        if stripped.startswith("## "):
            section = ""
            continue
        if not stripped:
            continue

        if section == "goal":
            goal = f"{goal}\n{stripped}".strip() if goal else stripped
        elif section == "acceptance":
            item = re.sub(r"^[-*]\s*", "", stripped)
            item = re.sub(r"^\[[ xX]\]\s*", "", item).strip()
            if item:
                acceptance.append(item)
        elif section == "steps":
            item = re.sub(r"^\d+\.\s*", "", stripped).strip()
            if item:
                steps.append(item)

    return TaskSpec(
        name=name,
        workspace=workspace,
        goal=goal,
        steps=steps,
        acceptance=acceptance,
        mode=mode,
        agent=agent,
        check_interval_seconds=check_interval_seconds,
        abort_session=abort_session,
    )


def extract_abort_from_markdown(text: str) -> bool:
    """Return ``True`` when the markdown frontmatter has
    ``abort: true`` (or ``abort session: true``). Used by the
    inbox / webhook dispatch path to abort the existing session
    before sending a follow-up prompt. Default ``False``.
    """
    for line in text.splitlines()[:20]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _ABORT_META_RE.match(stripped)
        if m:
            return _truthy(m.group(1))
    return False


def _truthy(raw: str) -> bool:
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def build_bootstrap_prompt(spec: TaskSpec, *, current_step: int = 0) -> str:
    if spec.initial_prompt and not spec.steps and not spec.acceptance and not spec.step_acceptance:
        return spec.initial_prompt

    step_blocks: list[str] = []
    for index, step in enumerate(spec.steps):
        accs = spec.step_acceptance[index] if index < len(spec.step_acceptance) else []
        if accs:
            acc_lines = "\n".join(f"   - [ ] {item}" for item in accs)
        else:
            acc_lines = "   - [ ] Step completed"
        step_blocks.append(f"{index + 1}. {step}\n{acc_lines}")
    steps_block = "\n\n".join(step_blocks) or "1. Complete the goal\n   - [ ] Step completed"
    global_block = "\n".join(f"- [ ] {item}" for item in spec.acceptance)
    global_section = f"\nFinal checks (whole task):\n{global_block}\n" if global_block else ""
    step_name = spec.steps[current_step] if 0 <= current_step < len(spec.steps) else "Finish remaining work"

    return f"""You are executing an OpenLoom harness task.

Task: {spec.name}
Workspace: {spec.workspace}

Goal:
{spec.goal or spec.name}

Steps (each step has its own acceptance):
{steps_block}
{global_section}
Current focus: step {current_step + 1} — {step_name}

Proceed through steps autonomously. Do not ask for confirmation between steps.

When you complete a step, include a line: STEP DONE: <number>
When all step and final checks pass, include a line: TASK COMPLETE
"""


def build_periodic_check_prompt(
    spec: TaskSpec,
    *,
    current_step: int,
    progress: dict[str, Any],
    completed_steps: list[int],
) -> str:
    step_lines: list[str] = []
    for index, step in enumerate(spec.steps):
        step_number = index + 1
        markers = completed_steps + ([progress["step_done"]] if progress["step_done"] else [])
        done = step_number in markers or progress["step_done"] >= step_number
        accs = spec.step_acceptance[index] if index < len(spec.step_acceptance) else []
        acc_hint = f" ({len(accs)} checks)" if accs else ""
        step_lines.append(f"{step_number}. {step}{' (done)' if done else ''}{acc_hint}")
    steps_block = "\n\n".join(step_lines) or "1. Complete the goal"
    global_block = "\n".join(f"- [ ] {item}" for item in spec.acceptance)
    global_section = f"\nFinal checks:\n{global_block}\n" if global_block else ""
    focus_index = min(current_step, len(spec.steps) - 1) if spec.steps else 0
    focus_step = spec.steps[focus_index] if spec.steps else "Finish remaining work"

    return f"""OpenLoom harness periodic check for "{spec.name}".

The session is idle. Confirm task status before we schedule the next check.

Steps:
{steps_block}
{global_section}
Reply with ONE of:
- STEP DONE: <number> — if a step is finished (required after each step)
- TASK COMPLETE — if all step and final checks pass
- Or continue working on step {focus_index + 1}: {focus_step}

Proceed autonomously. Do not ask for confirmation."""


def build_final_checks_nudge(spec: TaskSpec) -> str:
    final_block = "\n".join(f"- [ ] {item}" for item in spec.acceptance)
    return (
        f'OpenLoom harness check for "{spec.name}".\n\n'
        "All steps appear done. Confirm each final check:\n"
        f"{final_block}\n\n"
        "Reply with TASK COMPLETE when every final check passes."
    )


def _extract_final_checks_section(text: str) -> str:
    match = re.search(r"final checks(?: \(whole task\))?\s*:\s*(.*)", text, re.I | re.S)
    if not match:
        return ""
    section = match.group(1)
    section = re.split(
        r"\n\s*(?:Current focus|Reply with|Proceed autonomously|OpenLoom harness)",
        section,
        maxsplit=1,
        flags=re.I,
    )[0]
    return section.strip()


def count_global_acceptance_checked(text: str, acceptance: list[str]) -> int:
    if not acceptance:
        return 0

    section = _extract_final_checks_section(text)
    if section:
        checked = len(re.findall(r"\[[xX]\]", section))
        return min(checked, len(acceptance))

    count = 0
    for item in acceptance:
        criterion = item.strip()
        if not criterion:
            continue
        snippet = re.escape(criterion[:50])
        if re.search(rf"^\s*[-*]?\s*\[[xX]\][^\n]*{snippet}", text, re.I | re.M):
            count += 1
        elif re.search(rf"^\s*[-*]?\s*[^\n]*{snippet}[^\n]*\[[xX]\]", text, re.I | re.M):
            count += 1
    return min(count, len(acceptance))


def task_is_finished(
    *,
    task_complete: bool,
    step_done: int,
    acceptance_checked: int,
    step_count: int,
    acceptance_count: int,
) -> bool:
    """Decide whether the task should be marked ``completed``.

    The harness uses this to decide between nudging the agent and
    transitioning the task to ``completed``. The rule is intentionally
    simple: **when the agent explicitly reports ``TASK COMPLETE`` the
    task is finished.**

    Background: the original code also required
    ``acceptance_checked >= acceptance_count`` whenever the spec had
    an ``## acceptance`` block. That breaks the common LLM behaviour
    of completing all the work and reporting ``TASK COMPLETE`` in a
    single final message without restating every acceptance item
    with a ``- [x]`` checkbox. The harness would then re-prompt with
    ``Waiting on final checks`` forever, the agent would reply
    ``TASK COMPLETE`` again, and the cycle never resolved. The
    acceptance block is still parsed and surfaced in the dashboard
    (``result.acceptance_progress``); it just does not gate the
    completion transition.
    """
    if task_complete:
        return True
    all_steps_reported = step_count > 0 and step_done >= step_count
    return all_steps_reported


def detect_progress(text: str, spec: TaskSpec) -> dict[str, Any]:
    upper = text.upper()
    task_complete = _TASK_COMPLETE_RE.search(upper) is not None
    step_done = 0
    # The system prompt tells the agent to say ``STEP DONE: <n>``
    # but LLMs frequently invert the order (``STEP <n> DONE``) or
    # drop the colon (``STEP DONE <n>``). Match both orderings so
    # the harness does not get stuck on a stylistic variant.
    for match in re.finditer(
        r"STEP\s+(?:DONE\s*:?\s*(\d+)|(\d+)\s+DONE)",
        text,
        re.I,
    ):
        value = match.group(1) or match.group(2)
        try:
            step_done = max(step_done, int(value))
        except (TypeError, ValueError):
            continue

    checked = count_global_acceptance_checked(text, spec.acceptance)
    total_acceptance = len(spec.acceptance)
    acceptance_progress = (
        min(1.0, checked / total_acceptance) if total_acceptance else (1.0 if task_complete else 0.0)
    )

    return {
        "task_complete": task_complete,
        "step_done": step_done,
        "acceptance_checked": checked,
        "acceptance_total": total_acceptance,
        "acceptance_progress": acceptance_progress,
    }


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


def assistant_transcript(messages: list[dict[str, Any]], limit: int | None = None) -> str:
    assistants = [m for m in messages if message_role(m) == "assistant"]
    if limit is None:
        return "\n\n".join(_message_text(m) for m in assistants if _message_text(m))
    return "\n\n".join(_message_text(m) for m in assistants[-limit:] if _message_text(m))


# Notification payload tuning knobs. Kept as module constants so
# tests can reference them by name and a future CLI flag can
# override them via Settings.
RECENT_ACTIVITY_DEFAULT_N = 3
ACTIVITY_TEXT_MAX_CHARS = 1_000
TOOL_INPUT_EXCERPT_CHARS = 80


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
        # A tool part without a usable state dict cannot be
        # summarised meaningfully — skip rather than emit a
        # placeholder that would mislead a remote operator.
        if not isinstance(state, dict):
            continue
        # Tool name lives in state.tool (OpenCode 1.16) or in the
        # part itself; tolerate both.
        tool_name = (
            str(state.get("tool") or part.get("tool") or "").strip()
            or "unknown"
        )
        status = str(state.get("status") or "").strip().lower() or "unknown"
        # Input: try the canonical keys; fall back to the part's
        # own input field. The shape varies across OpenCode
        # versions, so we just string-coerce anything we find.
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


def detect_progress_from_messages(messages: list[dict[str, Any]], spec: TaskSpec) -> dict[str, Any]:
    return detect_progress(assistant_transcript(messages), spec)


def session_total_tokens(session: dict[str, Any]) -> int:
    from .telemetry import parse_session_tokens

    tokens = parse_session_tokens(session)
    return int(sum(tokens.values()))


_CONTINUE_PATTERNS = (
    "should i proceed",
    "should i continue",
    "want me to proceed",
    "want me to continue",
    "shall i proceed",
    "ready for step",
    "may i proceed",
    "do you want me to",
)

_ASKING_PATTERNS = (
    "which would you prefer",
    "which option",
    "would you like me to",
    "would you prefer",
    "should i use",
    "should i choose",
    "should i go with",
    "what should i",
    "how should i",
    "can i proceed",
    "is it ok to",
    "is it okay to",
    "please confirm",
    "let me know if",
    "which approach",
    "or should i",
    "do you want",
    "confirm whether",
)


def agent_awaiting_continue(text: str) -> bool:
    lower = text.lower()
    return any(pattern in lower for pattern in _CONTINUE_PATTERNS)


def looks_like_asking(text: str) -> bool:
    lower = text.lower().strip()
    if not lower:
        return False
    if agent_awaiting_continue(lower):
        return True
    if any(pattern in lower for pattern in _ASKING_PATTERNS):
        return True
    if "?" not in lower or len(lower) > 500:
        return False
    if "```" in lower or "http://" in lower or "https://" in lower:
        return False
    asking_starters = (
        "should ", "would ", "do you", "can i", "may i", "shall i",
        "which ", "what ", "how ", "could i",
    )
    return any(lower.lstrip().startswith(starter) for starter in asking_starters)


def auto_decide_reply(*, step_name: str | None = None) -> str:
    step_line = f" Focus on step: {step_name}." if step_name else ""
    return (
        "Yes — proceed autonomously with your recommended approach."
        f"{step_line} Do not ask for confirmation between harness steps; implement directly."
        " Reply with STEP DONE: <number> when a step is finished, or TASK COMPLETE when all checks pass."
    )


def needs_asking_reply(messages: list[dict[str, Any]]) -> bool:
    for idx in range(len(messages) - 1, -1, -1):
        if message_role(messages[idx]) != "assistant":
            continue
        if not looks_like_asking(_message_text(messages[idx])):
            continue
        for later in range(idx + 1, len(messages)):
            if message_role(messages[later]) == "user":
                return False
        return True
    return False


def needs_continue_reply(messages: list[dict[str, Any]]) -> bool:
    return needs_asking_reply(messages)


def nudge_fingerprint(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()[:16]


def last_log_detail(task: dict[str, Any]) -> str:
    log = task.get("check_log") or []
    if not log:
        return ""
    return str(log[-1].get("detail") or "")


def assistant_message_signature(messages: list[dict[str, Any]]) -> str:
    """Return a stable identifier of "the latest assistant message
    we have observed". Used to invalidate the nudge dedup when
    the agent has produced new content since the last nudge — a
    fresh turn from the agent should not be silently dropped
    just because the harness decided to repeat the same prompt.

    The signature is the (id, completed-timestamp) of the latest
    assistant message, or the empty string if there is no
    assistant message yet. Both id and timestamp are stable
    against edits to earlier messages; the highest-value-of-each
    pair gives a strong guarantee that a brand-new turn advances
    the signature.
    """
    latest_id = ""
    latest_completed = 0.0
    for message in messages:
        info = message.get("info") if isinstance(message.get("info"), dict) else message
        if not isinstance(info, dict):
            continue
        if str(info.get("role", "")).lower() not in {"assistant", "agent"}:
            continue
        mid = str(info.get("id") or "")
        t = info.get("time") or {}
        completed = t.get("completed") if isinstance(t, dict) else None
        completed_f = float(completed) if isinstance(completed, (int, float)) else 0.0
        if mid or completed_f:
            # Prefer the most recently completed; the comparison is
            # by completed timestamp first, then by id (lexicographic)
            # so two timestamps that are equal still produce a
            # stable order.
            if completed_f > latest_completed or (
                completed_f == latest_completed and mid > latest_id
            ):
                latest_id = mid
                latest_completed = completed_f
    if not latest_id and not latest_completed:
        return ""
    return f"{latest_id}|{latest_completed}"


def already_nudged(
    task: dict[str, Any],
    nudge: str,
    current_signature: str = "",
) -> bool:
    """Return True if we have already sent this exact nudge for
    the current assistant-message state. ``current_signature`` is
    the signature of the latest assistant message *at the time of
    this call* — if it differs from the signature stored at the
    last nudge, the dedup is invalidated and we re-nudge.

    This avoids the previous bug where two consecutive checks
    with the same nudge text but with a fresh agent reply in
    between were both treated as duplicates; the harness would
    then auto-pause the task even though the agent had moved on.
    """
    fp = nudge_fingerprint(nudge)
    detail = last_log_detail(task)
    if not detail.startswith(f"nudge:{fp}"):
        return False
    # Detail format: "nudge:<fp>|<status>[:<last_signature>]". The
    # signature is appended by the harness after a successful send.
    # If we cannot find one (old log entries from before the fix)
    # we conservatively assume the dedup is invalid.
    suffix = detail[len(f"nudge:{fp}"):]
    if "|" not in suffix:
        return False
    after_pipe = suffix.split("|", 1)[1]
    last_signature = ""
    if ":" in after_pipe:
        last_signature = after_pipe.split(":", 1)[1]
    if not current_signature:
        # Caller didn't provide a signature — fall back to the old
        # single-fingerprint behaviour (better than nothing).
        return True
    return current_signature == last_signature


def messages_indicate_busy(messages: list[dict[str, Any]]) -> bool:
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


CONFIG_FILENAME = "openloom.yaml"


def load_config(path: str | None = None) -> dict[str, Any]:
    target = path or os.path.join(os.getcwd(), CONFIG_FILENAME)
    if not os.path.exists(target):
        raise FileNotFoundError(f"{target} not found. Run 'openloom init' first.")
    with open(target) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("openloom.yaml must be a YAML mapping")
    return data


def load_task_spec(path: str | None = None) -> TaskSpec:
    return TaskSpec.from_dict(load_config(path))
