from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

MIN_CHECK_INTERVAL_SECONDS = 300  # 5 minutes


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
    auto_accept_permissions: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
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
        }

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
            auto_accept_permissions=bool(data.get("auto_accept_permissions", True)),
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

    meta_re = re.compile(r"^(workspace|mode|agent|check_interval(?:_seconds)?)\s*:\s*(.+)$", re.I)
    for line in lines[:20]:
        if line.startswith("# "):
            name = line[2:].strip() or name
            continue
        m = meta_re.match(line.strip())
        if not m:
            continue
        key, value = m.group(1).lower(), m.group(2).strip()
        if key == "workspace":
            workspace = value.strip("` ")
        elif key == "mode":
            mode = value
        elif key == "agent":
            agent = value
        elif key.startswith("check_interval"):
            check_interval_seconds = _interval_from_text(value)

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
    )


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
    all_steps_reported = step_count > 0 and step_done >= step_count
    has_final = acceptance_count > 0
    global_ok = not has_final or acceptance_checked >= acceptance_count

    if has_final:
        return global_ok and (task_complete or all_steps_reported)
    return task_complete or all_steps_reported


def detect_progress(text: str, spec: TaskSpec) -> dict[str, Any]:
    upper = text.upper()
    task_complete = "TASK COMPLETE" in upper
    step_done = 0
    for match in re.finditer(r"STEP DONE:\s*(\d+)", text, re.I):
        step_done = max(step_done, int(match.group(1)))

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


def detect_progress_from_messages(messages: list[dict[str, Any]], spec: TaskSpec) -> dict[str, Any]:
    return detect_progress(assistant_transcript(messages), spec)


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


def agent_awaiting_continue(text: str) -> bool:
    lower = text.lower()
    return any(pattern in lower for pattern in _CONTINUE_PATTERNS)


def needs_continue_reply(messages: list[dict[str, Any]]) -> bool:
    awaiting_index: int | None = None
    for idx in range(len(messages) - 1, -1, -1):
        if message_role(messages[idx]) != "assistant":
            continue
        if agent_awaiting_continue(_message_text(messages[idx])):
            awaiting_index = idx
            break
    if awaiting_index is None:
        return False
    for idx in range(awaiting_index + 1, len(messages)):
        if message_role(messages[idx]) == "user":
            return False
    return True


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
