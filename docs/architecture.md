# Architecture

OpenLoom is a thin webhook-driven layer on top of OpenCode. The codebase is split into four layers with strict import rules; new capabilities are added as `levels/` packages, never by widening `core/`.

## Layout

```
src/openloom/
├── __init__.py
├── cli.py                       only `openloom serve`; subcommand map is fixed
├── config.py                    Settings dataclass + env loader
├── core/                        events / harness / store / protocols / sink
├── runtime/                     OpenCode HTTP client, prompts, telemetry
├── levels/
│   ├── notify/                  WebhookSink + NotifyConfig (no file sink in 0.12)
│   └── server/                  SessionMonitor, WebSink, ConsoleSink, serve
└── server/                      FastAPI app + routes + static UI ([ui] extra)
docs/                            Detailed user guides
tests/
└── contracts/                   Architecture-enforcement tests
```

The four layers enforce the following invariants (enforced by `tests/contracts/test_architecture.py` and a small set of conventions):

1. `core/` does not import any sibling package.
2. `levels/` modules do not import each other. Shared code lives in `runtime/`.
3. No `try: import` of optional deps in `__init__.py`. Optional capabilities are cold-detected.
4. The harness does not call sink methods directly. State changes go through the store; the store emits an event; sinks subscribe to the bus.
5. API routes only read the store. The event bus pushes notifications, not data.
6. The base `pip install openloom` has only `httpx` + `pyyaml` as runtime deps. `fastapi`, `uvicorn`, etc. are all extras (`[ui]` / `[server]`).
7. Composition is done by decorator (e.g. `register_sink`, `register_checker`), not by inheritance.

## The state-change invariant

Every state change follows exactly one path:

```
some component → store write (store_version + 1) → bus.emit(event) → sinks
```

API routes **read** the store, they do not write to it (other than the explicit POST handlers for task create / status update / abort, which go through the harness's `add_task` / `complete_task` / etc.).

The harness is the only writer of task state. The monitor is the only writer of session status. Sinks are passive subscribers.

## Public contracts

`core/protocols.py` defines the seam between the harness and its dependencies:

| Protocol | Purpose |
|----------|---------|
| `OpenCodePort` | HTTP operations the harness needs (`list_sessions`, `send_prompt_async`, `messages`, `abort_session`, etc.). Implemented by `runtime.opencode.OpenCodeClient`. |
| `StorePort` | Persisted task state. Implemented by `core.store.Store` (SQLite, single connection, WAL). |
| `PromptsPort` | Spec parsing, prompt construction, message-progress heuristics. Implemented by `runtime.prompts`. |
| `StatusPort` | Session status normalization. Implemented by `runtime.session_status`. |
| `Sink` | The 4th party in the event-bus pipeline; the harness emits events, sinks consume them. WebSink (SSE), ConsoleSink (stdout), WebhookSink (HTTP) all implement this. |

`HarnessRunner.__init__` takes these by protocol, not by class. Substituting a fake in a test is just "implement the protocol".

## Assembly: `build_harness()`

The single source of truth for wiring is `runtime.factory.build_harness(settings, **kwargs)`. It returns a `HarnessBundle(harness, store, bus, client)` that `serve.py` uses to mount the FastAPI app, run the harness tick loop, and the monitor refresh loop.

```python
from openloom.runtime.factory import build_harness

bundle = build_harness(settings, extra_sinks=[...])
# bundle.harness.add_task(...)
# bundle.bus.emit(...)
# bundle.client.list_sessions()
```

## Cold detection

Optional dependencies are imported at first use, never at module import time. This means a missing `fastapi` (because the user did not install `[ui]`) only fails when the user actually runs `openloom serve`, not at `openloom` import time.

The pattern:

```python
# in some module
def some_cold_thing():
    try:
        from fastapi import FastAPI
    except ImportError as exc:
        raise RuntimeError("install [ui] first") from exc
    return FastAPI()
```

Sinks are also cold-detected: `WebSink` and `ConsoleSink` register themselves on the `Sink` registry via `@register_sink("web")` / `@register_sink("console")` decorators. The server module imports them to ensure registration before the harness queries the registry for a sink of a given name.

## The 0.12 cut: what we removed

0.12 is a YAGNI cut. The following pieces existed in 0.11 and are now gone, with the reasons:

| Removed | Reason |
|---------|--------|
| `levels/manual/` | The `openloom watch` runner with manual checks / acceptance / step-acknowledgement. Webhook handlers don't need this — the agent loop is the harness loop. |
| `levels/inbox/` | File-watch dispatch. The user wrote markdown files to a directory to enqueue tasks. Webhook replaces this with a single POST. |
| `levels/config/` | The `openloom init` command that wrote `openloom.yaml`. Webhook handlers now construct the task directly. |
| `levels/openspec/` | OpenSpec checkbox completion checks. OpenSpec is a third-party spec; if you need this, run it from your project. |
| `levels/notify/file.py` | File-based notification sink. Webhook is the only delivery path. |
| `runtime/planner.py` | AI task planner. Removed because `TaskSpec` no longer carries `steps` / `acceptance`, so plans have nowhere to land. |
| `EventType.SESSION_STALE_BUSY` | The stale-busy detector. The 0.11 monitor ran an N-pass busy check on every session and emitted this event. With manual-mode gone there is no long-lived observer per session; consumers detect stuck sessions themselves by watching `recent_activity.completed_at`. |
| `EventType.LOG_LINE` | The "abort failed" log line. Only manual-mode's abort path emitted this. |
| `TaskSpec` extras | The old `TaskSpec` had `steps`, `acceptance`, `step_acceptance`, `mode`, `agent`, `check_interval_seconds`, `initial_prompt`, `auto_accept_permissions`, `max_tokens`, `max_runtime_minutes`, `abort_session`. The 0.12 spec is just `name`, `workspace`, `goal`. The harness hardcodes a single 8-second poll interval. |
| `HarnessRunner._check_task` nudge machinery | The 0.11 harness sent "did you finish?" / "continue to step N" / "answer the user's question" prompts. With manual-mode gone, the agent is the source of truth on whether it's done. The harness only emits `TASK_UPDATED` with the current status. |
| `SessionMonitor.stale_busy_sessions` / `forget_session` / `attach_prompts` | All stale-busy infrastructure. |

The user can still:

- Create tasks from anywhere via `POST /api/tasks`.
- Watch live tasks in the web dashboard (no per-task YAML spec required).
- Receive notifications on any webhook for any subset of events.
- Abort and restart a session from a phone with two HTTP calls.

## State diagram (one tick)

```
harness.tick()
  ├─ store.list_due_tasks()  ─── reads only
  ├─ for each task:
  │    ├─ if pending → _start_task()
  │    │     ├─ if active_session_id is set: list_sessions() to verify
  │    │     ├─ else: create_session() to start fresh
  │    │     └─ send_prompt_async() with spec.goal
  │    └─ else: _check_task()
  │         ├─ session_status() + messages()  ─── reads only
  │         ├─ if busy: status="running"
  │         ├─ elif permission_waiting: status="waiting"
  │         ├─ elif latest assistant says TASK COMPLETE: status="completed" → emit TASK_COMPLETED
  │         └─ else: status="running" (idle, awaiting next turn)
  └─ return

monitor.refresh()
  ├─ list_sessions()
  ├─ session_status()  ─── reads only
  └─ publish monitor.sessions / monitor.status / monitor.by_directory
```
