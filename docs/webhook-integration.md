# OpenLoom 使用说明（v0.14）

## 这个工具是干什么的

OpenLoom 是一个跑在公司电脑上的"AI 任务调度器"。它帮你：

- **盯着一批任务**，定期检查 OpenCode 的执行进度
- **自动判断**任务什么时候算完成——agent 在回复里写了 `TASK COMPLETE`，或者 session idle 时（默认开启）
- **自动接受权限弹窗**——agent 调工具时不用手动点 Allow（默认开启）
- **把结果推出去**（出站 webhook）
- **接外部的请求**（入站 webhook）

一句话：**让 OpenCode 跑长任务、可以远程遥控**。

---

## 为什么会有这个工具

在公司里有个现实问题：

| 设备 | 能做什么 | 不能做什么 |
|------|----------|------------|
| **手机** | 上传下载文件到公司网盘 | 不能直接访问公司电脑的 webhook |
| **公司电脑** | 收发 webhook、上传下载文件 | 不能随身带走 |

普通 webhook 集成（比如 GitHub → 触发任务）需要**手机能访问到公司电脑的 HTTP 端点**。但公司电脑在防火墙后面，手机从外面是访问不到的。

**所以我们用网盘做中介**：

```
手机（上传任务 docx/json 到网盘）
   ↓ 网盘
公司电脑（connector 轮询下载 → 转成 webhook → OpenLoom → OpenCode 执行）
   ↓ 网盘
手机（下载结果 docx/json）
```

[`openloom-connector`](https://github.com/atbeta/openloom-connector) 这个独立包就是干这个的：轮询网盘、把文件转成 webhook、调 OpenLoom。

---

## 核心架构

```
┌──────────────┐       ┌──────────────────┐       ┌──────────────┐
│  手机 / 外部  │  →→→  │  openloom-       │  →→→  │   OpenLoom   │
│ （网盘客户端）│       │  connector       │       │   (公司电脑)  │
│              │  ←←←  │ （轮询网盘）     │  ←←←  │              │
└──────────────┘       └──────────────────┘       └──────┬───────┘
                                                         │
                                                         ↓ webhook
                                                   ┌──────────────┐
                                                   │   OpenCode   │
                                                   │   (AI agent) │
                                                   └──────────────┘
```

两个独立的工具，各自只做一件事：

| 工具 | 角色 | 安装位置 |
|------|------|----------|
| **OpenLoom** | 任务调度器 + webhook 接收/发送 | 公司电脑 |
| **openloom-connector** | 网盘 ↔ webhook 桥接 | 公司电脑（和 OpenLoom 一起跑） |

**OpenLoom 主仓库不动**——所有"接什么存储"的逻辑都在 connector。

---

## 一、OpenLoom 安装与启动（公司电脑上）

### 安装

```bash
pip install openloom[ui]
```

`[ui]` 会装上 FastAPI、uvicorn 这些 web 相关依赖。

### 启动 OpenCode Server

OpenLoom 不带 OpenCode 本身，需要先确保 OpenCode 在跑：

```bash
# OpenCode 默认监听 http://127.0.0.1:4096
# 如果改了端口，通过 OPENLOOM_OPENCODE_URL 告诉 OpenLoom
export OPENLOOM_OPENCODE_URL=http://127.0.0.1:4096
```

### 启动 OpenLoom

```bash
openloom serve
```

启动后看到：

```
openloom serve 0.14.0 (python 3.12)
  opencode    http://127.0.0.1:4096
  store       .openloom/openloom.sqlite3

openloom serve — http://127.0.0.1:55413
  store:    .openloom/openloom.sqlite3
  tasks:    0 pending/running
  sessions: 0 visible
  sources:  generic
```

打开浏览器访问 `http://127.0.0.1:55413` 就能看到 web dashboard。

### 配置（可选）

OpenLoom 默认开箱即用。你**唯一**必须配置的，通常只有：

```bash
# 接 connector（详见第四节）：OpenLoom 推事件给 connector 的地址
export OPENLOOM_NOTIFY_WEBHOOK_URLS='http://127.0.0.1:55414/listener/openloom'

# OpenCode 在非默认端口
export OPENLOOM_OPENCODE_URL='http://127.0.0.1:14096'

# OpenCode 启用了密码
export OPENLOOM_OPENCODE_PASSWORD='xxx'
```

其他全部走默认值。详见 [configuration.md](configuration.md)。

---

## 二、入站 Webhook（外部 → OpenLoom）

### 最简方式：零代码对接

外部系统 POST 一个 JSON 到 OpenLoom 的 webhook 端点即可：

```bash
curl -X POST http://127.0.0.1:55413/api/webhooks/generic \
  -H 'Content-Type: application/json' \
  -d '{
    "goal": "修复登录页面的 CSS 问题",
    "workspace": "/Users/me/my-project"
  }'
```

返回：

```json
{
  "ok": true,
  "taskId": "task_a1b2c3",
  "status": "pending",
  "name": "Webhook task",
  "workspace": "/Users/me/my-project",
  "source": "generic"
}
```

**JSON 字段说明**：

| 字段 | 必填 | 说明 |
|------|------|------|
| `goal` | ✅ | agent 收到的指令，告诉它做什么 |
| `workspace` | ⚠️ | 项目绝对路径；不填就要传 `sessionId` 绑定已有会话 |
| `sessionId` | ⚠️ | OpenCode 会话 ID；与 `workspace` 二选一 |
| `name` | ❌ | 任务显示名，默认 `"Webhook task"` |
| `event` | ❌ | 事件类型标记，仅作为元数据 |
| `metadata` | ❌ | 任意附加数据，写入 check_log 不影响执行 |

**sessionId 是干啥的**：如果你已经在 OpenCode 里开了一个会话（比如之前已经讨论过这个项目），把 sessionId 传进来，新任务就会接着那个会话继续，agent 能看到之前的上下文。在 dashboard 上打开任意一个会话，从 URL 或 sessions 列表里能看到 `ses_xxx` 格式的 ID。

### 查看已注册的 webhook source

```bash
curl http://127.0.0.1:55413/api/webhooks/sources
```

返回：

```json
{"sources": ["generic"]}
```

### 自定义 Source（适配你公司的特殊 payload）

如果你要接的是 GitHub、Slack、或者你们公司内部的某个系统，它们的 payload 格式不是 OpenLoom 能直接用的，那就写一个 parser：

```python
# my_parser.py
from openloom.core.registry import register_source
from openloom.core.webhook_types import SourceParser, WebhookInboundEvent

@register_source("my_company_ci")
class MyCIParser(SourceParser):
    def parse(self, headers, body):
        if body.get("type") != "build_finished":
            return None

        build = body.get("build", {})
        return WebhookInboundEvent(
            source="my_company_ci",
            event_name=f"build.{build.get('status')}",
            name=f"Build #{build.get('id')}",
            workspace=build.get("project_path"),
            session_id=build.get("session_id", ""),
            goal=f"修复构建失败：{build.get('error_log')}",
            metadata={"build_id": build.get("id")},
        )
```

在启动 OpenLoom 之前 import 一下：

```python
# run_loom.py
import my_parser  # 触发 @register_source 装饰器
from openloom.cli import main
main()
```

之后外部系统就可以：

```bash
POST http://127.0.0.1:55413/api/webhooks/my_company_ci
```

---

## 三、出站 Webhook（OpenLoom → 外部系统）

OpenLoom 在任务状态变化时（创建、启动、更新、完成、失败）会主动 POST 到你配置的 URL。

### 通过环境变量配置

```bash
export OPENLOOM_NOTIFY_WEBHOOK_URLS='https://your-system.com/openloom-hook'
export OPENLOOM_NOTIFY_WEBHOOK_EVENTS='TASK_COMPLETED,TASK_FAILED'
export OPENLOOM_NOTIFY_WEBHOOK_SECRET='可选的 HMAC 密钥'
```

也可以写到 `~/.openloom/config.yaml`：

```yaml
notify:
  webhook:
    - url: https://your-system.com/openloom-hook
      events: [TASK_COMPLETED, TASK_FAILED]
      signing_secret: ''
      max_retries: 3
```

环境变量优先于 YAML 文件（12-factor 惯例）。

### OpenLoom 推送的 payload 格式（v1 schema）

```json
{
  "schema_version": "1.0",
  "event": "TASK_COMPLETED",
  "task_id": "task_a1b2c3",
  "task_name": "修复登录 CSS",
  "timestamp": 1718836256.0,
  "timestamp_iso": "2026-06-19T12:30:56Z",
  "store_version": 42,
  "data": {
    "status": "completed",
    "summary": "已修复并通过测试",
    "recent_activity": [
      {
        "text": "我看了一下登录页的样式。",
        "completed_at": 1718836250.0,
        "tools": [{"tool": "bash", "status": "completed", "input_excerpt": "ls"}]
      }
    ]
  }
}
```

5 种 event 类型：

| event | 含义 |
|-------|------|
| `TASK_CREATED` | 任务被创建（webhook 入站或 dashboard） |
| `TASK_STARTED` | harness 已向 OpenCode 发送启动 prompt |
| `TASK_UPDATED` | 每次巡检 tick 都会发（默认 30s 一次） |
| `TASK_COMPLETED` | agent 报告完成 |
| `TASK_FAILED` | harness 检测到失败 |

### 接收方校验签名（Python 示例）

如果 OpenLoom 端配了 `signing_secret`，每个出站请求会带：

```
X-OpenLoom-Signature-256: sha256=<64位hex>
```

接收方校验：

```python
import hmac, hashlib

def verify_openloom(body: bytes, sig_header: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    received = sig_header.removeprefix("sha256=")
    return hmac.compare_digest(received, expected)
```

### 重试策略

如果接收方返回 5xx 或网络失败，OpenLoom 会按 1s → 4s → 16s 的指数退避重试，最多 3 次（可配）。

---

## 四、openloom-connector 安装与配置（关键：手机/网盘 ↔ OpenLoom）

这个工具**单独打包**，装在和 OpenLoom 同一台公司电脑上。仓库：
[`github.com/atbeta/openloom-connector`](https://github.com/atbeta/openloom-connector)。

详细文档以那里为准。下面给出最简集成流程。

### 安装

```bash
pip install openloom-connector
```

### 最小配置

```yaml
# openloom-connector.yaml
connector:
  class: my_company.MyStorage
  kwargs:
    api_url: https://your-storage.com
    token: xxx

paths:
  inbox: /tasks/incoming
  outbox: /tasks/results
```

**就这么多。** 其他全部自动：

- ✅ listener 永远开，地址硬编码为 `http://127.0.0.1:55414/listener/openloom`
- ✅ 不需要写 `outbound_webhook` / `enabled: true` / port / path
- ✅ 不需要 HMAC 签名（localhost 不需要）
- ✅ 不需要填 webhook URL（从 `openloom.url` 派生）

### 启动

```bash
PYTHONPATH=.  # 让 connector 找得到你的 my_company_storage.py
openloom-connector run -c openloom-connector.yaml
```

启动后 banner：

```
connector started — polling every 10s, prefix='task-'
openloom url: http://127.0.0.1:55413
listener:     http://127.0.0.1:55414/listener/openloom (OpenLoom should set OPENLOOM_NOTIFY_WEBHOOK_URLS=http://127.0.0.1:55414/listener/openloom)
inbox:        /tasks/incoming
archive:      (disabled)
```

**复制那行 `OPENLOOM_NOTIFY_WEBHOOK_URLS=...` 到 OpenLoom 端即可。**

### OpenLoom 端配套配置

在 OpenLoom 启动环境里加一行：

```bash
export OPENLOOM_NOTIFY_WEBHOOK_URLS='http://127.0.0.1:55414/listener/openloom'
```

OpenLoom 推事件 → connector 接收 → 写状态文件 / 结果文件 → 网盘同步 → 手机端看到。

### 实现你的 Connector（4 个方法）

外部对接方只需要写一个 Connector 子类，实现 4 个方法：

```python
# my_company_storage.py
import httpx
from openloom_connector import Connector, FileEntry

class MyCompanyStorage(Connector):
    def __init__(self, api_url, token):
        self._api = api_url
        self._h = {"Authorization": f"Bearer {token}"}

    def list_inbox(self):
        r = httpx.get(f"{self._api}/files",
                      params={"dir": self.inbox_dir}, headers=self._h)
        return [FileEntry(path=f["path"], name=f["name"], size=f["size"])
                for f in r.json()["files"]]

    def download(self, path):
        r = httpx.get(f"{self._api}/download",
                      params={"path": path}, headers=self._h)
        return r.content if r.status_code == 200 else None

    def upload(self, path, content):
        httpx.post(f"{self._api}/upload",
                   params={"path": path}, content=content, headers=self._h)

    def delete_inbox(self, path):
        httpx.post(f"{self._api}/delete",
                   params={"path": path}, headers=self._h)
```

详细示例（httpx、requests、本地文件系统）在 connector 仓库的 README。

---

## 五、任务文件格式

### 文件命名

- 必须以 `task-` 开头（可在 connector 配置里改）
- 后缀：`.json` / `.yaml` / `.yml` / `.docx`
- 其他文件全部忽略（同事之间的文档不会被误处理）

### JSON / YAML

**JSON 示例**：

```json
{
  "goal": "修复登录页 CSS",
  "workspace": "/Users/me/project",
  "name": "登录 CSS 修复",
  "sessionId": "ses_abc123"
}
```

**YAML 示例**：

```yaml
goal: 修复登录页 CSS
workspace: /Users/me/project
name: 登录 CSS 修复
sessionId: ses_abc123
```

### docx（公司网盘只能 docx 的情况）

在 Word 里做一个表格（**必须是文档中的第一个表格**），两列：字段名 | 值

| goal | 帮我把 README 翻译成英文 |
|------|------------------------|
| workspace | /Users/me/project |
| name | 翻译 README |
| sessionId | ses_existing_xyz |

字段说明（不区分大小写）：

| 字段名（接受） | 必填 | 说明 |
|----------------|------|------|
| `goal` | ✅ | 任务指令 |
| `workspace` | ⚠️ | 项目绝对路径；与 sessionId 二选一 |
| `sessionId` 或 `session_id` | ⚠️ | OpenCode 会话 ID；与 workspace 二选一 |
| `name` / `title` | ❌ | 任务名（默认用文件名） |
| 任意其他字段 | ❌ | 写入 `metadata`，agent 可见 |

保存为 `task-xxx.docx` 上传到网盘 `/tasks/incoming/` 即可。

### 文件名与归档

- 文件名必须以 `task-` 开头，后缀是 `.json` / `.yaml` / `.yml` / `.docx` 之一
- connector 处理成功后：
  - 原文件移动到 `/tasks/archive/`（如果配了 archive 目录）
  - 结果文件写到 `/tasks/results/`
  - 例：`task-fix-css.docx` → 结果为 `task-fix-css.result.docx`

---

## 六、手机端怎么用

整个流程对手机端来说就是 **"上传文件到网盘 → 等待 → 下载结果文件"**，零代码。

### 1. 创建一个任务

手机上的 Word / WPS / 腾讯文档 创建一个新文档，插入一个两列表格：

| goal | 帮我修复登录页面的 CSS 问题 |
|------|----------------------------|
| workspace | /Users/zhangsan/my-project |

保存为 `task-fix-css.docx`，上传到网盘的 `/tasks/incoming/` 目录。

> 如果你想继续之前在 OpenCode 里已经讨论过的会话，先去公司电脑 dashboard 上看一眼 session ID（格式 `ses_xxx`），在表格里再加一行 `sessionId: ses_xxx`。

### 2. 等

公司电脑上的 connector 每 10s 轮询一次，发现新 docx 就：
1. 读取 docx 里的表格
2. 提取 goal / workspace / sessionId
3. POST 给 OpenLoom
4. OpenLoom 创建任务、OpenCode 执行
5. 期间 OpenLoom 推送 TASK_UPDATED（每 30s），connector 写一个 `task-fix-css.status.json`（轻量状态）让手机能看到实时进度

### 3. 下载结果

任务完成后，OpenLoom 把结果写成一个 docx（同样的两列表格格式）。

`task-fix-css.result.docx` 保存到 `/tasks/results/`，手机下载即可看到。

原来的 `task-fix-css.docx` 已经被 connector 移动到 `/tasks/archive/`（如果配了 archive 目录）。

### 4. 完整流程示意

```
┌──────────────┐                              ┌──────────────────┐
│   手机        │                              │   公司电脑        │
│              │                              │                  │
│ 写 task-fix  │                              │                  │
│  -css.docx   │                              │                  │
│ （表格里写   │                              │                  │
│  goal 等）   │                              │                  │
│      ↓       │                              │                  │
│ 上传到网盘   │   ──网盘同步──→               │ connector 轮询   │
│ /tasks/      │                              │ ↓               │
│   incoming/  │                              │ 读到 docx        │
│              │                              │ ↓               │
│              │                              │ 解析表格         │
│              │                              │ ↓               │
│              │                              │ POST 到 OpenLoom │
│              │                              │ /api/webhooks/   │
│              │                              │   generic        │
│              │                              │ ↓               │
│              │                              │ OpenLoom 创建任务│
│              │                              │ ↓               │
│              │                              │ OpenCode 执行    │
│              │                              │ (5 分钟、几小时) │
│              │                              │ ↓               │
│              │   ←──网盘同步──               │ 实时状态文件     │
│ 看 status    │                              │ /tasks/results/  │
│   .json      │                              │ task-fix-css     │
│              │                              │   .status.json   │
│              │   ←──网盘同步──               │ ↓ (终态时)       │
│ 下载 result  │                              │ 结果 docx         │
│   .docx      │                              │ task-fix-css     │
│              │                              │   .result.docx   │
│              │                              │ ↓               │
│              │                              │ 原文件归档       │
│              │                              │ /tasks/archive/  │
└──────────────┘                              └──────────────────┘
```

---

## 七、YAML 配置参考

完整配置见 [configuration.md](configuration.md)。最常用的环境变量：

| 变量 | 默认 | 说明 |
|------|------|------|
| `OPENLOOM_OPENCODE_URL` | `127.0.0.1:4096` | OpenCode 地址 |
| `OPENLOOM_OPENCODE_USERNAME` | `opencode` | OpenCode 用户名 |
| `OPENLOOM_OPENCODE_PASSWORD` | `""` | OpenCode 密码（不设就不发） |
| `OPENLOOM_DATABASE` | `.openloom/openloom.sqlite3` | 任务存储路径 |
| `OPENLOOM_UI_HOST` | `127.0.0.1` | dashboard 绑定地址 |
| `OPENLOOM_UI_PORT` | `55413` | dashboard 绑定端口 |
| `OPENLOOM_NOTIFY_WEBHOOK_URLS` | `""` | 出站 webhook URL（逗号分隔多个） |
| `OPENLOOM_NOTIFY_WEBHOOK_EVENTS` | `*` | 事件过滤 |
| `OPENLOOM_NOTIFY_WEBHOOK_SECRET` | `""` | HMAC 密钥（可选） |
| `OPENLOOM_NOTIFY_WEBHOOK_MAX_RETRIES` | `3` | 重试次数 |
| `OPENLOOM_NOTIFY_RECENT_MESSAGES` | `3` | recent_activity 条数 |
| `OPENLOOM_IDLE_COMPLETES_TASK` | `true` | idle session 视为完成 |
| `OPENLOOM_AUTO_ACCEPT_PERMISSIONS` | `true` | 自动接受权限弹窗 |
| `OPENLOOM_CHECK_INTERVAL_SECONDS` | `30` | 巡检间隔（10s–3600s 钳制） |

也可以写 `~/.openloom/config.yaml`——env vars 优先于文件。

---

## 八、入门清单

给一个新同事的最小上手步骤：

### 公司电脑上（一次性）

1. 装 OpenCode 并启动（监听 `127.0.0.1:4096` 或你自定义端口）
2. `pip install openloom[ui]`
3. `pip install openloom-connector`
4. 写一个 `my_storage.py`，实现 4 个方法（参考第五节）
5. `openloom-connector init`，填好 YAML
6. `openloom serve`（一个终端，**配 `OPENLOOM_NOTIFY_WEBHOOK_URLS`**）
7. `openloom-connector run -c openloom-connector.yaml`（另一个终端）

### 手机上（每次任务）

1. 打开 Word，插一个两列的表格
2. 第一列填 `goal` / `workspace` / 可选 `sessionId`；第二列填值
3. 保存为 `task-xxx.docx`
4. 上传到网盘 `/tasks/incoming/`
5. 等几分钟到几小时（中间可看 `task-xxx.status.json` 看进度）
6. 从网盘 `/tasks/results/` 下载 `task-xxx.result.docx`

零代码对接目标达成。

---

## 九、常见问题

**Q：手机在公司外面能访问吗？**
A：只要网盘客户端能联网就行。手机不需要访问公司电脑。

**Q：任务执行多久能完成？**
A：取决于 agent 和任务复杂度。简单的几秒，复杂的几小时。OpenLoom 默认每 30 秒检查一次。

**Q：任务失败了怎么办？**
A：结果 docx 里 `status` 字段是 `failed`，`data.error` 字段有错误信息。可以改 docx 表格重新上传再试。

**Q：可以同时跑多个任务吗？**
A：可以。每个 docx 文件独立处理。

**Q：怎么知道任务当前进度？**
A：两个方式：(1) 实时看 `task-x.status.json`（每 30s 更新一次，状态变化立即更新）；(2) 登录公司电脑看 `http://127.0.0.1:55413` dashboard；(3) 订阅 OpenLoom 的出站 webhook（`TASK_UPDATED` 事件每 30s 发一次）。

**Q：OpenLoom 和 connector 必须装同一台电脑吗？**
A：是的。它们通过 HTTP 通信。如果 OpenLoom 在 A 机、connector 在 B 机，把 connector 配置里 `openloom.url` 改成 A 机地址即可。

**Q：sessionId 怎么获取？**
A：在公司电脑的 dashboard 上打开 OpenCode sessions 列表，每个会话的 ID 就是 `ses_xxx`。把这个 ID 复制到 docx 表格里。

**Q：手机能直接创建 docx 表格吗？**
A：可以。Word / WPS / 腾讯文档 / 金山文档都支持。插一个两列表格填字段就行。

**Q：归档目录可以省略吗？**
A：可以。如果 `paths.archive` 不填或为空，原文件会被直接删除。建议保留归档，方便排查问题。

**Q：任务完成后我把那个 task-xxx.docx 又上传了一次会怎样？**
A：connector 不知道是同一个任务，会创建新的 task_id 重新推给 OpenLoom。这相当于"重做"。如果你想"再跑一次同样的任务"，重命名一下文件（如 `task-xxx-v2.docx`）。

**Q：为什么 OpenLoom 默认 idle_completes_task=true？**
A：因为 bootstrap framing 让 agent 大概率主动写 "TASK COMPLETE"，但偶尔它会跑完工具调用忘了标——idle 兜底避免任务永远卡在 running。配合 auto_accept_permissions=true，远程用户基本不用手动干预。

**Q：为什么 connector 默认装成 0.3.0 listener 永远 on？**
A：因为 connector 不开 listener 就完不成闭环（OpenLoom 推事件过来没接收方）。硬编码 URL 让用户不用自己拼。

---

## 十、架构边界

为什么 webhook 在 OpenLoom 里、而 connector 是独立的包？

- **OpenLoom** 只负责：webhook 收发 + 任务执行 + 状态通知。它不关心你的存储是什么。
- **openloom-connector** 负责：把任意存储适配成 webhook。它不关心 OpenLoom 内部。

这两个东西通过 HTTP 解耦，可以独立升级、独立打包。OpenLoom 保持"观测面 + 极简无人值守"的定位，connector 单独迭代网盘适配能力。

要让你们的工具对接 OpenLoom，**只需要写一个 ~30 行的 Connector 子类**即可。

### 双侧发布节奏

- OpenLoom 主仓库：PyPI（`pip install openloom[ui]`），按版本号发版
- openloom-connector：GitHub 直装（`uv tool install git+https://github.com/atbeta/openloom-connector.git`），随用随升级

两者通过 webhook API 解耦——一方升级不影响另一方。
