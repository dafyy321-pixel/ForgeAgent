# ForgeAgent

基于 PostgreSQL 的持久 Agent Runtime，配套 React 任务控制台。任务、审批、动作、预算、检查点和验收证据保存于服务端；浏览器仅保存草稿、收藏和可选登录会话。

当前实现与证据边界见 [当前能力矩阵](docs/capabilities.md)，最新修改与验证见 [二轮实施记录](docs/ForgeAgent-二轮优化实施记录-2026-10-10.md)，此前结果见 [上一轮记录](docs/ForgeAgent-面试建议实施记录.md)。这是一套可运行的开发实现，尚未通过完整方案要求的生产、恶意多租户与外部 benchmark 验收。

## 导航

- [项目能力](#项目能力)
- [系统架构](#系统架构)
- [本地启动](#本地启动)
- [模型与沙箱](#模型与沙箱)
- [配置参考](#配置参考)
- [登记真实项目](#登记真实项目)
- [任务控制与副作用处理](#任务控制与副作用处理)
- [HTTP API 与 Python SDK](#http-api-与-python-sdk)
- [评测与治理](#评测与治理)
- [检查](#检查)
- [共享部署](#共享部署)
- [常见问题](#常见问题)
- [已知限制与后续工作](#已知限制与后续工作)
- [结构与依据](#结构与依据)

## 项目能力

ForgeAgent 面向需要持续运行、可追踪、可恢复的软件工程 Agent 任务。核心目标是让每次决策、每个动作及其验收结果都有可查询的持久记录。

| 领域 | 当前能力 |
| --- | --- |
| 任务工作空间 | 创建任务、项目筛选、执行时间线、待处理事项、上下文、产物与验收页面 |
| 持久执行 | PostgreSQL 调度、租约与心跳、epoch fencing、检查点、暂停/恢复/取消和新尝试 |
| 动作治理 | 意图先落库、稳定动作 ID、执行尝试、原始观察、审批与未知效果对账 |
| 费用与资源 | root 费用/Token/CPU/存储/墙钟额度、子任务分配与结算、未知账单门禁 |
| 工程工作区 | 固定 Git commit/worktree、文本和二进制文件语义、范围读写/搜索/补丁/移动/删除 |
| 独立验收 | 固定验收契约、测试保护、只读 Docker 验证、摘要绑定的证据与交付物 |
| 模型与上下文 | OpenAI/Anthropic 适配、结构化决策校验、约束优先编译、full/elide 策略与原始观察 recall |
| 知识治理 | 项目记忆、来源与有效期、候选技能、不可覆盖的技能版本和配对评测发布门禁 |
| 外部连接 | 版本化 MCP/A2A 接入、任务句柄、状态查询、取消和显式输入响应 |
| 运维接口 | OpenAPI、SSE、Python SDK、CLI、Prometheus、可选 OTel、本地孤儿对象回收 |

这里的“恢复”指从持久状态恢复合法执行，不是恢复进程内存；“成功”指固定验收通过且效果已结算，不是模型自行宣称完成。远端效果不确定时会暂停或等待对账，不承诺任意外部服务的 exactly-once。

## 系统架构

```mermaid
flowchart TD
    Console[React Console] --> API[FastAPI / HTTP / SSE]
    Clients[Python SDK / CLI] --> API
    API <--> DB[(PostgreSQL)]
    Worker[Durable Worker] <--> DB
    Worker --> Context[Context Compiler]
    Context --> Model[Model Adapter]
    Worker --> Tools[Tool and Remote Adapters]
    Tools --> Sandbox[Docker Sandbox]
    Tools --> Remote[MCP / A2A Services]
    Worker --> Verify[Independent Verifier]
    Verify --> Sandbox
    API <--> Objects[(Local Objects / S3)]
    Worker <--> Objects
```

PostgreSQL 是业务事实源，保存任务、动作、预算、授权、事件和引用；对象存储保存按摘要校验的工作区、原始响应和验收证据。API 与 Worker 是独立进程。浏览器关闭后，Worker 仍可继续执行。

一次任务的主要步骤：

1. API 固定任务目标、路径、能力、预算、项目基线与验收契约。
2. Worker 获取租约，核对当前权限与执行版本，编译上下文并预留调用费用。
3. 模型给出工具调用、子任务、补充输入或提议完成等结构化决策。
4. 工具动作先保存意图，满足权限和审批条件后执行，再保存回执与工作区快照。
5. 独立验收生成 `changes.patch`、`verification.json` 和 `summary.md`；证据绑定具体产物版本。

详细的不变量和状态设计见 [完整优化方案](docs/ForgeAgent-完整优化方案.md)，实现位置和缺口见 [后端实施与验收](docs/后端实施与验收.md)。

## 本地启动

需要 Node.js 22+、[uv](https://docs.astral.sh/uv/)。模型生成代码的执行与验收还需要可运行 Linux 容器的 Docker。

| 依赖 | 要求 | 用途 |
| --- | --- | --- |
| Node.js | 22+ | 前端、开发服务和本地 PG 启动脚本 |
| Python | 3.12+，建议 3.12 | API、Worker、SDK/CLI；可由 uv 管理 |
| uv | 当前可用版本 | 根据 `uv.lock` 同步 Python 依赖 |
| PostgreSQL | Compose 使用 17.6，本地二进制使用 18.4 | 真实持久存储；没有 SQLite 替代模式 |
| Docker | 支持 Linux 容器 | 真实生成代码执行和独立验收；固定契约示例不需要 |
| Chromium | Playwright 安装或指定现有路径 | 浏览器测试 |

首次下载并启动：

```sh
git clone https://github.com/dafyy321-pixel/ForgeAgent.git
cd ForgeAgent
npm ci
npm run start:local
```

启动脚本同步 Python 依赖、启动独立 PostgreSQL、执行迁移并启动 API、Worker 和前端。默认访问 <http://127.0.0.1:5173/>，若前端端口被占用会选择下一个空闲端口。API 文档：<http://127.0.0.1:8000/docs>。

也可访问 [OpenAPI JSON](http://127.0.0.1:8000/openapi.json) 和 [数据库就绪检查](http://127.0.0.1:8000/health/ready)。首次启动后点击“运行契约示例”，确认出现“已验证完成”，再检查动作账本及三个交付物，即可验证不依赖付费模型的完整链路。

本地脚本面向默认回环开发配置。使用远程数据库、不同 API 端口或自定义进程管理时，使用下面的分步启动方式。若前端自动选择了 5175 或更高端口，应将对应地址加入 `FORGE_TRUSTED_ORIGINS` 并重启 API。Linux/macOS 请使用普通用户运行本地 PostgreSQL，不以 root 用户执行初始化。

进程 PID 在 `.forge/local-processes.json`，日志在 `.forge/logs/`。脚本不会覆盖已占用的 API 端口；停止服务时仅停止记录的本项目进程。Windows Python 启动器可能还有子进程，应同时检查监听 8000 的进程。数据库数据保存在 `.forge/postgres/`，不要删除它来重启服务。

也可分别启动，后续命令在不同终端运行：

先在项目根目录执行 `npm ci` 和 `uv sync --frozen`。随后启动数据库，待它打印 ready 后，在另一终端执行迁移；最后分别运行 API、Worker 和前端。

```sh
uv sync --frozen
npm run db:local
npm run db:migrate
npm run api
npm run worker
npm run dev -- --port 5173
```

`db:local` 使用真正的 PostgreSQL 18.4 本地二进制。Docker 可用时可替换为 `docker compose up -d postgres`，使用 PostgreSQL 17.6；二者占用同一 55432 端口，不要同时启动。应用账号 `forge` 不具有 superuser/BYPASSRLS 权限。Compose 和本地脚本中的口令仅用于回环开发环境。

`compose.yaml` 当前仅编排数据库，不会自动启动 API、Worker、前端或构建沙箱。Docker Compose 的数据库使用命名卷，本地二进制使用 `.forge/postgres/`；二者不是同一份数据目录，不要直接互换数据文件。

升级依赖或代码后，停止本项目 API/Worker，执行 `npm ci`、`uv sync --frozen`、`npm run db:migrate` 后再启动。任务固定执行实现摘要，旧任务可能需要通过 fork 创建新尝试。数据库迁移不提供自动破坏性降级。

## 模型与沙箱

参考 `.env.example` 创建本地 `.env`，配置以下项目后重启 API 和 Worker：

PowerShell 使用 `Copy-Item .env.example .env`，bash/zsh 使用 `cp .env.example .env`。只在 `.env` 尚不存在时复制，避免覆盖已有配置；`.env` 已被 Git 忽略。

```dotenv
FORGE_MODEL_PROVIDER=openai
FORGE_MODEL_ID=<精确模型ID>
FORGE_MODEL_API_KEY=<密钥>
FORGE_INPUT_PRICE=<每百万输入token的美元价格>
FORGE_OUTPUT_PRICE=<每百万输出token的美元价格>
FORGE_SANDBOX_IMAGE=forgeagent-sandbox:local
```

`FORGE_MODEL_PROVIDER` 支持 `openai`、`anthropic`。价格必须与实际提供方匹配；返回的 usage 不能确定时保留预算预留并暂停，提供账单证据后才能恢复。模型、工具和代码语义固定到任务；更换模型需在动作账本中显式绑定，修改 Runtime 实现后应从检查点创建新尝试。

OpenAI 默认使用 Responses/native 工具模式，也支持按能力选择 Chat Completions、结构化输出或 JSON；Anthropic 使用 Messages/native。所有决策均经 Pydantic 校验。选择支持当前请求参数的精确模型 ID。没有内置“最新模型”别名、自动切换强模型或跨提供方 fallback，也不把兼容端点视为已经通过契约验证。

```sh
npm run sandbox:build
```

默认沙箱使用无网络、非 root、只读根文件系统的 Docker 容器。依赖应在受控镜像中预装。宿主不会执行模型生成的 Python 或 shell 命令。开发用 Docker 配置不等于完成恶意多租户隔离认证。

不配置模型或 Docker 时，可以点击“运行契约示例”。它使用明确标识的固定决策器与 AST 验收，验证真实数据库、Worker、文件快照、产物与证据流程，不调用外部模型，也不代表模型能力评测。

## 配置参考

API 与 Worker 从项目根目录的 `.env` 和进程环境读取配置，进程环境优先。修改配置后需要重启相关进程；不要把密钥写进任务目标、记忆或技能正文。

| 变量 | 默认值 / 示例 | 含义 |
| --- | --- | --- |
| `FORGE_DATABASE_URL` | `postgresql+psycopg://forge:forge-local@127.0.0.1:55432/forge` | 数据库连接 |
| `FORGE_DATA_DIR` | `.forge` | 本地对象和工作区根目录 |
| `FORGE_AUTH_MODE` | `local` | `local` 或 `oidc` |
| `FORGE_TRUSTED_ORIGINS` | 本机 5173/5174 等地址 | 逗号分隔的浏览器来源白名单 |
| `FORGE_MODEL_PROVIDER` | `unconfigured` | `openai` 或 `anthropic` |
| `FORGE_MODEL_ID` | 空 | 精确模型标识 |
| `FORGE_MODEL_API_KEY` | 空 | 仅服务端使用的密钥 |
| `FORGE_MODEL_BASE_URL` | 空 | 空值使用 SDK 默认地址；可显式指定端点 |
| `FORGE_INPUT_PRICE` / `FORGE_OUTPUT_PRICE` | `0` | 每百万 Token 的美元价格，真实调用要求大于 0 |
| `FORGE_CONTEXT_WINDOW` / `FORGE_MAX_OUTPUT` | `32768` / `4096` | 上下文窗口和最大输出预算 |
| `FORGE_SANDBOX_IMAGE` | `forgeagent-sandbox:local` | 执行镜像，运行时绑定内容摘要 |
| `FORGE_SANDBOX_RUNTIME` | `runc` | Docker runtime；使用其他 runtime 需先自行安装验证 |
| `FORGE_LEASE_SECONDS` | `30` | Worker 租约时长 |
| `FORGE_WORKER_TENANTS` | 空 | 逗号分隔的租户列表；本机模式默认只处理 `local` |
| `FORGE_MAX_OBJECT_BYTES` | `16777216` | 单对象字节上限；文本快照内容使用该值的一半 |
| `FORGE_REMOTE_HOSTS` | 空 | 允许 MCP/A2A 访问的 HTTPS 主机名，不含路径 |
| `FORGE_CALLBACK_SECRET` | 空 | 回调签名验证密钥 |
| `FORGE_S3_ENDPOINT` / `FORGE_S3_BUCKET` | 空 / `forgeagent` | 设置 endpoint 后使用 S3，桶需预先建立 |
| `FORGE_S3_ACCESS_KEY` / `FORGE_S3_SECRET_KEY` | 空 | S3 访问凭证 |
| `FORGE_OIDC_ISSUER` / `FORGE_OIDC_AUDIENCE` / `FORGE_OIDC_JWKS_URL` | 空 / `forgeagent` / 空 | OIDC 验证配置 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | 空 | 可选 OTLP HTTP 导出地址 |

CLI 还读取 `FORGE_API_URL`（默认 `http://127.0.0.1:8000`）和可选 `FORGE_ACCESS_TOKEN`。这两个变量应设置在调用 CLI 的进程环境中，CLI 不负责从 `.env` 加载它们。

前端通过 Vite 将 `/v1`、`/health` 代理到 8000；`npm run build` 只产生静态文件。正式托管 `dist/` 时，需要由反向代理配置 API 转发和 SPA 路由回退。

## 登记真实项目

推荐使用 [Git 接入向导与 CLI 预检](docs/project-onboarding.md)。CLI 采用独立流式 bundle 上传；下列小型内联 JSON 仍受 2 MiB 请求限制。

设置页可登记文本文件基线和验收契约。CLI 支持从本地目录登记；不会自动下载任意仓库 URL。基线不可覆盖，更新使用新项目 ID。

```sh
uv run forge project register my-project-v1 ./example ./acceptance.json
uv run forge run ./task.json --key my-request-key
uv run forge status <run-id>
uv run forge events <run-id>
uv run forge replay <run-id>
uv run forge artifacts <run-id>
```

`acceptance.json` 示例：

```json
{"id":"answer@1","argv":["python","-m","unittest","discover","-s","hidden_tests"],"protected_tests":{"hidden_tests/test_answer.py":"import unittest\nfrom src.example import answer\nclass Contract(unittest.TestCase):\n    def test_answer(self):\n        self.assertEqual(answer(),42)\n"}}
```

`task.json` 示例：

```json
{
  "project_id": "my-project-v1",
  "title": "修复 answer",
  "task": {
    "goal": "使 answer 返回 42",
    "allowed_paths": ["src"],
    "acceptance_profile": "answer@1"
  },
  "budget": {
    "max_cost_usd": "1.00",
    "max_tokens": 500000,
    "max_turns": 100,
    "max_tool_calls": 300,
    "max_wall_seconds": 7200
  },
  "capabilities": ["repo.read", "workspace.write", "tests.run"],
  "model": "configured",
  "harness": {
    "context_policy": "elide",
    "memory": true,
    "observation_recall": true
  }
}
```

当前工作区提供器支持 UTF-8 文本文件，不支持二进制、符号链接、子模块或完整 Git worktree。默认总快照内容上限 8 MiB，单对象上限 16 MiB。

登记目录应只包含任务所需的代码，示例至少包含 `src/example.py`。受保护测试由验收契约单独提供，避免把隐藏测试混入生成模型的工作区。不要直接登记包含凭证、生产数据或大型依赖目录的仓库根目录。CLI 登记也受 API 的 2 MiB 请求体上限约束，因此实际可上传基线可能小于快照上限。

默认不授予 `external.write` 和 `delegate`。需要远端写入或子任务时显式声明对应 capability；子任务仍受父任务的项目、路径、能力、深度与 root 预算约束。

## 任务控制与副作用处理

| 状态 | 含义 | 常见下一步 |
| --- | --- | --- |
| `QUEUED` | 等待 Worker 领取 | 确认 Worker 正常运行 |
| `ACTIVE` | 正在决策、执行或验证 | 查看时间线、动作和上下文 |
| `WAITING` | 等待审批、子任务、远端结果、重试或对账 | 根据 `waitReason` 处理 |
| `PAUSED` | 用户暂停或执行条件不满足 | 补充输入、修复配置、对账或 fork |
| `CANCELLING` | 已停止安排新动作，正在结算在途工作 | 等待或核查未知效果 |
| `SUCCEEDED` | 固定验收通过 | 下载补丁和证据 |
| `FAILED` | 当前尝试失败 | 查看验证结果，必要时创建新尝试 |
| `CANCELLED` | 在途工作已处理，任务取消 | 记录保留用于审计 |

```sh
uv run forge pause <run-id>
uv run forge resume <run-id>
uv run forge cancel <run-id>
uv run forge fork <run-id>
uv run forge action inspect <action-id>
```

`resume` 继续当前尝试；`fork` 基于已有工作区或检查点创建新的尝试，绑定当前执行实现。终态不会被改回运行态。`replay` 仅重建审计投影，不重新调用模型或外部工具。

审批绑定动作参数摘要、工作区版本和当前权限。权限撤销、参数改变或审批超时后，需要新审查。对未知动作必须提供证据；例如：

```sh
uv run forge action reconcile <action-id> occurred "Provider receipt #123 confirms the operation"
```

`occurred` 表示已确认整个操作发生，`absent` 表示已确认未发生。不能仅凭网络超时选择 `absent`。远程输入是否送达与整个远程任务是否完成是不同问题，控制台提供单独的“核对输入投递”入口。模型账单不确定时也有独立预算对账入口。

## HTTP API 与 Python SDK

接口以运行中的 [OpenAPI 文档](http://127.0.0.1:8000/docs) 为准，业务路径以 `/v1` 开头。下面列出主要入口；`{id}` 为具体资源 ID。

| 方法与路径 | 用途 |
| --- | --- |
| `GET /health/live`、`GET /health/ready` | 进程存活、数据库连接检查 |
| `GET /v1/workspace` | 控制台工作空间投影 |
| `GET/POST /v1/runs` | 查询和创建任务 |
| `GET /v1/runs/{id}` | 任务当前状态与版本 |
| `POST /v1/runs/{id}/pause`、`resume`、`cancel`、`fork` | 控制任务或创建新尝试 |
| `GET /v1/runs/{id}/events` | SSE 事件流，支持 `Last-Event-ID` |
| `GET /v1/runs/{id}/actions`、`checkpoints`、`replay` | 动作、检查点与审计 |
| `GET /v1/runs/{id}/context/{turn}` | 实际上下文和省略清单 |
| `GET /v1/runs/{id}/budget` | root 预算与资源账本 |
| `GET /v1/runs/{id}/artifacts` | 交付物列表 |
| `GET /v1/artifacts/{artifact_id}/download` | 下载单个交付物 |
| `POST /v1/approvals/{id}/decision` | 批准或拒绝具体动作 |
| `POST /v1/actions/{action_id}/reconcile` | 整个外部动作对账 |
| `POST /v1/outbox/{id}/reconcile` | 远程输入投递对账 |
| `GET/POST /v1/projects`、`/v1/connections` | 项目与外部连接登记 |
| `POST /v1/memories`、`DELETE /v1/memories/{id}` | 记忆新增与停止后续召回 |
| `POST /v1/skills`、`/v1/skills/{id}/release` | 技能版本登记与发布/停用 |
| `GET/POST /v1/evaluation-datasets` | 版本化任务集 |
| `POST /v1/experiments`、`GET /v1/evaluations/{id}` | 配对实验与结果报告 |
| `GET /v1/operations/health` | 运维待处理项与 runbook |
| `POST /v1/operations/objects/gc` | 本地孤儿对象预览或回收 |
| `GET /metrics`、`GET /v1/metrics` | Prometheus 文本与 JSON 指标 |

创建任务和 fork 等操作需要 `Idempotency-Key`；相同 key 和相同请求返回已有结果，参数冲突会拒绝。任务控制操作通常需要最新 `expected_version`，可从任务返回值的 `stateVersion` 获取。出现 `VERSION_CONFLICT` 时先刷新状态，再决定是否重试，不应自动覆盖他人的决定。

PowerShell 中创建不调用外部模型的契约示例：

```powershell
$headers = @{ 'Idempotency-Key' = [guid]::NewGuid().ToString() }
$run = Invoke-RestMethod -Method Post `
  -Uri 'http://127.0.0.1:8000/v1/examples/smoke' -Headers $headers
Invoke-RestMethod -Uri "http://127.0.0.1:8000/v1/runs/$($run.id)"
```

Python SDK 示例，在已执行 `uv sync` 的项目环境中运行：

```python
import asyncio
import uuid

from forgeagent.sdk import Client, ForgeError


async def main():
    async with Client("http://127.0.0.1:8000") as client:
        try:
            run = await client.request(
                "POST", "/examples/smoke", key=str(uuid.uuid4())
            )
            async for event in client.events(run["id"]):
                print(event["seq"], event["type"])
            print(await client.status(run["id"]))
        except ForgeError as error:
            print(error.status, error.payload)


asyncio.run(main())
```

SDK 的 `events` 不自动断线重连；消费方保存最后的事件序号后，使用 `after_seq` 重新订阅。它在终态且所有事件发送完成后结束；暂停任务仍可能保持连接。共享部署时向 `Client` 传入 `token`。服务端错误包含 `code`、`message`、`retryable` 和关联 ID 等字段，验证错误不会回显完整请求正文。

## 评测与治理

评测页提供固定契约评测、版本化任务集登记、配对实验、报告下载。配对实验固定同一模型与验收器，可以比较 full/elide 上下文、记忆注入、观察 recall 和技能组合。报告按任务聚类，不将重复运行视为独立样本。

技能先登记为 candidate，只有显式实验可加载。发布需提供 held-out 配对证据、至少 20 个任务/两个项目/每配置三次运行，以及预先登记的非劣与成本门禁。固定 fixture 不能用于发布。来源、许可和人工审核记录均保留。

可以先用以下小任务集验证实验链路。`dataset.json`：

```json
{
  "id": "addition-cases@1",
  "split": "development",
  "source": "Runtime Lab contract",
  "cases": [{
    "id": "addition",
    "project_id": "runtime-lab",
    "task": {"goal": "Fix addition", "allowed_paths": ["src"]},
    "budget": {"max_cost_usd": "0.10"}
  }]
}
```

`experiment.json`：

```json
{
  "dataset_id": "addition-cases@1",
  "model": "fixture",
  "configurations": [
    {"name": "baseline", "harness": {"context_policy": "full"}},
    {"name": "elision", "harness": {"context_policy": "elide"}}
  ],
  "repetitions": 3,
  "seed": 42,
  "max_total_cost_usd": "0.60",
  "noninferiority_margin": 0.02,
  "max_cost_ratio": 1.0
}
```

```sh
uv run forge eval dataset ./dataset.json
uv run forge eval experiment ./experiment.json
uv run forge eval report <evaluation-id>
uv run forge admin health
uv run forge admin gc
```

这里有 1 个独立 case、2 个配置、每配置 3 次运行；总任务预算上限为 `0.10 × 2 × 3 = 0.60` 美元，fixture 实际模型费用为 0。重复次数不增加独立 case 数量，因此该实验不能证明模型能力或作为技能发布证据。

真实评测使用 `model: configured`、固定项目版本和保留任务集。预期暂停或拒绝的 case 必须预先登记原因代码。配对报告先合并同一 case 的重复，再进行任务聚类 bootstrap；报告保留所有尝试费用、成功率、正确处置率、区间与时延。未结算账单不会被当作零费用。

项目记忆在新任务创建时按租户、项目和有效期筛选，作为非可信知识进入上下文，不能提升执行权限。删除记忆会停止后续召回，但历史任务快照仍保留；当前没有血缘级彻底删除功能。

## 检查

```sh
npm run check
npm run test:backend
npm run build
npx playwright install chromium
npm run test:ui
```

后端测试使用真实 PG。浏览器测试需要已启动的 PG、API、Worker；Playwright 会启动或复用前端。可以用 `FORGE_BROWSER_PATH` 指定本地 Chromium。可选 Docker 集成测试需设置 `FORGE_TEST_DOCKER=1`。CI 定义见 `.github/workflows/runtime.yml`，本地验证不代表 CI 已在远端运行。

Windows 上指定已有浏览器：

```powershell
$env:FORGE_BROWSER_PATH = 'C:/path/to/chrome.exe'
npm run test:ui
```

Docker 可用后执行真实容器测试：

```powershell
npm run sandbox:build
$env:FORGE_TEST_DOCKER = '1'
npm run test:backend
```

2026-09-24 的本地实现验收记录为 **75 项后端测试通过、3 项 Docker 测试跳过、5 项浏览器测试通过**，另有两条第三方依赖弃用警告。浏览器测试包含 1440/720/390 像素宽度。该记录对应当时环境，不是对任意环境或未来版本的保证。

测试覆盖幂等创建、并发预算预留、租约 fencing、租户隔离、事件不可变、未知效果、远程输入崩溃恢复、验收证据、上下文预算、技能门禁、对象回收与前端主要流程。测试输出位于 `test-results/`，Playwright HTML 报告位于 `playwright-report/`；这些目录不会提交到仓库。

完整静态检查可运行 `uv run ruff check backend tests/backend migrations`。CI 会启动 PostgreSQL 17.6、构建沙箱镜像、执行后端与前端测试并保存浏览器报告。CI 状态以 GitHub Actions 中具体 commit 的运行结果为准。

## 共享部署

将 `FORGE_AUTH_MODE` 设为 `oidc`，配置 issuer、audience、JWKS URL，由可信 IdP 签发 `tenant_id`、`sub` 和 `roles`（管理员为 `forge-admin`）。通过本机管理命令 `uv run forge admin provision <tenant> <subject>` 登记身份。必须配置 HTTPS 反向代理、数据库非特权账号、独立凭证与隔离 Worker。

本地认证只能绑定回环接口，不能直接暴露公网。对象存储默认本地，支持 S3 配置；生产备份、保留策略、PITR、密钥轮换和 gVisor 部署尚需按验收文档完成验证。

根任务与子任务共享费用、Token 预留和工具次数额度；子任务另受自身费用上限限制。CPU/存储/墙钟已按 root 预扣与结算，并保守处理崩溃。GC 按引用图与保留类别处理本地及 S3 对象；真实 S3 与容器计量仍待联调。

共享环境配置还需注意：

- API、Worker 和迁移使用相同数据库与明确的版本组合；普通应用账号不使用 superuser/BYPASSRLS。
- 多 Worker 的并发领取由 PG 协调；单 Worker 使用有界异步槽、租户公平调度与跨 Worker 提供方准入；实际吞吐须测量。
- 本地对象和工作区需要部署侧保证路径一致、访问权限与存储可用性；仅开启 S3 不代表整个运行环境已无状态化。
- 网络默认关闭；MCP/A2A 端点需管理员登记允许的 HTTPS 主机，并逐个批准外部写入。
- `GET /metrics` 也受身份认证保护；采集器需要符合部署的认证方式。原始模型内容不应直接进入普通遥测。
- 备份应同时覆盖数据库和被引用的对象，并实际演练恢复；不能仅凭检查点宣称已有灾备。

本地 GC 最短宽限期是 24 小时，`forge admin gc` 只预览，显式 `--apply` 才回收。它依据引用图、孤儿宽限和财务/未知效果门禁回收；S3 支持版本/marker 清理，完整擦除须走血缘流程，保留无正文财务审计。

## 常见问题

| 现象 / 错误 | 排查与处理 |
| --- | --- |
| 前端提示后端连接失败 | 确认 API 8000 和 PG 55432 正常；检查 `.forge/logs/api.log` 和 `/health/ready` |
| 任务一直处于 QUEUED | 确认 Worker 已启动、数据库可连接、租户配置正确；检查 `.forge/logs/worker.log` |
| `MODEL_NOT_CONFIGURED` | 配置提供方、精确模型 ID 与密钥，重启 API/Worker，必要时显式绑定模型 |
| `PRICING_REQUIRED` | 配置大于 0 的实际输入/输出价格；不要填虚假零价格跳过预算 |
| `SANDBOX_UNAVAILABLE` | 确认 Docker daemon、Linux 容器和指定镜像可用；Windows 检查 WSL2 |
| `MODEL_BINDING_CHANGED` | 配置与任务固定的模型 profile 不一致；通过动作账本显式绑定当前配置 |
| `SEMANTIC_DRIFT` | 代码或工具版本变化；从已有工作区创建新尝试，不直接篡改旧 manifest |
| `VERSION_CONFLICT` | 任务已被 Worker 或其他用户更新；刷新后重新审查操作 |
| `BUDGET_LIMIT` / `TOKEN_BUDGET_LIMIT` | 查询 root 已花费和预留额度，先核对未知账单；不得直接释放不明预留 |
| UNKNOWN 动作或投递 | 从提供方查询真实结果，再提交有证据的对账；不要盲目重发 |
| `HOST_DENIED` / `ORIGIN_DENIED` | 本机模式使用 localhost/127.0.0.1 和已允许端口；共享访问应配置 OIDC |
| 端口 55432 被占用 | 不要同时启动本地 PG 与 Compose PG；先确认现有服务的归属和数据位置 |
| 端口 8000 被占用 | 启动脚本会停止继续启动；复用现有服务，或核实 PID 后停止本项目旧进程 |
| `BODY_LIMIT` / `WORKSPACE_LIMIT` | 减小登记目录，排除依赖、二进制与无关文件；核对请求和快照的不同上限 |
| 浏览器测试无法启动 Chromium | 安装 Playwright Chromium，或通过 `FORGE_BROWSER_PATH` 指定可用程序 |
| 启用技能被 `SKILL_GATE` 拒绝 | 提供满足门槛的 held-out 配对实验及候选配置，固定样例不具备发布资格 |

先运行 `uv run forge admin health` 获取当前租户的待处理资源及处理说明。排障时保留关联 ID、run ID、action ID 和错误代码，不把密钥或完整生产数据附到公开 Issue。

## 已知限制与后续工作

当前版本实现了核心运行链路，但没有完成设计方案中的全部生产与研究目标。以下内容不能由已有测试结果推断为已完成：

- 真实模型质量、成本和性能评测，以及 SWE-bench、Terminal-Bench、SkillsBench 官方基准适配。
- MCP 2026-07-28/Tasks 和 A2A 1.0 的真实服务互操作、完整认证与能力协商。新协议适配为实验状态，反向 sampling/工具执行请求不会自动放行。
- 项目 ACL、独立 gVisor manager 与凭证代理已有机制验证；恶意多租户隔离及真实部署/轮换演练待环境。
- 已实现 root 资源结算、引用图/血缘删除和联合备份机制；真实 S3、PITR 演练与生产 SLO 仍待验收。
- Git worktree 和二进制语义已有本地验证；子模块须单独登记，自动仓库下载和生产发布仍不开放。
- 当前摘要、规划检查清单和规则候选提炼是基线；技能灰度、局部动作融合和消融开关已实现，真实效果与成本收益待测。

后续验证以 [当前能力矩阵](docs/capabilities.md) 和 [真实验收门禁](docs/research-acceptance.md) 逐项补齐，验证结果应绑定具体版本和环境。不要将架构文档中的目标指标、样本量建议或论文结果当成本项目实测结果。

## 结构与依据

```text
ForgeAgent/
  backend/forgeagent/
    api.py              HTTP API、SSE 与控制台接口
    domain.py           请求、决策、预算等契约
    db.py               持久模型、事务、事件与 fencing
    service.py          任务创建、控制、预算、审批与检查点
    worker.py           持久执行、恢复、子任务与验收调度
    models.py           模型提供方适配
    context.py          上下文编译与省略清单
    sandbox.py          文件边界和 Docker 执行
    verification.py     独立验收与证据绑定
    remote.py           MCP/A2A、远程任务与回调
    evaluations.py      任务集、配对统计与技能门禁
    storage.py          本地/S3 对象存储
    auth.py             本机/OIDC 身份
    operations.py       运维诊断
    maintenance.py      本地对象回收
    sdk.py / cli.py     Python 客户端与命令行
  src/                  React 控制台
  migrations/           Alembic 迁移
  tests/backend/        PG 契约、故障与可选容器测试
  tests/frontend/       Playwright 测试
  scripts/              启动、PG 和图标导出脚本
  sandbox/Dockerfile    默认执行镜像
  infra/init.sql        开发数据库账号初始化
  public/               图标、预览与第三方许可
  docs/                 设计、资料与验收矩阵
  .github/workflows/    GitHub Actions
  compose.yaml          本地 PostgreSQL 编排
  .env.example          环境配置模板
  pyproject.toml        Python 工程与命令入口
  uv.lock               Python 依赖锁
  package-lock.json     Node.js 依赖锁
```

`.forge/`、`.venv/`、`node_modules/`、本地 `.env`、构建目录和测试报告均不进入版本管理。业务数据库与工作区不会随着 Git 推送同步到 GitHub。

- `backend/forgeagent/`：API、领域状态、持久 Worker、工具、沙箱、模型、上下文、评测、SDK/CLI。
- `migrations/`：Alembic 迁移、租户 RLS、动作不可变与终态约束。
- `src/`：原控制台及新增的动作账本、预算对账、环境诊断、项目/任务集登记。
- `tests/backend/`、`tests/frontend/`：契约与浏览器测试。
- [完整优化方案](docs/ForgeAgent-完整优化方案.md)、[资料核查与选型依据](docs/资料核查与选型依据.md)。
- [Forge Marks 设计与来源](docs/Forge-Marks-图标设计与来源.md)：继续使用既有图标与视觉规范。

## 开发与许可说明

修改共享状态机、预算、授权或副作用边界时，应补充真实 PG 契约测试；修改界面时运行构建与 Playwright，并检查手机布局。新增数据库结构通过 Alembic 迁移，版本化任务集、项目基线和技能使用新版本标识，避免覆盖既有审计依据。

Forge Marks 的业务图形与改编来源见上方设计文档；部分基础图标源自 Iconoir，保留 [Iconoir MIT 许可](public/licenses/Iconoir-MIT.txt)。前端参考来源见 [REFERENCES.md](src/variants/twitter/REFERENCES.md)。研究快照保留在 `docs/research/`，应结合各自来源与证据范围阅读。

仓库当前尚未声明项目整体的开源许可证。第三方组件的许可仅适用于对应组件，不代表整个 ForgeAgent 已采用同一许可。
