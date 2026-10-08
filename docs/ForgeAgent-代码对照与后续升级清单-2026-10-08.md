# ForgeAgent 代码对照与后续升级清单

审查日期：2026-10-08。代码基线：`ef4b6fe`，项目版本 `0.2.0`。对照文件：[完整优化方案](D:/PAI/ForgeAgent/docs/ForgeAgent-完整优化方案.md)、[后端实施与验收](D:/PAI/ForgeAgent/docs/后端实施与验收.md)。

本轮是代码审查与升级建议，没有修改业务代码。方案中的执行指令作为对照材料，不视为本轮开发授权。审查覆盖后端 Runtime/API/适配器/存储/验证/评测，前端数据和操作链路，迁移、测试、CI、启动脚本与部署配置。没有重新核查方案引用的外部论文或最新协议规范，因此下文的协议升级均要求真实互操作测试，不能仅凭版本名称判定兼容。

## 结论与证据边界

项目已经具备可运行的持久 Runtime 开发实现：真实 PG、租约与 epoch、动作意图和回执、审批、root 预算、文本快照、独立验收、MCP/A2A 适配、评测和控制台均已有代码。下一阶段应优先修补已有链路的不变量，再提升真实仓库可用性、扩容效率和研究能力。

清单共 **68 项**。其中 **4 项 P0、52 项 P1、12 项 P2**。这是代码问题与目标能力的工作清单，不是 68 个互相独立的大项目；不少项应合并在同一工作包落地。P0 指当前可绕过关键约束或导致任务卡死的缺口；P1 指下一阶段的可靠性、可用性及验收建设；P2 指有条件引入的增强、产品完善或研究能力。涉及同租户权限的问题，在共享部署前应作为门禁，本机单用户模式下影响较小。

证据等级：**复现**＝本轮用隔离测试租户或本地 Git 实际检查；**静态**＝从当前调用链可直接确认；**差距**＝方案要求尚未完整实现；**条件**＝只有目标场景或实测收益成立才投入。

本轮检查结果：

- `uv run --frozen ruff check backend tests/backend migrations`：通过。
- `node node_modules/typescript/bin/tsc --noEmit -p tsconfig.json`：通过。
- 首次全量测试时本地 PostgreSQL 尚未运行；终止该次尝试，启动项目已有的本地 PG 后重跑。
- `uv run --frozen pytest tests/backend -q --maxfail=3`：**75 passed、3 skipped、2 warnings**，耗时 23.80 秒。3 项跳过为真实 Docker 测试；不能计为通过。
- 单独筛选的不依赖数据库检查：21 passed、52 deselected；这与全量测试有重叠，不能相加。
- 新增临时检查没有调用付费模型、真实远端副作用或部署外网。使用测试身份替换 OIDC 入口，验证的是身份解析之后的 API 授权行为，不是 OIDC 签名绕过。
- 未重新运行 Playwright、真实模型、真实 Docker、S3 或 MCP/A2A 服务联调；旧文档中的历史验收数字与本轮结果分开处理。

## A. 已有实现中优先修复的 10 项

### 01 · P0 · 补齐同租户内的资源与操作授权【复现】

**现状与问题：** RLS 和 `db.get()`限制租户；任务控制没有校验调用者是否为创建者或项目授权成员。`service.authorization()`检查的是 `run.actor` 的能力。审批函数虽然接收 reviewer，却没有判断 reviewer 是否有审查该动作的资格。租户边界不等于用户/项目边界。

**本轮证据：** 使用同租户、非管理员、没有 `Authorization` 记录的 `outsider` 身份，暂停 `owner` 的任务得到 `202 PAUSED`。审批 reviewer 权限缺口为静态结论，没有执行真实外部效果。

**升级：** 在 API/Service 公共入口统一检查 `actor × project × resource × operation`；区分查看、编辑、控制、审批、对账、评测权限；Worker 继续使用原执行主体的有效能力。新建任务的幂等作用域也应纳入调用主体，避免同租户其他用户猜中 key 后拿到已有响应。

**验收：** 同租户 A/B 用户分别覆盖读取、暂停、恢复、取消、fork、输入、bind-model、审批及下载；非授权用户拒绝，获明确项目共享权限时允许。依据：方案 13.4、16；[api.py](D:/PAI/ForgeAgent/backend/forgeagent/api.py:151)、[service.py](D:/PAI/ForgeAgent/backend/forgeagent/service.py:99)、[approve](D:/PAI/ForgeAgent/backend/forgeagent/service.py:428)。

### 02 · P0 · 将隐藏测试移出公开项目、检查点和事件投影【复现】

**现状与问题：** `/projects`返回完整项目 JSON，包括 `acceptance.protected_tests`。检查点包含完整 `state.acceptance`；事件投影同样保存完整 state，公开 replay/SSE 也存在暴露路径。生成模型当前上下文没有直接注入这些测试，无网沙箱也不会直接访问控制面；不能据此称模型已绕过隔离。但普通成员能获取本应受保护的验收答案，削弱共享评测与角色隔离。

**本轮证据：** 非管理员请求 `/projects`为 `200`且含 `protected_tests`；请求另一用户任务的 `/checkpoints`同样 `200`，返回隐藏测试标记 `SECRET_ORACLE`。

**升级：** 隐藏测试使用独立权限的对象引用；Run/Event/Checkpoint 保存验收摘要和受控引用；DTO 按角色过滤，普通页面只返回验收 ID、摘要和允许展示的诊断。

**验收：** 项目、workspace、run、checkpoint、context、events、replay、报告及下载入口均不得向无验收读取权限的主体返回测试正文。依据：方案 14.2、18；[projects](D:/PAI/ForgeAgent/backend/forgeagent/api.py:583)、[checkpoints](D:/PAI/ForgeAgent/backend/forgeagent/api.py:354)、[emit](D:/PAI/ForgeAgent/backend/forgeagent/db.py:233)。

### 03 · P0 · 统一验证入口与成功态的账单结算门禁【复现】

**现状与问题：** `resume`拒绝 UNKNOWN ModelCall，但 `/verify`只检查 Action；`Worker.complete()`没有检查未知模型账单与预算预留。可以通过另一入口绕过暂停原因。

**本轮证据：** 对已修正的 fixture 工作区设置 UNKNOWN ModelCall、未知账单和 100 微美元预留。`POST /verify`返回 `202`；Worker 随后置为 `SUCCEEDED`，预算仍预留 `100`。这是受控状态检查，不涉及真实模型收费。

**升级：** 抽取统一的 `can_verify/can_finalize`确定性条件，涵盖模型用量、预算账本、动作、子任务、产物摘要及必要发布回执；所有成功入口共用。允许单独生成测试报告时，也不能把 Run 标为已结算完成。

**验收：** 未知费用、零货币但未知 Token、未结算子调用分别阻止正常终态；对账后才能完成。依据：方案 11.2、14.4；[verify_run](D:/PAI/ForgeAgent/backend/forgeagent/api.py:515)、[complete](D:/PAI/ForgeAgent/backend/forgeagent/worker.py:600)。

### 04 · P0 · 修复取消与中断模型账单对账后的任务卡死【复现】

**现状与问题：** 接管 DISPATCHED ModelCall 时，`claim()`将 Run 无条件设为 PAUSED；即使已经请求取消也会覆盖 CANCELLING。退出后 Job 为 DONE。预算对账不重新调度；重复 cancel 因 `cancel_requested=True`提前返回。

**本轮证据：** CANCELLING 任务带中断模型调用，接管后为 `PAUSED / cancel_requested=True / job=DONE`；结算并重复 cancel 后仍不变，下一 Worker claim 返回 `None`。resume 又拒绝已请求取消的任务。

**升级：** 将取消意图与等待账单状态组合处理；账单对账后自动唤醒取消结算流程；重复取消应保持幂等且修复必要调度，不丢失未完成操作。

**验收：** 覆盖取消与响应、失联、接管、对账的不同交错，最后进入 CANCELLED 或明确的待对账态，无不可操作的 PAUSED。依据：方案 5、6、11；[claim](D:/PAI/ForgeAgent/backend/forgeagent/worker.py:28)、[control](D:/PAI/ForgeAgent/backend/forgeagent/service.py:340)、[预算对账](D:/PAI/ForgeAgent/backend/forgeagent/api.py:436)。

### 05 · P1 · 修正空文件创建/删除补丁，验证补丁确实可应用【复现】

**现状与问题：** `sandbox.patch()`对新增/删除空文件只输出 `diff --git`，没有新建/删除 mode 等有效信息；Verifier 验证的是 `current`文件集合，没有在指定基线上应用最终 patch 后再比对文件树。

**本轮证据：** 空文件新增、删除两例，生成补丁均只有一行；`git apply --check`退出 `128`：`No valid patches in input`。工作区通过不代表补丁可交付。

**升级：** 优先用 Git 生成标准 patch；显式支持空文件、无末尾换行及文件删除，后续扩展 mode/二进制。最终验收从干净基线应用实际交付 patch，并对比得到的树摘要。

**验收：** 新增、删除、空文件、换行边界的 patch 可应用，应用后的树与验证对象一致。依据：方案 14.1；[patch](D:/PAI/ForgeAgent/backend/forgeagent/sandbox.py:149)、[complete](D:/PAI/ForgeAgent/backend/forgeagent/worker.py:600)。

### 06 · P1 · 验收失败后支持受预算约束的修复回路【静态】

**现状：** `complete()`对 FAIL 直接置 FAILED。模型第一次提议完成后若隐藏验收失败，当前尝试即终结，不能继续消费验收反馈修复。

**升级与验收：** 区分可修复验收失败、硬终止、环境不可判定；将允许公开的失败诊断返回决策回路，设置次数和总预算上限，最终通过仍由独立验证签发。不能暴露隐藏答案或修改验收。依据：方案 5、14；[complete](D:/PAI/ForgeAgent/backend/forgeagent/worker.py:600)。

### 07 · P1 · 新输入后重新判断完成提议和 TaskSpec revision【静态】

**现状：** `/input`写入 `state.input`，却不清理旧 `completion`；恢复后 `advance()`发现 completion 会直接验证，新增输入可能没有经过模型处理。也没有正式任务修订接口。

**升级与验收：** 区分补充信息与修改目标；前者使旧决策失效并重新决策，后者生成 TaskSpec revision、更新验收绑定并使旧证据失效。验证 INCONCLUSIVE 后补充信息应先被实际消费。依据：方案 4、5、14；[user_input](D:/PAI/ForgeAgent/backend/forgeagent/api.py:504)、[advance](D:/PAI/ForgeAgent/backend/forgeagent/worker.py:215)。

### 08 · P1 · 明确 resume 的 checkpoint 参数语义【静态】

**现状：** 控制入口仅验证 `checkpoint_id`属于该任务，实际 `service.control()`仍使用最新 workspace 和 state；选择旧检查点不改变恢复位置。fork 才真正使用所选 workspace。

**升级与验收：** 若 resume 只继续最新状态，应拒绝或移除该参数；若提供指定检查点恢复，则需要明确新旧事件、动作、外部效果和预算的处理规则，不能回滚已发生效果。通常旧快照实验应走 fork。依据：方案 8.6、16；[control API](D:/PAI/ForgeAgent/backend/forgeagent/api.py:151)。

### 09 · P1 · 打通技能发布 UI 的评测门禁【静态】

**现状：** `toggleSkill`启用时只发送 enabled/review；后端 release 必须提供 evaluation_id/configuration，因此当前开关不能完成合法发布。

**升级与验收：** candidate→选择合格 held-out 实验→显示对照和门禁→人工说明→发布；停用保留简单入口。门禁失败准确展示原因。依据：方案 10、16；[toggleSkill](D:/PAI/ForgeAgent/src/useRuntime.ts:63)、[release_skill](D:/PAI/ForgeAgent/backend/forgeagent/api.py:678)。

### 10 · P1 · 修复设置页异步状态与草稿同步【静态】

**现状：** `Settings`只在首次挂载用 `useState(app.settings)`初始化；后端数据晚到时草稿可能继续显示前端默认值，随后保存可覆盖真实设置。

**升级与验收：** 加载结束后建立草稿，或在未编辑时同步后端；显式 dirty/version，显示保存成功和冲突。慢请求下已有设置正确显示，用户编辑不被轮询覆盖。依据：方案 16；[Settings](D:/PAI/ForgeAgent/src/BackendViews.tsx:31)。

## B. Runtime 与恢复：11–18

| 编号/优先级 | 现状与可升级内容 | 最低验收与依据 |
|---|---|---|
| 11 / P1 | **事件重建与版本化 reducer【差距】**。`replay()`直接取末事件 projection，并非对历史事件执行 reducer；事件重复存完整 state。增加版本化状态重建、checkpoint 后增量恢复、seq 连续性和投影一致性检查，保留审计回放的只读语义。 | checkpoint + 后续事件重建与 live projection 一致；旧 schema 有兼容测试。方案 6、8；[replay](D:/PAI/ForgeAgent/backend/forgeagent/service.py:458)、[emit](D:/PAI/ForgeAgent/backend/forgeagent/db.py:233)。 |
| 12 / P1 | **粒度合理的语义绑定和升级【差距】**。当前把大量 Python 文件整体 hash，API/config 等变化也可能阻断运行；但缺少可执行旧版本镜像和细粒度迁移。按 prompt/tool/harness/verifier/model/lock/image 分项固定，记录分段与兼容决策。 | UI/API 非执行变更不无谓中断；真正语义变化仍阻断；旧 Run 可用原版本执行或显式迁移/fork。方案 8.5、17.4；[implementation_bindings](D:/PAI/ForgeAgent/backend/forgeagent/service.py:50)。 |
| 13 / P1 | **明确已持久化模型响应的消费机制【差距】**。当前响应、决策、动作主要同一次事务保存；对象先上传但 PG 未提交会重新请求并可能再次收费。增加 response-received/decision-applied 标记和稳定调用身份，能查询的 provider 优先对账。 | 已提交响应不重调模型、不派生两套动作；对象孤儿与未提交响应不冒充已提交事实。方案 6.4、8；[decide](D:/PAI/ForgeAgent/backend/forgeagent/worker.py:232)。 |
| 14 / P1 | **调度并发、公平性和背压【差距】**。已有 tenant 配额与 SKIP LOCKED，但一个 Worker 按 tenant 顺序 `await once`，长调用阻塞其后租户。增加有界执行槽、root/provider/sandbox 配额、队列优先级和公平领取。 | 20 活动 Run 下测量队列等待、吞吐和饥饿；旧 epoch 保持不可提交。方案 6.5、20；[claim/run](D:/PAI/ForgeAgent/backend/forgeagent/worker.py:28)。 |
| 15 / P1 | **移出 asyncio 中的阻塞 DB/对象操作【静态】**。异步 Worker/SSE/远端轮询使用同步 SQLAlchemy/boto3/文件调用；慢调用可阻塞 heartbeat。使用受控线程边界或异步驱动，避免长 I/O 持有 Run 行锁。 | 人为延迟 PG/S3 后，其他任务心跳和 SSE 保持响应，无无故接管。方案 6.1、17；[heartbeat](D:/PAI/ForgeAgent/backend/forgeagent/worker.py:78)、[storage](D:/PAI/ForgeAgent/backend/forgeagent/storage.py:24)。 |
| 16 / P1 | **加强工作区原子提交与失败隔离【差距】**。epoch 目录已经隔离；restore 只写入、不删除目录残留；同一 lease 多动作中，写入后快照失败可能留下未提交内容供下一动作读取。增加动作暂存树、成功快照提交与失败回退。 | 中途 I/O/快照失败后下一动作仅见已提交树；restore 对已有目录也得到准确树。方案 7、8.3；[restore](D:/PAI/ForgeAgent/backend/forgeagent/sandbox.py:52)、[dispatch](D:/PAI/ForgeAgent/backend/forgeagent/worker.py:440)。 |
| 17 / P1 | **进展与失败分类【差距】**。重复指纹只看 calls 和 workspace；缺少 error signature、已完成验收子目标、技术重试/重规划分账。建立机器可读原因与持久 Run Journal，明确计数重置规则。 | 同样调用但取得新证据不误停；反复不同无效写入不能绕过死循环检查。方案 5.3、8.7；[decide](D:/PAI/ForgeAgent/backend/forgeagent/worker.py:394)。 |
| 18 / P1 | **子任务 join 与预算契约【差距】**。已共享 root、隔离目录及合并冲突；父任务等待所有 child，任一失败即 CHILD_FAILED。增加 required/optional、deadline、预留分配、交付类型和明确 join 结果。 | 可选探索失败不使父任务无条件停滞；必需 child 未结算不能完成；合并后再验收。方案 11.3；[create_run](D:/PAI/ForgeAgent/backend/forgeagent/service.py:132)、[advance](D:/PAI/ForgeAgent/backend/forgeagent/worker.py:204)。 |

## C. 真实 Coding 能力与验证：19–26

| 编号/优先级 | 现状与可升级内容 | 最低验收与依据 |
|---|---|---|
| 19 / P1 | **完整 Git 工作区【差距】**。现在是 UTF-8 文件字典；无真实 commit/worktree、二进制、mode、符号链接和子模块能力。引入固定 Git commit、受控仓库来源及规范内容 manifest；按需支持特殊文件。 | 在固定真实仓库中产生可应用 patch，子任务 worktree 隔离，快照恢复保留声明的文件语义。方案 8、11、14；[files/restore](D:/PAI/ForgeAgent/backend/forgeagent/sandbox.py:27)、[CLI register](D:/PAI/ForgeAgent/backend/forgeagent/cli.py:133)。 |
| 20 / P1 | **最小但实用的仓库工具集【差距】**。当前工具为 list/read/整文件 write/test/recall/integrate，缺少范围读取、rg、apply_patch、删除/移动和符号查询。先补最常用工具，均有大小、路径和前置摘要约束。 | 中大型文件定位无需整文件读写；每项操作有明确恢复契约。方案 7、9；[TOOLS](D:/PAI/ForgeAgent/backend/forgeagent/service.py:15)、[TOOL_INPUTS](D:/PAI/ForgeAgent/backend/forgeagent/domain.py:128)。 |
| 21 / P1 | **可用的依赖/构建与长期沙箱【差距】**。当前测试每次 Docker run、工作区只读且网络关闭，基础镜像只有 Python；难覆盖 npm/build/install/dev server。按任务镜像预装锁定依赖，提供受限 scratch/cache；需要时持久沙箱加恢复初始化。 | 至少 Python 与 JS 真实任务可执行构建/测试；依赖下载受控、不会给验收产物任意写权限。方案 8.1、13；[execute](D:/PAI/ForgeAgent/backend/forgeagent/sandbox.py:80)、[Dockerfile](D:/PAI/ForgeAgent/sandbox/Dockerfile:1)。 |
| 22 / P1 | **类型化验收和交付契约【差距】**。Task.criteria/scope 主要展示，deliverables 未用于完成判定；验收仍为项目 JSON 单条 argv。建立 AcceptanceSpec、必需 ArtifactSet、范围和发布需求的强契约。 | UI 不把自然语言 criteria 当已验证标准；缺必需产物不成功；发布任务缺 receipt 不成功。方案 4、14；[Task](D:/PAI/ForgeAgent/backend/forgeagent/domain.py:49)、[complete](D:/PAI/ForgeAgent/backend/forgeagent/worker.py:600)。 |
| 23 / P1 | **环境失败归为 INCONCLUSIVE【静态/差距】**。有 argv/image 时，任何非零退出均 FAIL；缺模块、环境设施故障、超时未有结构化分类。保护测试目前仅 tests/test 路径和少数 Python 配置的精确比对。 | 缺依赖和业务断言失败分开；按项目保护验证配置，同时允许合法新增测试；测试诊断可追溯。方案 14；[verify](D:/PAI/ForgeAgent/backend/forgeagent/verification.py:7)。 |
| 24 / P1 | **补齐 EvidenceBundle【差距】**。已有 digest/verdict/command/result；缺 task revision、开始结束时间、完整日志引用、实际环境/依赖清单与测试报告。独立验收还需避免生成代码通过导入路径/配置替换验证逻辑。 | 任一交付证据可独立定位任务、产物、环境和命令；外部重放验证一致；红队用例不能用空测试或替换 runner 过关。方案 14.2–14.3；[verify](D:/PAI/ForgeAgent/backend/forgeagent/verification.py:65)。 |
| 25 / P1 | **root CPU、存储和墙钟资源总量【差距】**。货币/Token/工具次数已有账本；容器有单次资源限额，但无 root CPU/存储硬额度，工作区与辅助调用未统一计量。明确硬限制、软估计、运行时间与等待时间。 | 多 child、日志和快照不会绕过 root 额度；到限额仍能保存必要终止证据。方案 11.2、20.3；[Budget](D:/PAI/ForgeAgent/backend/forgeagent/domain.py:41)、[budget ledger](D:/PAI/ForgeAgent/backend/forgeagent/service.py:258)。 |
| 26 / P1 | **共享执行的隔离与凭证架构【差距】**。开发 Docker 已有无网/非 root/只读边界；API/Worker 仍在同环境调用 Docker。共享运行需独立 Sandbox Manager/host、gVisor 与短期凭证代理，沙箱不持有控制面权限。 | 真实 Linux/gVisor 验证资源/网络/进程边界；Worker 不暴露 Docker socket 或生产凭据给任务。方案 13；[sandbox](D:/PAI/ForgeAgent/backend/forgeagent/sandbox.py:80)、[compose](D:/PAI/ForgeAgent/compose.yaml:1)。 |

## D. 模型、上下文和成本：27–34

| 编号/优先级 | 现状与可升级内容 | 最低验收与依据 |
|---|---|---|
| 27 / P1 | **按能力提供模型适配器【差距】**。OpenAI Chat Completions JSON mode、Anthropic 文本 JSON 已接；未处理原生工具调用、provider 特有推理项、结构化输出限制和 finish reason。按实际选用模型接接口，不强制全迁移。 | 截断、拒绝、空响应、工具配对和 provider 错误有契约测试；精确模型 ID 与可复现限制透明。方案 11.1；[generate](D:/PAI/ForgeAgent/backend/forgeagent/models.py:16)。 |
| 28 / P1 | **完整计费和未知用量判定【差距】**。只计输入/输出，不区分缓存读写、思考 Token 等 provider 字段；预算金额为零时，评测仅看 reserved 金额可漏掉 UNKNOWN/Token 预留。增加价格表版本、原始 usage 与逐项归因。 | 所有收费分项和失败/辅助调用纳入；未知费用为零也阻止错误结算报告；价格变更不改历史账单。方案 11、18、20；[estimate_cost](D:/PAI/ForgeAgent/backend/forgeagent/models.py:7)、[report](D:/PAI/ForgeAgent/backend/forgeagent/evaluations.py:150)。 |
| 29 / P1 | **真实 tokenizer 与协议预算【差距】**。UTF-8 字节上界安全但可能大幅浪费窗口；编译和预留使用不同序列化粒度。添加 tokenizer profile，统一 messages/tool schema/reasoning 预留计数，未知模型保留安全上界。 | 中文、代码、工具 schema 的实际计数不溢出且利用率可测；不能用更低估计绕过 root Token 额度。方案 9.2；[compile_context](D:/PAI/ForgeAgent/backend/forgeagent/context.py:15)、[decide](D:/PAI/ForgeAgent/backend/forgeagent/worker.py:255)。 |
| 30 / P1 | **上下文的相关性、关键事实与 Journal【差距】**。现在最新 receipt 优先，再 skills，再全部有效 memory；`unresolved`未形成完整独立状态投影，约束中缺少显式固定的预算/工具能力/验收诊断。增加 source_ref/dependency、关键错误保留和短进展记录。 | 很长观察不挤掉必须的审批、预算、关键错误和完成条件；每项可解释选入/省略原因。方案 9；[compile_context](D:/PAI/ForgeAgent/backend/forgeagent/context.py:22)。 |
| 31 / P2 | **分阶段结构化摘要【差距/条件】**。当前只有 full/elide。先规则缩减，再按阈值调用摘要；schema 固定 goal/constraints/facts/decisions/unresolved/evidence_refs，摘要调用有预算。 | 引用原文校验、约束不变，失真回退；同模型实验表明成功率不劣且总成本有收益再开启。方案 9.3、21.2；[Harness](D:/PAI/ForgeAgent/backend/forgeagent/domain.py:65)。 |
| 32 / P1 | **统一 ObservationEnvelope 与日志引用【差距】**。有 ref/preview/exit_code，但字段分散；原始结果通常 canonical 单行 JSON，HTTP observation 分行与模型 recall 解 JSON 后分行语义不同。统一 stdout/stderr/error spans、行范围、截断和引用接口。 | 页面和模型对同引用得到相同行号；超大输出保留截断声明和可用片段；quote 可原文验证。方案 9.4；[observation](D:/PAI/ForgeAgent/backend/forgeagent/api.py:361)、[dispatch recall](D:/PAI/ForgeAgent/backend/forgeagent/worker.py:504)。 |
| 33 / P2 | **稳定前缀与缓存/压缩经济性【条件】**。任务状态和观察都进同一动态用户消息，未规划模型缓存段与命中统计。按 provider 实际机制组织稳定 system/tools/skill 前缀，隔离私有材料。 | 衡量缓存命中、摘要代价和全任务费用；节省未成立即停用，不靠 token 压缩率证明收益。方案 9.5；[context](D:/PAI/ForgeAgent/backend/forgeagent/context.py:70)、[models](D:/PAI/ForgeAgent/backend/forgeagent/models.py:16)。 |
| 34 / P1 | **模型故障和安全重试策略【差距】**。有界重试已有，但目前可自动退避主要是 429；5xx 因未知账单不直接重试，未处理 Retry-After/jitter/provider 原请求查询。按无请求发出、确定未收费、未知响应分别处理。 | 429 尊重 Retry-After；5xx/超时不盲目重发；重试和 fallback 不漂移模型实验、不绕过未知费用。方案 8.7、11.1；[error handling](D:/PAI/ForgeAgent/backend/forgeagent/worker.py:328)。 |

## E. Memory、Skills 与离线演化：35–40

| 编号/优先级 | 现状与可升级内容 | 最低验收与依据 |
|---|---|---|
| 35 / P1 | **先全文/关键词的 Memory 检索【差距】**。create_run 当前取全部项目/租户有效 memory，固定进 state；没有任务相关性召回、valid_from/repo revision/conflict/supersedes。先 PG 全文/结构化检索；向量有实测收益再加。 | 记忆随任务相关，过期/被替代事实不召回；召回决策可评测，避免越权过滤晚于检索。方案 10.1–10.2；[create_run](D:/PAI/ForgeAgent/backend/forgeagent/service.py:155)。 |
| 36 / P1 | **删除血缘与活动任务撤回策略【差距】**。memory 删除只改 status；旧 Run 已复制正文，后续决策仍可能使用。区分审计保留、停止使用与隐私彻底删除，建立摘要/缓存/embedding/对象的血缘。 | 用户明确撤回使用后活动 Run 不继续注入；依法保留的历史记录有隔离说明；彻底删除可给出范围和结果。方案 10、15；[delete_memory](D:/PAI/ForgeAgent/backend/forgeagent/api.py:634)。 |
| 37 / P2 | **技能包与渐进披露【差距】**。SkillVersion 是单段 content + 元数据，尚无 SKILL.md/资源包 manifest、按需正文/引用加载和工具依赖。保留小包原则，增加完整内容 digest 与来源许可验证。 | 恢复固定包内全部资源摘要；按需披露降低上下文费用，内容不能扩张权限。方案 10.3；[register_skill](D:/PAI/ForgeAgent/backend/forgeagent/api.py:653)。 |
| 38 / P2 | **失败聚类和离线候选生成【差距/条件】**。当前人工登记 candidate；没有轨迹提炼、冲突检查或独立候选生成。离线分析公共失败模式，生成小型候选，再经过保留集门禁。 | 原始轨迹来源、敏感内容清理、候选生成费用可追溯；生产 Run 不自行改权限或热换技能。方案 10、21.1；[evaluations](D:/PAI/ForgeAgent/backend/forgeagent/evaluations.py:245)。 |
| 39 / P2 | **技能评测的因果归因和数据治理【差距】**。已有 held-out/20 case/2 project/3 repeat/非劣/费用门禁；但候选配置可同时改 memory/context/其他 skills，无法归因到单技能。加强单变量对照、数据划分防泄漏与样本量评估。 | 发布证据能解释该技能贡献；反复筛选同保留集不能无限复用；重要结论附适用范围和区间。方案 10.4、18；[release_gate](D:/PAI/ForgeAgent/backend/forgeagent/evaluations.py:245)。 |
| 40 / P2 | **灰度分配、退役与回退【差距】**。active/retired 已有，但无灰度指派和在线退化触发；发布审核字段与包内容一起写入 data。分离不可变包、发布记录和分配策略。 | 历史 Run 不变；新 Run 可小流量用新版本，退化可撤回分配且保留证据。方案 10、21；[release_skill](D:/PAI/ForgeAgent/backend/forgeagent/api.py:678)。 |

## F. MCP/A2A 与真实外部效果：41–46

| 编号/优先级 | 现状与可升级内容 | 最低验收与依据 |
|---|---|---|
| 41 / P1 | **真实协议协商与互操作矩阵【差距】**。legacy SDK 和新版本手写 adapter 已分离，尚无真实服务矩阵；MCP server identity/schema/capability 缓存、A2A Agent Card 和认证协商不完整。逐版本固定规范与测试服务。 | 各版本明确可调用/不可调用方法；协商失败无效果发送；同主机不同主体缓存不串用。方案 12；[dispatch_remote](D:/PAI/ForgeAgent/backend/forgeagent/remote.py:156)。 |
| 42 / P1 | **让远程工具真正进入模型目录【差距】**。模型 Tools 只含本地工具；remote action 由用户 API 注入，不是模型按连接能力发现和调用。建立受控 ToolRegistry，持久绑定发现 schema 和版本，动态裁剪目录。 | Agent 可提出获准远程工具动作，schema 验证和审批不弱化；工具列表更新产生 manifest revision。方案 7、9、12；[prepare_action](D:/PAI/ForgeAgent/backend/forgeagent/service.py:390)、[remote_action](D:/PAI/ForgeAgent/backend/forgeagent/api.py:768)。 |
| 43 / P1 | **效果分类、下游幂等、CAS 与对账插件【差距】**。所有远端调用统一 external_write，固定 JSON-RPC id 不等于下游业务幂等。声明 read/write/idempotent/conditional/queryable 契约；绑定目标真实资源版本，提供工具级 reconcile。 | 模拟效果成功但响应丢失时，端侧计数为 1；不具备查询/幂等的效果正确等待人工；工作区 digest 不冒充远端 ETag。方案 7、13.5；[prepare_remote](D:/PAI/ForgeAgent/backend/forgeagent/remote.py:115)、[authorize_remote](D:/PAI/ForgeAgent/backend/forgeagent/remote.py:101)。 |
| 44 / P1 | **远程取消/轮询的 durable 控制状态【差距】**。输入已有 outbox/unknown；cancel_sent 只在 cancel 与后续 get 都成功后落库，状态查询失败后可能再次发送 cancel。poll 也优先于主 advance 的暂停判断。持久化控制请求/响应、重试契约和 deadline。 | cancel 请求与查询分别注入故障，不重复未声明幂等的控制效果；暂停、取消和待输入的先后行为可解释。方案 12.2；[poll_remote](D:/PAI/ForgeAgent/backend/forgeagent/remote.py:209)、[advance](D:/PAI/ForgeAgent/backend/forgeagent/worker.py:148)。 |
| 45 / P1 | **回调签名、密钥作用域和 Inbox 消费【差距】**。回调签名只覆盖 timestamp+body，provider/message_id 来自路径/头；全局 secret 共用。Inbox 只记录 observation，没有完整 consumer 状态投影链。将 tenant/provider/message ID/任务关联一并绑定，按连接管理密钥。 | 修改 message_id 不能绕过去重；重复/乱序/重放回调不回退终态；真实提供方身份可验证。方案 6.3、12；[accept_callback](D:/PAI/ForgeAgent/backend/forgeagent/remote.py:317)。 |
| 46 / P2 | **远端 Artifact 代理与必要的 A2A 服务端能力【差距/条件】**。目前主要把远端 result 当 observation；无完整 artifact MIME/size/digest/受控下载处理，也未对外提供完整 Agent 服务契约。先完成调用侧，确有互操作需求再加服务端。 | 远端“成功”不直接视为本地验收通过；下载防 SSRF、超限和权限串用，必要对外接口有版本测试。方案 12.3；[remote_result](D:/PAI/ForgeAgent/backend/forgeagent/remote.py:68)。 |

## G. 数据、存储、运维与部署：47–55

| 编号/优先级 | 现状与可升级内容 | 最低验收与依据 |
|---|---|---|
| 47 / P1 | **冻结历史迁移定义【静态】**。0001 调当前 Base.metadata.create_all；0003 也引用当前 ORM 表。未来改模型会改变旧 migration 的执行结果，fresh install 与逐版本 upgrade 可能不一致。改为显式、固定 DDL 与受控 expand/contract。 | 从空库及各历史版本升级得到同一 schema；不会因最新 Python 代码悄悄改写历史迁移。方案 15、17.4；[0001](D:/PAI/ForgeAgent/migrations/versions/0001_runtime.py:11)。 |
| 48 / P1 | **核心 JSON 记录补充数据库约束【差距】**。有租户 FK/终态和 Action 不可变触发器；不少 Document 的领域关联、schema version、生命周期仍只在 JSON，Skill/Checkpoint/Manifest 内容也未全面 DB 不可变。为关键引用补 FK/唯一键/CHECK/索引，保持简单记录不拆过细。 | 跨租户/跨 Run attempt、错误 checkpoint/ref、非法 phase/wait 状态不可写入；后台维护遵守相同约束。方案 4、15；[db records](D:/PAI/ForgeAgent/backend/forgeagent/db.py:171)、[constraints](D:/PAI/ForgeAgent/migrations/versions/0002_constraints.py:9)。 |
| 49 / P1 | **S3 真实联调、异常和大对象链路【差距】**。S3 代码存在但未验收；get 缺失对象/服务错误与本地异常语义不同，put 后整对象读取验证，未有 multipart/完整 checksum/spool。按实际对象大小引入必要能力。 | 缺失、损坏、限流、上传成功但 PG 失败均有测试；对象不可用时不写 READY；spool 有容量上限。方案 15.4；[ObjectStore](D:/PAI/ForgeAgent/backend/forgeagent/storage.py:10)。 |
| 50 / P1 | **保留策略、S3 GC 与完整删除【差距】**。本地 GC 仅删终态命名空间未引用对象，所有事件/历史引用都永久保留。需要日志/快照/交付不同保留期、引用图、墓碑、S3 GC 和隐私删除流程。 | 当前及交付 evidence 引用不被清理；过期历史按政策删除；跨租户操作拒绝且可审计。方案 10、15.4；[collect](D:/PAI/ForgeAgent/backend/forgeagent/maintenance.py:25)。 |
| 51 / P1 | **旧 epoch 工作目录和孤儿容器回收【静态/差距】**。每次 claim 新 epoch 都 restore 到新目录；快照失败、verify 目录也可能累积。现有 GC 仅处理 objects，没有 workspace/container 的生命周期回收。 | 长任务多次 claim 后磁盘不线性无界增长；强杀 Worker 后容器可辨识并回收；清理不影响活跃 epoch。方案 13、17；[root/restore](D:/PAI/ForgeAgent/backend/forgeagent/sandbox.py:48)、[maintenance](D:/PAI/ForgeAgent/backend/forgeagent/maintenance.py:25)。 |
| 52 / P1 | **数据库与对象联合灾备【差距】**。没有可执行 PITR/WAL、对象备份或联合恢复演练。建立备份、恢复脚本、引用一致性巡检、RPO/RTO 测量。 | 在独立环境恢复任务、审批、账本、工作区和证据，并报告实际丢失范围；恢复不等于仅重启 Worker。方案 15.4、17.4；[compose](D:/PAI/ForgeAgent/compose.yaml:1)。 |
| 53 / P1 | **覆盖完整链路的观测与脱敏【差距】**。OTel 主要 run.advance span；Prometheus 当前为聚合 gauge，缺各阶段耗时、queue/recovery/error/cost 等。增加关联 span/links、histogram/counter 和进入遥测前的脱敏。 | trace 可跨接管关联模型/工具/验证；遥测服务故障不影响正确性；密钥/私有原文不出普通日志。方案 17；[telemetry](D:/PAI/ForgeAgent/backend/forgeagent/telemetry.py:9)、[metrics](D:/PAI/ForgeAgent/backend/forgeagent/api.py:947)。 |
| 54 / P1 | **维护任务、死信与 SLO 告警【差距】**。operations 已提供诊断/runbook，但缺有租约的维护排程、attempt/最大重试/死信、告警平台及测得的 SLO。把租约/审批/未知费用/outbox/GC 扫描做成可重入工作。 | 非法任务不无限热循环；UNKNOWN 积压有处理时限和负责人；恢复 P95 以注明硬件的实测公布。方案 6.5、17；[Job/schedule](D:/PAI/ForgeAgent/backend/forgeagent/db.py:265)、[health](D:/PAI/ForgeAgent/backend/forgeagent/operations.py:7)。 |
| 55 / P1 | **可复现完整部署与升级回退【差距】**。Compose 只有数据库；没有 API/Worker/Console/对象/观测的完整组合和发布镜像。固定镜像摘要、迁移账号与应用账号边界、反向代理/OIDC/TLS 配置，保留旧执行版本。 | 新机器按文档启动真实任务；滚动升级不损坏等待审批 Run；回退与升级失败有可执行步骤。方案 3.2、19–20；[compose](D:/PAI/ForgeAgent/compose.yaml:1)、[start-local](D:/PAI/ForgeAgent/scripts/start-local.mjs:1)。 |

## H. API、控制台与工程维护：56–62

| 编号/优先级 | 现状与可升级内容 | 最低验收与依据 |
|---|---|---|
| 56 / P1 | **替换全量 workspace 轮询与 N+1【静态】**。前端每 1.5 秒拉所有 Run/Event/Artifact；presenter 对各 Run 多次查账本/动作/权限，并读取所有 artifact 内容。拆概览与按需详情，批量聚合，事件增量更新。 | 数据量从 100 到 1万事件时响应不按全历史成倍放大；产物按需加载，慢客户端不阻塞 Worker。方案 16–17；[workspace](D:/PAI/ForgeAgent/backend/forgeagent/presenters.py:106)、[poll](D:/PAI/ForgeAgent/src/useRuntime.ts:25)。 |
| 57 / P1 | **列表分页、筛选和查询索引【差距】**。Run 有 cursor，但按 UUID 排序；动作/检查点/项目/评测/事件查询多为全部 rows 后 Python 过滤。引入稳定 `(created_at,id)`游标与服务端过滤，关键数据批量查。 | 新记录加入时分页不重漏；大租户按项目/状态查询有 EXPLAIN 与负载验证。方案 6、15、16；[runs](D:/PAI/ForgeAgent/backend/forgeagent/api.py:125)、[rows](D:/PAI/ForgeAgent/backend/forgeagent/db.py:226)。 |
| 58 / P1 | **SSE 生命周期与客户端断线续传【差距】**。服务端已按 seq 补读，但异步流内同步查询、每 0.5秒扫库；只在连接建立验证 JWT，长期连接不主动复查到期/撤权。SDK 无自动重连。 | 网络断开/慢客户端/身份过期有明确处置，客户端从最后 durable seq 续读且去重；需要时 LISTEN/NOTIFY 仅用于唤醒。方案 6.3、16；[events API](D:/PAI/ForgeAgent/backend/forgeagent/api.py:198)、[SDK events](D:/PAI/ForgeAgent/backend/forgeagent/sdk.py:49)。 |
| 59 / P2 | **产品表单与真实登录流程【差距】**。项目/连接/实验仍以 JSON 编辑器登记；OIDC 要人工粘 token。对常用任务提供有校验表单、验收选择、风险与预算预览；共享服务接 OIDC 正常登录/续期/退出。 | 普通用户无需理解内部 JSON 才能创建合法配置；身份过期不会丢草稿，403 有可操作提示。方案 16；[JsonForm/Settings](D:/PAI/ForgeAgent/src/BackendViews.tsx:8)。 |
| 60 / P1 | **前端错误、取消请求和稳定幂等键【差距】**。`api<T=any>`把结构化错误压成字符串，无 AbortController/请求超时；create/fork 重试生成新 key。useRemote 存在重叠请求/切页晚到更新风险。 | 网络超时后重试同一意图不创建第二个 Run；409 刷新后重新审查；切换任务不显示旧响应。方案 16；[api](D:/PAI/ForgeAgent/src/api.ts:1)、[useRuntime](D:/PAI/ForgeAgent/src/useRuntime.ts:43)、[useRemote](D:/PAI/ForgeAgent/src/BackendViews.tsx:6)。 |
| 61 / P1 | **状态和恢复条件显示真实数据【静态】**。progress 是 turn×10；checksFor 只看 spent，不扣 reserved；workspace 可用仅 bool(ref)，environment 部分靠默认/原因字符串；INCONCLUSIVE 被映成 not_run。显式显示未知/未检查、总预留、真实条件和子任务成本归属。 | UI 与后端阻断原因一致；无证据不显示环境已通过；不能将更多模型轮数显示为真实完成比例。方案 14、16；[run_view](D:/PAI/ForgeAgent/backend/forgeagent/presenters.py:6)、[checksFor](D:/PAI/ForgeAgent/src/runtime.ts:28)。 |
| 62 / P2 | **契约类型、适度模块化与发布质量【差距】**。worker/api 文件较大，前端较多 any/压缩单行；CI 已有基础流程。按职责提取可测试函数，OpenAPI 生成前端/SDK 类型，补 Python 类型检查、包安装检查、升级依赖矩阵和 CI 失败诊断产物。 | 先覆盖关键边界再拆文件；生成类型能发现协议漂移；锁文件可重装，2 条依赖弃用警告有明确升级验证。方案 19；[domain](D:/PAI/ForgeAgent/backend/forgeagent/domain.py:37)、[workflow](D:/PAI/ForgeAgent/.github/workflows/runtime.yml:1)、[pyproject](D:/PAI/ForgeAgent/pyproject.toml:1)。 |

## I. 故障、安全和研究验收：63–68

| 编号/优先级 | 现状与可升级内容 | 最低验收与依据 |
|---|---|---|
| 63 / P1 | **故障窗口和状态机测试【差距】**。现有 75 项测试覆盖若干竞态与故障；Hypothesis 主要用于 context 性质，尚无完整 Runtime RuleBasedStateMachine 与 kill/restart 矩阵。将 01–10 加为回归，再覆盖意图/派发/回执/对象/验证/取消窗口。 | 固定种子可复现，多次随机交错不中断不变量；方案建议的 20场景×100 次作为目标，不能把普通单测数量冒充故障门禁。方案 18.3；[runtime tests](D:/PAI/ForgeAgent/tests/backend/test_runtime.py:1)、[remote tests](D:/PAI/ForgeAgent/tests/backend/test_remote.py:1)。 |
| 64 / P1 | **独立效果 oracle 与红队用例【差距】**。可控响应已测试 UNKNOWN，但没有独立服务端效果计数、完整同租户角色矩阵和恶意验收探测。增加真实受控效果服务、重复/未授权效果计数、prompt/tool/skill 注入和逃逸攻击用例。 | 效果计数来自服务端而非 Action 状态；任一安全失败不以更高成功率抵消。方案 13、18；[test_auth](D:/PAI/ForgeAgent/tests/backend/test_auth.py:1)、[test_remote](D:/PAI/ForgeAgent/tests/backend/test_remote.py:215)。 |
| 65 / P1 | **真实环境互操作发布门禁【差距】**。本轮 Docker 3项跳过；真实模型、S3、协议服务无本轮验证。建立可选有预算 nightly 集成任务，保存精确版本和环境报告；协议与沙箱关键测试在发布门禁不能跳过。 | 至少一条真实模型→修改→容器测试→patch→证据链；PG受支持版本/真实协议端点/S3矩阵通过。方案 20；[CI](D:/PAI/ForgeAgent/.github/workflows/runtime.yml:1)、[sandbox tests](D:/PAI/ForgeAgent/tests/backend/test_sandbox.py:1)。 |
| 66 / P2 | **真实任务集和外部 benchmark【差距】**。数据集/配对调度已实现，没有真实 60开发+60隔离任务，也无官方 benchmark adapter。按仓库/问题族划分固定 case、环境、隐藏测试和预期处置，再逐项接外部评测。 | 模型智能数字只来自真实任务；fixture 只验证机制；SWE-bench/Terminal-Bench/SkillsBench 按实际环境选择并锁定版本。方案 18.1–18.2；[evaluations](D:/PAI/ForgeAgent/backend/forgeagent/evaluations.py:35)。 |
| 67 / P2 | **完整 Harness 消融开关【差距/条件】**。已有 full/elide、memory、recall、skills 配置；未有 planning、工具形式、摘要/staged、action fusion、delegation 对照和受控语义漂移实验。按研究问题逐项做开关。 | A–F及逐项移除均共用安全/Verifier；无故障与注入故障分开；融合保留子 receipt，未确认收益不默认启用。方案 18.4/18.6、21；[Harness](D:/PAI/ForgeAgent/backend/forgeagent/domain.py:65)。 |
| 68 / P2 | **统计、时延和全成本研究报告【差距】**。任务聚类 bootstrap、配对差异已正确起步；仍缺错误分布、恢复/人工干预/重复效果指标、sandbox/storage/辅助费用、暂停前后明确时间语义与预注册实验治理。 | 原始 case/config/seed/commit/模型和环境可追溯；包括失败支出，CI不足时结论为证据不足；控制多候选筛选与样本量，不承诺固定20任务足够证明2%非劣。方案 18.5、20.3；[report](D:/PAI/ForgeAgent/backend/forgeagent/evaluations.py:150)。 |

## 推荐实施顺序

1. **先修补既有不变量：01–10，加 63/64 的对应回归。** 01–04 是首批门禁；05 是交付真实性；06/07 使任务能在失败与新增输入后继续合理执行。不要以新增摘要或多 Agent 功能推迟这些修复。
2. **打通真实仓库任务：19–24、27/28/34、41–45、65。** 先完成一个真实 Python 仓库及一个真实 JS 仓库的受控链路；外部写效果先使用可计数测试服务。
3. **长任务与扩容可靠性：11–18、25/26、47–58。** 优先解决阻塞 I/O、全量轮询和每 epoch 目录累积，再测20个活动 Run。共享部署同时完成角色权限、沙箱隔离、备份与升级演练。
4. **提升上下文与知识质量：29/30/32/35/36，再研究31/33/37–40。** 精确计数、相关性、证据和失效治理优先于向量与自动演化。
5. **研究与产品完善：59–62、66–68。** 真实配对数据决定摘要、融合、多 Agent、向量检索是否值得保留；UI完成态与成本必须依据后端事实。

不建议仅为“完整架构”立即加入 Kafka、Redis 正确性链路、Temporal 双重调度、OPA、Vault、Kubernetes、全仓向量化或默认多 Agent。当前代码已经有单一 PG 事实源和窄 Runtime，先以实测队列、政策治理和部署需求决定是否替换/引入基础设施。若更换 durable engine，要明确替换原 Kernel 对应职责，不能同时管理同一 Run。

本清单没有给出工期、性能提升百分比或成功率提升承诺；这些需要真实任务先导实验和明确资源条件。代码存在、契约测试通过、真实联调通过、研究收益成立是四种不同证据，发布说明应分别标注。
