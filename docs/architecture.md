# Architecture

OpenLoom is a thin harness and observer on top of OpenCode. The codebase is split into four layers with strict import rules; new capabilities are added as `levels/` packages, never by widening `core/`.

## Layout

```
src/openloom/
├── core/        # events / harness / store / 3 ABC + protocols (keep lean)
├── runtime/     # OpenCode HTTP client, session status, prompts
├── levels/      # Progressive capabilities (manual, config, ui, inbox, notify, …)
└── server/      # create_app(harness=) factory + routes + static (needs [ui])
docs/            # Detailed user guides (configuration, inbox, notifications, …)
tests/           # contracts/ holds the architecture-enforcement tests
```

The four layers enforce the following invariants (enforced by `tests/contracts/test_architecture.py`):

1. `core/` does not import any sibling package.
2. `levels/` modules do not import each other. Shared code lives in `runtime/` or `server/`.
3. No `try: import` of optional deps in `__init__.py`. Optional capabilities are cold-detected (the import happens at first use, the result is not cached).
4. The harness does not call sink methods directly. State changes go through the store; the store emits an event; sinks subscribe to the bus.
5. API routes only read the store. The event bus pushes notifications, not data.
6. The base `pip install openloom` has only `httpx` + `pyyaml` as runtime deps. `fastapi`, `uvicorn`, etc. are all extras (`[ui]` / `[server]`).
7. Composition is done by decorator (e.g. `checker.with_pre_archive(...)`), not by inheritance.

## The state-change invariant

Every state change follows exactly one path:

```
some component → store write (store_version + 1) → bus.emit(event) → sinks
```

API routes **read** the store, they do not write to it (other than the explicit POST handlers for task create / status update / inbox trigger, which go through the harness's `add_task` / `complete_task` / `pause_task`).

The harness is the only writer of task state. The monitor is the only writer of session status. Sinks are passive subscribers.

## Public contracts

`core/protocols.py` defines the seam between the harness and its dependencies:

| Protocol | Purpose |
|----------|---------|
| `OpenCodePort` | HTTP operations the harness needs (`list_sessions`, `send_prompt_async`, `messages`, …). Implemented by `runtime.opencode.OpenCodeClient`. |
| `StorePort` | Persisted task state. Implemented by `core.store.Store` (SQLite, single connection, WAL). |
| `CheckerPort` | Re-check the current step before the harness decides the task is done. Implemented by `levels.manual.checker.StringChecker` and its decorator variants. |
| `CheckResultProtocol` | Result of a check. |
| `PromptsPort` | Spec parsing, prompt construction, message-progress heuristics. Implemented by `runtime.prompts`. |
| `StatusPort` | Session status normalization. Implemented by `runtime.session_status`. |

`HarnessRunner.__init__` takes these by protocol, not by class. Substituting a fake in a test is just "implement the protocol".

## Assembly: `build_harness()`

The single source of truth for wiring is `runtime.factory.build_harness(settings, **kwargs)`. It returns a `HarnessBundle(harness, store, bus, client)` that `serve`, `watch`, and tests all use.

```python
from openloom.runtime.factory import build_harness

bundle = build_harness(settings, extra_sinks=[...])
# bundle.harness.add_task(...)
# bundle.bus.emit(...)
# bundle.client.list_sessions()
```

`serve.py` and `watch.py` add background tasks (the inbox watcher, etc.) on top of the bundle, but the assembly path is the same.

## Cold detection

Optional dependencies are imported at first use, never at module import time. This means a missing `fastapi` (because the user did not install `[ui]`) only fails when the user actually runs `openloom serve`, not at `openloom watch` startup.

The pattern:

```python
# in some module
def some_cold_thing():
    try:
        from fastapi import FastAPI  # or whatever
    except ImportError as exc:
        raise RuntimeError("install [ui] first") from exc
    return FastAPI()
```

`tests/contracts/test_architecture.py` scans `core/` and `__init__.py` files for `try: import` to enforce this.

## What "level" means

A `level/` is a self-contained capability that can be loaded or skipped without affecting the rest of the system:

| Level | What it adds |
|-------|--------------|
| `manual` | The `openloom watch` runner: harness loop + a checker + a console sink. |
| `config` | `openloom init` / `openloom.yaml` parsing. |
| `openspec` | OpenSpec checkbox completion checks. |
| `ui` | Web dashboard (`[ui]` extra). |
| `validate` | Pre-archive validation hooks (run the project's pytest/mypy). |
| `github` | GitHub integration. |
| `inbox` | `InboxWatcher` for `OPENLOOM_INBOX_DIR` polling. |
| `notify` | Webhook + file sinks. |
| `server` | The `openloom serve` entry point (FastAPI app + uvicorn). |

A level that needs runtime HTTP, FastAPI, or a database pulls those in as cold-detected deps. A level that needs no extras (e.g. `manual`) runs in the base install.

## State diagram (one tick)

```
harness.tick()
  ├─ store.list_due_tasks()  ─── reads only
  ├─ for each task:
  │    ├─ spec = PromptsPort.from_dict(task["spec"])
  │    ├─ if task.status == "pending": _start_task()
  │    │     ├─ if spec.abort_session && session exists: client.abort_session()
  │    │     ├─ client.send_prompt_async(...)
  │    │     └─ store.update_task_status("running")
  │    │         └─ bus.emit(TASK_STARTED) → sinks
  │    └─ else: _check_task()
  │         ├─ checker.check(spec, messages)
  │         ├─ detect_progress(...)
  │         └─ store.update_task_progress(...)
  │             └─ bus.emit(TASK_UPDATED | TASK_COMPLETED | TASK_FAILED)
  └─ return
```

`monitor.refresh()` runs on its own 8-second loop:

```
monitor.refresh()
  ├─ client.list_sessions()  ─── reads
  ├─ client.session_status()  ─── reads
  ├─ for busy sessions: client.messages(sid, limit=4)  ─── reads
  │   └─ detect latest completed timestamp (for stale-busy tracking)
  ├─ update _stale_count per session
  ├─ when threshold crossed: bus.emit(SESSION_STALE_BUSY) → sinks
  └─ return
```

The harness and the monitor share the `EventBus` but never share state directly. They each write to the same bus and read from the same store, which is the only correct way to compose them.
