# OpenLoom

[![CI](https://github.com/atbeta/openloom/actions/workflows/ci.yml/badge.svg)](https://github.com/atbeta/openloom/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/openloom)](https://pypi.org/project/openloom/)

**Don't trust the agent's word — trust the file system.**

OpenLoom is a webhook-driven task harness for [OpenCode](https://github.com/opencode-ai/opencode). It gives OpenCode a JSON task API, a session monitor, webhook fan-out, a web dashboard, and a file-inbox storage runner — so you can dispatch work from anywhere, even a phone.

OpenLoom does **not** replace OpenCode. It fills the gaps in its HTTP API.

## Install

Requires Python 3.11+ and a running OpenCode server.

```bash
uv tool install openloom
```

That's it. Server deps, `python-docx`, `requests` — all bundled. No `--with`, no `[extras]`.

## Quick start

### 1. Start OpenCode

```bash
opencode serve
```

### 2. Configure (optional)

Defaults work out of the box with a local OpenCode on port 4096. For custom setups:

```bash
openloom init
```

This writes `~/.openloom/config.yaml`. Edit it, or use env vars:

```bash
export OPENLOOM_OPENCODE_URL=http://127.0.0.1:4096
export OPENLOOM_OPENCODE_USERNAME=opencode
export OPENLOOM_OPENCODE_PASSWORD=your-password
```

### 3. Start OpenLoom

```bash
openloom serve
```

Web dashboard at `http://127.0.0.1:8967`. Verbose logging: `openloom serve -v`.

## Webhook API

Dispatch a task from any HTTP client, CI pipeline, or messaging bot:

```bash
curl -X POST http://127.0.0.1:8967/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"name": "fix-typo", "workspace": "/path/to/project", "goal": "Fix the typo in README.md"}'
```

**Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/tasks` | Create and dispatch a task |
| `GET` | `/api/tasks` | List all tasks |
| `GET` | `/api/tasks/{id}` | Get task status |
| `POST` | `/api/tasks/{id}/abort` | Abort a stuck session |
| `DELETE` | `/api/tasks/{id}` | Delete a task |
| `GET` | `/api/tasks/{id}/events` | SSE stream of task events |

### Task payload

```json
{
  "name": "unique-task-name",
  "workspace": "/absolute/path/to/project",
  "goal": "Natural-language instruction for OpenCode"
}
```

### Webhook notifications

OpenLoom fires webhooks on task lifecycle changes. Configure in `config.yaml`:

```yaml
bus:
  webhook_url: https://your-service.com/hooks/openloom
  webhook_secret: "shared-secret"
```

Event payload:

```json
{
  "event": "TASK_UPDATED",
  "task_name": "fix-typo",
  "task_id": "abc123",
  "status": "completed",
  "timestamp": 1719676800,
  "timestamp_iso": "2024-06-29T12:00:00+00:00",
  "recent_activity": [...]
}
```

## Storage Runner

Drop files into an inbox directory and OpenLoom dispatches them as tasks automatically.

### Setup

Add a storage level to `config.yaml`:

```yaml
levels:
  storage:
    path: ./inbox           # directory to watch
    poll_interval_s: 5      # check every 5 seconds
    class: myapp.MyConnector  # optional connector class
```

### Supported formats

| Extension | Format |
|-----------|--------|
| `.yaml` / `.yml` | Task spec (name, workspace, goal) |
| `.md` | Markdown — first heading as name, body as goal |
| `.txt` | Plain text |
| `.docx` | Word documents (auto-detected) |

### Connectors

Storage connectors fetch task files from external sources. A connector is a Python class with a `download(path) -> bytes | None` method. Put it in `./connectors/`:

```python
# connectors/mysource.py
import requests
from openloom.levels.storage.base import Connector

class MyConnector(Connector):
    def __init__(self):
        self._session = requests.Session()
        self._session.proxies = {"http": None, "https": None}  # bypass system proxy

    def download(self, path: str) -> bytes | None:
        url = f"https://my-server/files/{path}"
        r = self._session.get(url, timeout=30)
        r.raise_for_status()
        return r.content
```

Configure in `config.yaml`:

```yaml
levels:
  storage:
    class: mysource.MyConnector
```

## Dashboard

`openloom serve` serves a Svelte dashboard at `http://127.0.0.1:8967`:

- **Dashboard** — live task overview
- **Tasks** — create, browse, abort, delete tasks
- **Activity** — event stream with session messages
- **Session drawer** — inspect OpenCode sessions in real time

## Configuration

Run `openloom init` for a starter `~/.openloom/config.yaml`. Key sections:

```yaml
opencode_url: "http://127.0.0.1:4096"
server:
  port: 8967
bus:
  webhook_url: ""
  webhook_secret: ""
levels:
  storage:
    path: ./inbox
    poll_interval_s: 5
```

Full reference: [docs/configuration.md](docs/configuration.md).

## Requirements

- Python 3.11+
- [OpenCode](https://github.com/opencode-ai/opencode) running with HTTP API enabled
