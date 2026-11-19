# ForgeAgent 项目审查与技术选型

2026-11-19 · 基线 `c49b9c560bccda5f0301085ed95750e2232c7e65`

## 整改与复验记录

下文保留初审时的判断和证据。整改覆盖六项优先问题及性能、界面表中的五项问题。

| 范围 | 已交付的行为与复验入口 |
|---|---|
| 共享部署与隐私 | Worker 使用 PostgreSQL 跨连接限制并发，manager 独立限流，RPC 显式信任部署 CA。擦除跟随跨预算 root 的内容派生关系，同时清理当前及历史 manager 回执。见[共享部署测试](../tests/backend/test_manager_deployment.py)、[正式 Compose 拓扑冒烟脚本](../scripts/deploy-manager-smoke.sh)和[擦除回归](../tests/backend/test_run_erasure_scope.py)。 |
| 用户输入与交付 | 两个输入入口统一使旧提案失效，确认绑定提案生成时的输入版本。下载保留原始字节，公开验收报告的大小与摘要对应脱敏后交付给用户的内容。历史评测报告的校验文件已修正。 |
| 调度与工作台 | 隔离损坏历史、退避暂时性读取失败，按任务与状态分批查询队列，由对账、审批或到期唤醒等待任务。工作台在快照成功后更新版本并刷新待办目录；筛选按钮支持键盘操作。三种模型协议均保留讨论提示。 |

三项实验均已实现。配对评测用两个脚本化模型 profile、两种 Harness 配置运行四个合成 Python/TypeScript 任务，共 16 次试验；每个配置组合均有两个独立验收通过、两个按预期暂停。通过 CLI 可重跑及比较报告。[配对实验与命令](external-repair-pilot.md)

按需工具发现和只读符号组合均可按任务启用，默认关闭。OpenAI Responses 原生请求离线对照中，计入发现的一次额外请求，64 个远程工具的本地估算输入从 5,242 降至 3,081 token；2 个工具时从 1,479 升至 3,075。只读组合在单文件及 32 文件样本中各减少一个有依赖的工具调用阶段，保持源片段摘要与底层计量次数一致，原始回执各多出 48 个估算 token。25 次重复测量中，本地工具 P95 未出现改善。模型实际选择准确率、供应商费用与真实任务交付收益仍需使用固定模型测量。[可复跑的实验](../scripts/profile-tool-paths.py) · [完整结果](../benchmarks/reports/offline-tool-paths-2026-11-19.json)

本地复验使用 Windows、UTF-8 PostgreSQL 和锁定的 REA，后端全量 442 项通过、11 项跳过；独立 Chromium 实例连接真实 API 和 Worker，32 项端到端测试全部通过。[Linux 运行时验收记录](https://github.com/dafyy321-pixel/ForgeAgent/actions/workflows/runtime.yml) · [调度性能作业](https://github.com/dafyy321-pixel/ForgeAgent/actions/workflows/performance.yml)

## 初审记录

**下一轮先打通真实共享部署，修复方案确认、数据擦除和交付校验，再扩展模型实验。** ForgeAgent 已经有较完整的持久任务内核。当前影响使用者的主要问题出现在边界上：部署配置与实际运行依赖、预算家族与内容派生关系、内部证据与用户实际下载的文件，以及后端状态与浏览器所见状态。

## 现有能力和验证程度

| 维度 | 已有证据 | 决策含义 |
|---|---|---|
| 架构 | PostgreSQL 保存任务、账本、租约、事件和检查点。对象存储保留工作区和证据。模型提出完成后，Worker 调用独立验收并生成补丁。 | 保留单一调度所有者、未知效果对账和独立验收。下文的队列问题都有局部修复路径。 |
| 工程质量 | 同一 SHA 的 Linux CI 在 10 月 20 日通过迁移、Ruff、ty、前端 lint、OpenAPI 类型同步、wheel 安装、Docker 构建、pytest、前端构建和 Playwright。 | 可在现有流水线增加有针对性的回归。涉及真实部署配置的验收必须使用同一份配置。 |
| 真实任务 | 10 月的 paid-v10 是两个公开历史缺陷，各跑恢复开/关一次。关闭时 h11 和 trace-mapping 都经独立验收通过，分别使用 12 和 16 次模型调用。开启时两项都因支持恢复的证据不足而暂停。 | 这是两份模型补丁的交付记录。独立任务数为 2。样本不足以决定默认启用恢复或判断其总体效果。 |
| 成本与运维 | v8–v10 的已知模型费合计 0.147036 美元，另有 0.033027 美元未决预留。环境、工具、存储费用不全。同 SHA 的四次专用性能作业均跳过。查询时公开的 live release 和 image 工作流均为 0 次运行。 | 保留未知账单和失败尝试。当前公开记录不足以支持生产容量、完整成本或正式共享部署可用性的承诺。 |

证据来源：[当前能力矩阵](capabilities.md) · [付费试验记录](../benchmarks/reports/external-repair-evidence-2026-10-19.json) · [同提交 CI](https://github.com/dafyy321-pixel/ForgeAgent/actions/runs/39373613841) · [11 月 16 日性能任务](https://github.com/dafyy321-pixel/ForgeAgent/actions/runs/43857049052) · [发布验收](https://github.com/dafyy321-pixel/ForgeAgent/actions/workflows/release-gates.yml) · [镜像发布](https://github.com/dafyy321-pixel/ForgeAgent/actions/workflows/images.yml)

## 优先整改

P1 指会阻断一条已提供的工作流，或影响数据和确认承诺。P2 指在特定数据规模、网络状态或操作路径下出现的问题。以下优先级用于本项目排期。

### 1. P1 · 共享沙箱配置存在两个独立阻断点

Compose 要求 sandbox-manager 没有业务数据库凭据，但每个执行请求都要在 manager 内连接 PostgreSQL 领取 advisory lock。按文档部署时，默认连接指向 manager 容器自身的回环地址。另一个问题出现在更早的 TLS 握手阶段。Compose 通过 `SSL_CERT_FILE` 指定私有 CA，Worker 访问 manager 的 HTTPX 客户端使用 `trust_env=False` 且没有传入 CA。锁定版本的 HTTPX 在这个配置下使用 certifi。发布测试使用 HTTP 回环并让 manager 继承数据库配置，两种偏差均可被测试掩盖。[部署清单](../compose.deploy.yaml) · [沙箱 RPC，第 84、169、202 行](../backend/forgeagent/sandbox_manager.py) · [准入连接，第 21 行](../backend/forgeagent/concurrency.py) · [HTTPX 源码](https://github.com/encode/httpx/blob/0.28.1/httpx/_config.py#L33-L41) · [发布测试配置](../.github/workflows/release-gates.yml)

将跨 Worker 的并发配额放在已有数据库权限的一侧，manager 独立限制本机容量。RPC 客户端显式加载部署 CA，继续禁用环境代理。证书按服务单文件挂载，当前整个 TLS 目录同时挂到多个服务，实际私钥可读性取决于宿主权限。**验收**使用正式 Compose 拓扑和测试 CA，让无 DB、模型、S3 凭据的 manager 完成一次固定镜像的只读验收；错误 CA 应被拒绝，并发额度在两个 Worker 同时请求时仍然生效。

### 2. P1 · 补充需求后，旧讨论方案仍可确认执行

普通消息入口会清除 `plan_ready`，另一个公开的 `/input` 入口更新输入版本和对话后保留该标记。对于已提交方案的暂停讨论，客户端可取得当前版本与重新计算的对话摘要，通过确认检查。此时助手还没有为修改后的需求提出新方案。显式用户确认、项目范围和服务端权限检查仍然存在。[两个输入入口和确认条件，第 34、75、138 行](../backend/forgeagent/conversation_api.py)

将方案绑定到生成时的输入版本，将两个入口的方案失效规则收拢到同一个事务内。分别测试从两个入口补充要求、等待新提案、确认执行的全过程，验证只有对应输入版本的新提案可以创建执行任务。

### 3. P1 · 擦除完成后，派生任务和 manager 回执仍可能保有正文

Fork、继续讨论和从确认方案创建执行任务会复制工作区或对话，接收方可能拥有独立预算 root。擦除闭包沿 root、记忆和技能传播，遗漏了显式运行来源。没有其他记忆或技能关系时，这些独立任务会留下内容。另有一条物理存储路径：独立 manager 将请求和含命令输出的回执写入操作目录，家族擦除只处理对象、工作区和 verification 目录，随后即可返回 `erased`。前者在本地模式也成立，后者以使用独立 manager 且产生持久回执为前提。[闭包和物理清理，第 27、118 行](../backend/forgeagent/knowledge_erasure.py) · [Fork](../backend/forgeagent/api.py) · [复制工作区和对话，第 115、155 行](../backend/forgeagent/conversation_api.py) · [操作回执，第 95、105 行](../backend/forgeagent/sandbox_manager.py)

用现有来源字段处理历史派生关系，新操作持久登记内容来源。让 manager 回执可按租户与任务定位，物理清理完成后再确认擦除。以唯一测试标记覆盖多跳派生、重试和无关任务，分别检查用户读取结果与磁盘副本；保留必要的无正文财务审计事实。

### 4. P1 · 一条损坏的任务历史可能阻塞整个租户的队列

领取操作先按租户加锁，选最早可运行任务，再校验已有 epoch 的历史。投影比较失败有暂停路径，但检查点损坏或事件缺口可让重建直接抛错。领取事务回滚后任务仍在原队首，外层调度器只记录异常类型，后续领取可能持续命中同一条任务。这是对损坏历史的静态故障分析，本次未观察到线上事故。[领取逻辑，第 94–110 行](../backend/forgeagent/worker.py) · [重建校验，第 67–84 行](../backend/forgeagent/reducer.py) · [调度异常处理，第 30 行](../backend/forgeagent/scheduling.py)

在损坏事件链以外持久记录隔离状态，让领取查询跳过待人工处理的任务；暂时性存储故障单独退避。验收条件是同租户放入一条损坏记录和一条健康任务，出现可定位的诊断，健康任务仍能完成，坏任务的外部动作不会被重发。

### 5. P1 · 用户收到的文件可能与显示的摘要不一致

有两个独立原因。远程产物按原始字节入库，浏览器下载时却先调用 `Response.text()`，再从字符串创建 Blob。非 UTF-8 文件会被重新编码。用同一 Web API 检查，三字节 `00 80 FF` 变成七字节。另一个问题只需普通 JSON 即可出现：受保护验收报告下载前会脱敏，列表仍展示内部原始报告的摘要和大小。即使使用保留原始字节的客户端，公开报告也可能校验失败。[浏览器下载，第 540–564 行](../src/useRuntime.ts) · [文本解码，第 99 行](../src/api.ts) · [脱敏下载，第 713 行](../backend/forgeagent/api.py) · [产物摘要，第 221 行](../backend/forgeagent/presenters.py) · [公开报告投影](../backend/forgeagent/service.py)

让下载直接使用字节，单独提供文本预览。为公开验收报告生成与下载内容一致的摘要和大小，内部证据继续独立保存。用受保护验收报告、非 UTF-8 文件和普通文本分别从浏览器保存，逐项比对公开摘要并检查脱敏。交付页还应把点击的产物 ID 放入路由，目前预览入口只传运行 ID，任务详情默认选第一个产物。[预览入口](../src/TaskViews.tsx) · [详情页选择](../src/TaskWorkspace.tsx)

### 6. P2 · 已发布的外部试验摘要文件无法验证 Git 中的报告

对当前工作树与 `git show HEAD:<path>` 的原始字节分别计算 SHA-256，均为 `35498371e33c19a55af2fec01577805b56ea0ff5c1eca79ed18ff13ff6966282`。随附校验文件记录 `d48ff45a62a4eb7be266e9d36f94fb0b981f4a6021fe8d53b379cf6d4702b328`，恰好等于同一文件转成 CRLF 后的摘要。仓库要求 LF。这能解释校验失败，报告中的模型结果本次未重跑。[报告](../benchmarks/reports/external-repair-evidence-2026-10-19.json) · [校验文件](../benchmarks/reports/external-repair-evidence-2026-10-19.sha256) · [Git 换行规则](../.gitattributes)

从最终待发布字节生成校验文件，在干净 checkout 对所有已跟踪的报告与校验文件执行一致性检查。这项修复小，适合先交付。

## 性能、界面与可维护性

| 优先级 | 具体问题 | 建议与可观测结果 |
|---|---|---|
| P2 | 每个任务执行步都会读租户整个 Inbox，再在 Python 排除已消费和其他任务的消息。远程轮询还有租户级 Outbox 读取。 | 在 SQL 限定待处理状态和任务或动作，按查询计划选索引并限制批次。用一万条已消费消息加一条待处理消息，记录装载行数、持锁时间、普通本地任务 P95。[调用入口](../backend/forgeagent/worker.py) · [查询，第 376、386、562 行](../backend/forgeagent/remote.py) |
| P2 | 取消后待人工对账的任务约每 30 秒被领取、再写等待事件。这条路径没有新检查点，反复从同一个检查点归约，连续 n 轮的累计工作量可达二次增长。审批等待也会周期性写事件。 | 在状态变化时写业务事件，由对账、审批或到期时间唤醒，保留低频兜底扫描。测一小时等待产生的事件数、重放条数和人工审批版本冲突率。[等待分支，第 269、316 行](../backend/forgeagent/worker.py) · [重放查询](../backend/forgeagent/reducer.py) |
| P2 | 页面先保存已见 revision，再拉取工作区；拉取失败被内部捕获。在没有 SSE 或后续版本变化的列表页，一次短暂失败即可让页面持续显示旧状态。另一路径上，待办计数会刷新，但同页目录只依赖 URL。 | 只确认已成功应用的快照版本，用它刷新受影响的待办、审批目录。固定 revision，模拟一次 503 再恢复；让用户停在空收件箱，验证新审批出现、处理后消失。[工作区轮询，第 278 行](../src/useRuntime.ts) · [目录依赖](../src/useCatalog.ts) · [待办来源，第 448 行](../src/TaskViews.tsx) |
| P2 | 默认 native 模式通过字符串截断移除 JSON schema，也一并去掉其后追加的讨论模式提示。原生控制工具描述仍解释方案确认，服务端继续限制只读权限；缺失的是完整讨论指引，例如使用用户语言。 | 将协议 schema 与模式提示分开组装。对同一讨论输入检查 native、structured、JSON 三种实际出站请求，断言保留讨论语义。[编译器，第 78–88 行](../backend/forgeagent/context.py) · [native 适配，第 150 行](../backend/forgeagent/model_protocol.py) |
| P3 | 任务筛选声明为 ARIA tablist，键盘方向键未实现。 | 若交互本质是列表筛选，改为带选中状态的按钮组；以仅键盘操作切换并核对筛选结果。[任务筛选](../src/TaskViews.tsx) |

`api.py` 约 1,700 行，`worker.py` 约 1,200 行。更有价值的拆分点是上面已经出现分歧的业务规则：两个输入入口共同使用方案失效逻辑、三个出口共用公开产物表示、领取故障拥有独立的处置状态。仅按文件长度拆目录难以验证收益。性能方面，仓库保留了共享 Windows 环境的历史读路径样本与调度波动，尚需在专用 runner 重建当前 SHA 基线。[历史读路径测量](history-performance.md) · [专用性能作业设计](scheduler-performance.md)

## 近期技术与适配实验

### 首选：模型与 Harness 配对评测

11 月 17 日，Runta 与 Baseten 公布 FrontierHarness Eval v1.1。原文列出 22 种配置、5 个模型、62 项任务、每项 4 次尝试，共 27,280 次运行。不同模型对应的成本与通过率排序会变化。11 月 18 日 X 有相关报道，Reddit 讨论指出样本构成和重采样会影响对排名的解读。这是供应商组织的外部实验，任务以终端工作为主。[研究原文](https://runta.com/blog/introducing-frontierharness-eval-v1-1/) · [X 报道](https://x.com/thenewstack/status/2122963097295945806) · [Reddit 讨论](https://www.reddit.com/r/artificial/comments/1xzcg62/they_ran_27280_codingagent_trials_the_best/)

ForgeAgent 已有按 case 配对、费用结算和单变量实验。增量是在模型 profile 或核心 Harness 改动时重跑同一批任务，并按 profile 单独报告。建议先补一批可离线重现、按仓库和问题族拆分的 Python/TS 缺陷，覆盖补丁生成、需要人工澄清、需要正确暂停三种情况。先固定一个模型比较恢复开关，再按相同协议跑第二个模型。记录独立 PASS、正确暂停、人工介入、每个成功交付所摊的全部尝试成本、未知费用和 P95；保留失败任务与每个负迁移案例。两个历史成功案例可做冒烟，单列于隔离保留集之外。[配对计划](../backend/forgeagent/pilot.py) · [研究统计](../backend/forgeagent/research.py) · [外部 pilot 的样本边界](external-repair-pilot.md)

### 条件采用：按需加载远程工具 schema

当前 context 将整份已选远程工具描述放进必需材料，native 协议又把有权调用的工具定义发给模型。工具目录较大时会消耗上下文，甚至在首次有效读取前触发预算门槛。Anthropic 的 Tool Search 支持延迟加载；这是 2025 年已经公布的能力，11 月的 X 讨论反映的是持续存在的工程问题。[官方机制](https://www.anthropic.com/research/advanced-tool-use) · [11 月 X 讨论](https://x.com/SeanJHKwon/status/2121984504948568225) · [必需目录，第 109 行](../backend/forgeagent/context.py) · [native 工具构造](../backend/forgeagent/model_protocol.py)

先测真实请求中的工具字节和 provider 输入 token。最小对照组是消除 native 模式的重复描述。只有在目录仍占明显预算时，再试只读工具发现，按请求展开少量已授权、已固定摘要的 schema，保留原有派发权限、审批与审计。用小目录和大目录分别比较正确选工具率、首个有效动作前的调用次数、输入 token、端到端成功率；把发现多耗的一轮也计费。

### 小范围试验：Code Mode 的只读工具组合

Anthropic 在 11 月 12 日发布实验性 Code Mode，允许模型编写 JavaScript 调用工具，11 月 16 日扩展了与 agent teams 的兼容。X 上一项只有两种任务的小实验报告，较大任务省 token，小任务反而更贵。适合借鉴的是减少连续读取之间的模型往返，整体迁移的收益需要实测。[11 月 12 日发布说明](https://github.com/anthropics/claude-code/releases/tag/v2.1.323) · [11 月 16 日发布说明](https://github.com/anthropics/claude-code/releases/tag/v2.1.326) · [X 小样本记录](https://x.com/bolyki/status/2123068346224509366)

ForgeAgent 已有动作融合、逐动作账本和观察摘要。建议先实现一个受限的只读组合：对一次符号搜索命中的若干位置做有界范围读取，返回带路径、行号和来源摘要的结果，每个底层动作仍独立计量。这个原型可复用现有工具，也能检查固定动作列表难以表达的“先搜索、再读命中项”。对照现有融合，在小仓库和多文件仓库分别统计定位轮次、返回 token、P95 和独立 PASS。如果收益只出现在大任务，就按任务启用。[现有动作融合，第 705 行](../backend/forgeagent/worker.py) · [仓库工具](../backend/forgeagent/repo_tools.py)

## 建议执行顺序

1. **先恢复可信交付。** 修正报告校验文件、公开产物摘要和下载字节。同步修复两种输入入口的方案失效规则。这几项可用确定性测试验收。
2. **共享部署前通过两条端到端检查。** 用私有 CA、无数据库凭据的 manager 完成一次验收；用正常创建的派生任务和 manager 回执验证擦除闭环。让发布工作流留下绑定 SHA 的结果。
3. **再做队列和前端可靠性。** 隔离损坏历史，缩小 Inbox 查询，按状态变化唤醒等待任务，补齐工作区断线恢复和审批同页刷新。开启专用 runner 后以当前提交建立可复测的基线。
4. **最后决定默认策略。** 先扩大跨仓库配对样本，再按成本、成功率和人工介入决定是否扩大恢复、结构检索或工具组合的启用范围。额外模型、向量库和第二套调度引擎目前缺少足以支撑迁移成本的项目数据。

本次使用三个 subagent 分别检查运行时、前端与发布工程、部署与隐私，再对照源文件复核主要发现。Graphify 对后端模块和前端顶层实现共 86 个文件做了结构提取，另行阅读了迁移相关文档、工作流、测试及现有评测。实际重新计算了报告摘要，并核查同提交的公开 CI 步骤。本机没有重跑 PostgreSQL、Linux Docker、真实模型及跨主机部署；建议的回归条件和性能实验均为后续验收项。
