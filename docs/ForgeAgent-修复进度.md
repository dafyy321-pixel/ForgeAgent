# ForgeAgent 68 项修复进度

目标保留全部 68 项。状态根据当前实现和测试更新，部分实现及未验证项不计为全部完成。

用户要求：每完成一个大部分，在 `main` 上 commit 并推送成功后，才开始下一部分。

## 第一部分：权限、验收与恢复基础

已覆盖 01–11、13、47；16、23、63、64 部分推进。其他项继续保留在任务范围。

本部分已提交并推送：`ef0ec403846aae2ebefb9dd87e5a7ad4a1afe194`。

验证：后端完整测试 **113 passed、3 skipped、2 warnings，27.14 秒**；Ruff、TypeScript 与生产构建通过；**7 项 Edge 前端测试通过，47.4 秒**（真实本地 API/Worker 的 5 项集成测试及 2 项界面回归）。真实 Docker 的 3 项跳过不能计为通过，真实模型、S3、gVisor 与外部协议联调未验证。

使用注意：Worker 验收现在需要 Git；共享成员由管理员通过 `/v1/authorization` 分配 `project_permissions`，如 `{"project-id":["read","create","control","edit","fork","approve"]}`。审批不会因任务所有权而自动获权。设置保存必须提供 `expected_revision`；旧任务发生执行语义漂移时，当前先使用 fork，明确迁移与旧执行器属于 12 的后续工作。

## 第二部分：Runtime 与恢复

本部分已提交并推送：`b4955c453f02eb382487494440809c87af960bf5`。

完成 12、14–18 的实现与本地机制验证，并补充 63 的发布/恢复故障窗口。全量后端 **133 passed、3 skipped、2 warnings，80.52 秒**；其后新增的心跳与 SSE 延迟回归通过，Runtime 专项 **22 passed，52.25 秒**。Edge 前端 **7 passed，40.1 秒**；Ruff、TypeScript、生产构建与 git diff --check 通过。

包含真实 PostgreSQL 下 20 个活动 fixture Run 的有界调度，跨 Worker 提供方/沙箱准入机制、慢租户占用上限，子预算与 deadline/join，延迟对象存储下的租约心跳，延迟数据库下的 SSE 响应，工作区上传/数据库提交/restore 回退故障，以及真实子进程的归档执行。fixture 和模拟延迟不计为真实模型、S3 或 gVisor 验收；这些仍由 49/65 等项目保留。

操作和限制见 [Runtime 升级与恢复](runtime-upgrades.md)。支持显式迁移和已捕获归档执行；归档前 legacy 任务无旧源码时需迁移或 fork。共享 Worker 必须使用一致的 provider/sandbox 总额度；子预算是 root 内的预留，不是新增额度。

## 第三部分：真实仓库与验证能力

本部分已提交并推送：`e3014c3f13020f0a10a4e9504784d1dcf3f51208`。

19–26 的代码与本地验证已完成。全量后端 **172 passed、4 skipped、2 warnings，129.50 秒**；之后补充退出码分类、无 checkout 的固定 worktree 与边界检查，仓库/工具/验收专项 **46 passed、1 skipped，23.70 秒**；再补充子任务共享 root 资源、manager 路径/gVisor 门禁与凭据代理，验收专项 **27 passed，3.01 秒**。Edge 前端 **7 passed，58.4 秒**；Ruff、TypeScript、生产构建与 git diff --check 通过。

用户确认暂无 Linux Docker/gVisor、S3 桶或模型 API，先完成代码和本地验证。上述 4 项跳过包括 3 项真实 Docker 测试与 1 项 Windows 符号链接测试。依赖镜像、manager 与 gVisor 的真实部署仍待环境验收；mock 仅验证协议和契约，不能计为真实执行通过。操作与边界见 [固定仓库与验收](repository-verification.md)。

## 第四部分：模型、上下文与成本

本部分已提交并推送：`b6d337cbe53a65d83e464c6c9a66eb03ffe1670a`。

27–34 已实现代码与本地契约。全量后端 **208 passed、4 skipped、2 warnings，121.97 秒**；模型/恢复/远程专项 **47 passed，10.67 秒**；最后的日志元信息修正后模型专项 **24 passed，3.67 秒**。Ruff、git diff --check、生产构建和 TypeScript 通过。真实 API、收费、token count 兼容性与缓存经济性未验证，用户要求先完成本地验证，保留 65 的真实环境门禁。详见 [模型、上下文与成本](model-context-cost.md)。

## 第五部分：Memory、Skills 与演化

本部分已提交并推送：`f68e2005f668e12003f2a40b3ee7f1f4bd529038`。

补充修正：迁移 0006 仅对已登记、全 root 已结算且无活动租约/未知效果的删除开放终态和动作正文擦除，保留正常不可变约束。真实 Worker 完成并产生文件动作后再擦除的回归、知识与迁移/不可变约束组合 **16 passed，15.84 秒**；SQL NULL 与 JSON null 均明确处理。此修正验证并单独推送后再进入第六部分。

补充修正已提交并推送：`6f5d4b1c423beec6f24fff70e653813b95478b33`。

35–40 的代码与本地机制验证已完成。全量后端 **221 passed、4 skipped、2 warnings，184.90 秒**；知识生命周期专项 **13 passed，5.26 秒**，知识/评测/迁移组合 **20 passed，17.89 秒**。Ruff、git diff --check、TypeScript 与生产构建通过。此前同时运行全量和专项时，20 Run 调度回归超过 45 秒上限；隔离重跑 **30.12 秒通过**，随后完整回归通过，未放宽测试上限。检索/撤回/删除血缘、渐进披露与包导出、离线候选、单变量发布门禁/保留集治理、灰度与回退均已接入。操作、历史保留和物理删除边界见 [知识生命周期](knowledge-lifecycle.md)。暂无真实模型/S3 环境，不把本地机制验证当技能收益或远程删除实测。

## 第六部分：MCP/A2A 与外部效果

本部分已提交并推送：`5acd8f32bf689cff05abcfec7b8a90d61a802def`。

41–46 已实现代码与本地机制验证。全量后端 **238 passed、4 skipped、5 warnings，168.98 秒**；远程/Runtime 组合 **59 passed，92.06 秒**，增强本地 TLS 验证后协议专项 **16 passed，9.75 秒**。Edge 前端 **8 passed，42.5 秒**，包括真实 API/Worker 集成和远程目录/选择回归；Ruff、TypeScript、生产构建和 diff --check 通过。早期完整回归的旧测试依赖未审核连接、取消时退回 WAITING，已改为审核连接前置和保留 CANCELLING 的断言，最终完整回归通过。

新增连接发现/认证/schema 绑定、原生工具目录和 Runtime 动作链路、效果分类/下游业务键/CAS/只读对账查询、持久取消 Outbox、reviewer/form schema/输入版本绑定、连接级全范围签名和 Inbox 消费、受限 Artifact 下载。四种协议均使用真实本地 TLS HTTP socket fixture；这不能视为外部服务互操作通过。旧 MCP/A2A SDK 的 3 条弃用 warning 与既有 2 条测试依赖 warning 保留。真实外部身份/协议环境仍待联调。操作与支持范围见 [远程协议](remote-protocols.md)。

同时补正第 36 项实际工作区租户目录摘要不一致的问题，改用 Sandbox 路径生成函数，并限制删除后对象重新发布、使上传与删除互斥且不阻塞租约心跳。实际文件工作区已完成任务擦除及禁止迟到对象发布专项 **2 passed，4.05 秒**；这项路径修正发生在最后完整回归启动后，专项覆盖最终代码。

## 第七部分：数据、运维与部署

48–55 的代码和本地机制已实现。完整后端 **260 passed、5 skipped、5 warnings，154.20 秒**；最后增强财务关联、唯一索引和删除保护后，迁移/Runtime/知识/记录完整性 **63 passed，27.50 秒**；新增旧 epoch 与租户容器边界后运维/存储专项 **18 passed、1 skipped，4.74 秒**。Ruff、diff --check、TypeScript 与生产构建通过，部署/collector/workflow YAML 可解析。Edge 单 Worker 全组 **8 passed，47.2 秒**；双 Worker 首轮跨新浏览器页面加载 8 秒超时（7 通过、1 失败），任务本身已经成功，单 Worker 重跑全部通过。最后财务跨 root/控制重绑定专项 **6 passed，6.74 秒**，联合恢复按最终迁移重跑 **5 passed，8.60 秒**。

真实本地 PostgreSQL 联合备份恢复演练 **5 passed，4.13 秒**：同一导出 snapshot、独立源库/目标库、对象逐字节校验、引用图/迁移号、原 owner/ACL/RLS、只新建不覆盖。测试数据库已精确清理，既有运行数据库未覆盖。客户端来自 PostgreSQL 官方分发，仅置于忽略的本地工具目录。

实现 0007 记录/模型/控制/财务不可变规则、同租户 JSON 关联和唯一索引；有界 spool/分片 S3、稳定脱敏错误；分类引用图和历史版本 GC；管理员 root/派生血缘擦除；旧 epoch/verify/stage/backup 与标签/租户/截止时间绑定容器回收；持久维护 job/租约恢复/五次上限/死信/审核重试；完整 operation 遥测与 SLO/alert 事实；runtime/manager/web 镜像、完整部署组合、私有凭据/TLS/OIDC/可选 MinIO/collector、备份升级与回退手册。慢存储心跳回归改为显式阻塞读取，避免上传改用流式 verify 后任务提前结束导致测试时序误判。

真实 S3、Linux Docker/gVisor、镜像构建发布与生产监控告警投递未验证。真实 S3 测试要求显式 FORGE_TEST_S3 与隔离 bucket 确认；备份演练默认 opt-in，已在本地单独启用通过。完整回归的跳过不计为真实环境通过。操作见 [数据与部署运维](operations-deployment.md)。

## 第八部分：API、控制台与工程维护

56–62 已实现代码和本地验证：签名 keyset 分页与 SQL 权限过滤、0008 查询索引、轻量 revision 同步、焦点 SSE、有界窗口、按需产物/技能正文、动作账本批量查询和 SQL 指标聚合；SDK/浏览器去重续传、有界退避、令牌到期/撤权；结构化错误、取消/超时、不确定写入的跨刷新稳定幂等键；默认字段表单和高级 JSON；OIDC 授权码/PKCE、续期与 callback；真实预算预留/未知账单/环境状态/INCONCLUSIVE；生成 OpenAPI 契约、ty 检查和隔离 wheel 安装。

Edge 单 Worker **13 passed，54.7 秒**，包含真实 API/Worker、异步设置、发布证据、请求取消、幂等键跨刷新，以及本地 OIDC 发现/授权码/PKCE 回调和原路由恢复。Python wheel 独立环境安装后 SDK/契约/CLI 导入与 `forge --help` 通过。0008 后本地 PostgreSQL 联合恢复 **5 passed，7.29 秒**。Ruff、TypeScript、生产构建、生成类型检查、ty、sdist/wheel 构建通过。

完整后端首次 **279 passed、1 failed、6 skipped**，本轮异常耗时约 36 分钟，失败为 20 任务调度 45 秒超时；单独重跑 **1 passed，36.34 秒**。最终完整回归 **281 passed、6 skipped、5 warnings，191.29 秒**。指标聚合新增专项发现 Decimal/float 转换问题，已修正并纳入完整回归。

共享 OIDC、真实模型/S3/gVisor 仍待环境联调。第七部分已提交推送并核对 `66bd48c214ea06729620b2ed605f6a11aa502326`。接口、窗口、游标密钥与登录配置见 [API 与控制台](console-api.md)。

## 逐项状态

| 编号 | 项目 | 状态 | 实现与证据 / 尚待内容 |
|---|---|---|---|
| 01 | 补齐同租户内的资源与操作授权 | 已验证修复 | 运行资源统一权限入口；项目授权；审批 reviewer 检查；调用者作用域的幂等键；test_security_regressions.py |
| 02 | 将隐藏测试移出公开项目、检查点和事件投影 | 已验证修复 | 新验收正文存内部对象；公开项目/检查点/事件过滤历史正文；隐藏验收输出隔离；test_security_regressions.py |
| 03 | 统一验证入口与成功态的账单结算门禁 | 已验证修复 | API 与 Worker 共用账单/Token/动作/子任务/未消费响应门禁；test_security_regressions.py |
| 04 | 修复取消与中断模型账单对账后的任务卡死 | 已验证修复 | 取消意图不被账单未知状态覆盖；对账自动调度；重复取消记事件并恢复调度；test_security_regressions.py |
| 05 | 修正空文件创建/删除补丁，验证补丁确实可应用 | 已验证修复 | Git 生成补丁；git apply --check 和应用后完整文件树比较；空文件/无末尾换行/CRLF/中文路径回归 |
| 06 | 验收失败后支持受预算约束的修复回路 | 已验证修复 | 预算内有限修复；验收失败反馈；硬约束失败与环境不可判定分开；test_delivery_regressions.py |
| 07 | 新输入后重新判断完成提议和 TaskSpec revision | 已验证修复 | 新输入清除旧 completion/验证，增加 input_revision；过期持久响应丢弃；模型上下文消费回归 |
| 08 | 明确 resume 的 checkpoint 参数语义 | 已验证修复 | resume 明确继续当前状态；checkpoint 参数要求使用 fork，返回 CHECKPOINT_FORK_REQUIRED；回归通过 |
| 09 | 打通技能发布 UI 的评测门禁 | 已验证修复 | 后端发布证据选项和门禁原因；前端选择实验/配置/审核说明；Edge 回归通过；未声明真实付费模型联调通过 |
| 10 | 修复设置页异步状态与草稿同步 | 已验证修复 | 异步数据同步、dirty 草稿、CAS revision、冲突提示和保存反馈；API 与 Edge 回归通过 |
| 11 | 事件重建与版本化 reducer | 已验证修复 | schema v2 增量事件与摘要链；兼容 v1；检查点绑定已提交事件；重建和 live 对照；Worker 恢复检查 |
| 12 | 粒度合理的语义绑定和升级 | 已本地验证 | 分阶段模块/执行依赖固定，排除 API/UI/配置文本；显式迁移、审计分段、旧意图/审批/证据失效；归档摘要与依赖校验、真实子进程执行 |
| 13 | 明确已持久化模型响应的消费机制 | 已验证修复 | 先持久 RECEIVED 响应，再原子应用决策；崩溃回归证明不重新请求/不重复 intent；未知费用先对账 |
| 14 | 调度并发、公平性和背压 | 已本地验证 | 有界槽与占用公平；租户/root 并发；独立 PostgreSQL 会话的跨 Worker provider/sandbox 准入；20 活动 fixture Run；真实负载性能留待联调 |
| 15 | 移出 asyncio 中的阻塞 DB/对象操作 | 已本地验证 | Worker/SSE/远程/API 的同步事务与对象/文件/Git 操作移到线程；检查点无重复 S3 上传；请求发布在行锁外复查；子任务复用固定引用；慢存储心跳和慢 DB SSE 回归 |
| 16 | 加强工作区原子提交与失败隔离 | 已本地验证 | 成功动作才发布 workspace；从提交快照重建；上传/DB 提交失败可安全恢复；restore 安装和回退同时失败保留备份，成功后清理残留 |
| 17 | 进展与失败分类 | 已本地验证 | 有界持久 journal、失败类别/签名、无新证据上限；不同写入不能重置，新成功证据重置重复指纹，input revision 独立处理 |
| 18 | 子任务 join 与预算契约 | 已本地验证 | required/optional、独立 deadline、原子 cost/token 分配与返还、必需交付物与集成；父成功前所有子账单和效果结算；父集成后再验收 |
| 19 | 完整 Git 工作区 | 已本地验证 | 固定 commit/bundle、独立 worktree、SHA-1/SHA-256、binary/执行位/空文件/安全链接；Git 原始字节与实际补丁应用；Windows 符号链接测试显式跳过，gitlink 注册独立项目 |
| 20 | 最小但实用的仓库工具集 | 已本地验证 | 范围/base64 读取、字面搜索、符号、apply_patch、删除与移动；摘要 CAS、scope、完整树校验；失败回退回归 |
| 21 | 可用的依赖/构建与长期沙箱 | 已实现，本地契约验证 | lock 摘要、固定依赖镜像/标签、offline 镜像示例、Docker layer/有界 session cache、最长 300 秒 session；实际镜像与 Docker 执行待环境联调 |
| 22 | 类型化验收和交付契约 | 已本地验证 | 类型化文件/摘要/输出条件、退出码策略、必需产物枚举、发布效果回执门禁；自由文字 criteria 仅作人类说明 |
| 23 | 环境失败归为 INCONCLUSIVE | 已本地验证 | 未配置退出码/超时/截断/基础设施错误为环境不可判定；既有测试、新增 conftest、锁/Node/CI/注册治理配置受保护 |
| 24 | 补齐 EvidenceBundle | 已本地验证 | v2 时间/耗时、task/input/artifact/semantic 绑定、固定仓库/镜像/lock/实际沙箱配置、构建与独立日志产物；隐藏输出脱敏 |
| 25 | root CPU、存储和墙钟资源总量 | 已本地验证 | root 原子 CPU 上限预扣/结算、崩溃保守扣费、session 生命周期扣费；父子共享对象存储计量与摘要去重、root 墙钟截止；容器实际计量待联调 |
| 26 | 共享执行的隔离与凭证架构 | 已实现，本地协议验证 | 独立 manager RPC、共享部署禁止直接 Docker、强制 runsc、精确请求签名/短期凭据、持久幂等回执、受控只读凭据代理；真实 gVisor/TLS 部署待联调 |
| 27 | 按能力提供模型适配器 | 已本地验证 | Responses/Chat/Messages 原生目录、strict schema、结束/拒绝/截断状态、call ID 和加密 reasoning 恢复；真实协议待联调 |
| 28 | 完整计费和未知用量判定 | 已本地验证 | pinned 普通/缓存/写入/输出费率；推理不重复计费；不完整/型号/tier/额外 hosted tool 费用保留 UNKNOWN；确定性摘要无付费辅助调用 |
| 29 | 真实 tokenizer 与协议预算 | 已本地验证 | tiktoken BPE、完整 provider count 请求；不可用先暂停；Chat/兼容端点保守上限；真实计数接口待联调 |
| 30 | 上下文的相关性、关键事实与 Journal | 已本地验证 | 相关路径/关键词、固定事实/输入/失败、错误引用；真实 Worker 嵌套回执回归 |
| 31 | 分阶段结构化摘要 | 已本地验证 | 确定性 phase summary 和大成功回执引用摘要；约束/输入/证据绑定和篡改回归 |
| 32 | 统一 ObservationEnvelope 与日志引用 | 已本地验证 | 本地动作/远端轮询同一 envelope；原引用、时间/行号/截断/64KiB UTF-8 范围读取；root 存储额度 |
| 33 | 稳定前缀与缓存/压缩经济性 | 已实现，本地测量契约验证 | 稳定 system/tools、租户/Run cache key；实际读写收益与压缩估算分开，允许负收益；真实成本实测待 API 环境 |
| 34 | 模型故障和安全重试策略 | 已本地验证 | max_retries=0；429 持久 Retry-After 秒/日期和有界退避；超时/5xx不重发；原 resp 只读查询和人工结算 |
| 35 | 先全文/关键词的 Memory 检索 | 已本地验证 | 英文词/中文二元组、相关性/范围、时间/revision、冲突引用（含召回上限外）、显式 supersession；memory=false 不召回 |
| 36 | 删除血缘与活动任务撤回策略 | 已本地验证 | 撤回决策/旧 observation 隔离；memory/skill/Run family 闭包、并发创建事务锁；隔离和待重试物理清理；保留结算审计，S3 版本删除仅协议 mock |
| 37 | 技能包与渐进披露 | 已本地验证 | UTF-8/base64/执行位资源、包/资源摘要、metadata-only context、skill.read 实际 Worker 链路、frontmatter/manifest ZIP 导出和路径边界 |
| 38 | 失败聚类和离线候选生成 | 已本地验证 | 持久 failure category/signature、来源摘要与 follow-up observation 工具轨迹；零收费确定性候选，禁止隐藏验收日志/held-out/已擦除来源；收益仍需真实评测 |
| 39 | 技能评测的因果归因和数据治理 | 已本地验证 | 目标单技能差异、同 Harness/memory=false、一次性不重叠 holdout、列表隔离和派生来源限制；真实模型门禁保留 |
| 40 | 灰度分配、退役与回退 | 已本地验证 | tenant/Run/skill 哈希 cohort、固定分配日志、退役阻止新选择、同名且曾评测发布的回退、追加审核历史；不热改已固定 Run |
| 41 | 真实协议协商与互操作矩阵 | 已实现，本地 TLS 契约验证 | 四版本独立发现/版本/扩展/schema/认证矩阵、公网固定 IP/TLS；外部服务、OAuth 代理待联调 |
| 42 | 让远程工具真正进入模型目录 | 已本地验证 | 显式选择、固定目录、native/structured/JSON schema、provider call ID 和真实 Worker 意图/观察链路、当前撤权/停用检查 |
| 43 | 效果分类、下游幂等、CAS 与对账插件 | 已本地验证 | read/write/irreversible、独立审批、Runtime 业务 key 注入、必填下游版本、幂等 API、审核只读查询证据；不推断下游幂等保证 |
| 44 | 远程取消/轮询的 durable 控制状态 | 已本地验证 | 独立取消 Outbox、即时 ack、崩溃/超时不重发、原 handle 查询/终态结算、暂停恢复、取消优先、reviewer/schema/input 摘要绑定 |
| 45 | 回调签名、密钥作用域和 Inbox 消费 | 已本地验证 | tenant/connection/message/time/body 摘要全范围 HMAC、独立连接密钥、冲突去重和消费一次；仅唤醒查询、不得推进验收成功 |
| 46 | 远端 Artifact 代理与必要的 A2A 服务端能力 | 已本地验证 | 已存响应索引、允许列表/固定 IP/无重定向、跨 origin 不带凭据、限流限大小/配额/摘要、未验证产物；当前出站产品未开放 A2A 创建服务 |
| 47 | 冻结历史迁移定义 | 已验证修复 | 0001 固定 SQL、0003 显式 DDL；隔离 PostgreSQL schema 中依次迁移并对照当前列/类型/租户策略 |
| 48 | 核心 JSON 记录补充数据库约束 | 已本地验证 | 0007 同租户 JSON 关联、attempt/turn 唯一、不可变模型/证据/财务/控制身份、禁止账本删除；隔离迁移和真实 Worker 回归 |
| 49 | S3 真实联调、异常和大对象链路 | 已实现，本地契约验证 | 64KiB 流、有限 spool、分片成功/abort、流式 SHA256、错误脱敏分类、短期 token；真实 S3 显式测试待环境 |
| 50 | 保留策略、S3 GC 与完整删除 | 已本地验证 | recovery/deliverable/knowledge/audit 引用图、强引用保留、24h 孤儿策略、S3 所有版本/marker 分批 GC、root 血缘擦除与重试；真实 S3 待联调 |
| 51 | 旧 epoch 工作目录和孤儿容器回收 | 已实现，本地机制验证 | 保留当前 epoch、回收旧 epoch/verify/stage/backup、Git remove；Docker owner/storage/tenant/deadline 标签和签名 manager 回收；真实容器待环境 |
| 52 | 数据库与对象联合灾备 | 已本地真实演练 | PostgreSQL 导出 snapshot + 引用对象 + SHA256 manifest；新隔离库恢复、owner/ACL/RLS/引用/schema/对象校验；S3 备份待环境 |
| 53 | 覆盖完整链路的观测与脱敏 | 已本地验证 | 模型/工具/验证/恢复/存储/备份 operation span、固定标签计数/耗时、禁异常正文与参数；生产 OTel 后端待部署 |
| 54 | 维护任务、死信与 SLO 告警 | 已本地验证 | 持久 maintenance job、行锁领取/租约恢复、退避/五次上限/死信/审核重试、SLO 和机器可读 alert/runbook；告警后端待部署 |
| 55 | 可复现完整部署与升级回退 | 已实现，配置本地验证 | 三个镜像 target、GHCR 摘要/SBOM/provenance、Postgres/API/Worker/manager/maintenance/TLS/OIDC/S3/OTel 组合与升级回退；实际构建发布待 Linux 环境 |
| 56 | 替换全量 workspace 轮询与 N+1 | 已本地验证 | revision/SSE 增量同步、按需正文、有界窗口、run/action 批量查询、SQL 指标聚合；查询计数回归 |
| 57 | 列表分页、筛选和查询索引 | 已本地验证 | 所有 console 集合 signed keyset catalog、身份/条件绑定、SQL 先权限后 LIMIT、0008 索引；稳定与篡改回归 |
| 58 | SSE 生命周期与客户端断线续传 | 已本地验证 | SSE 原令牌到期/每批撤权/最长连接、完整帧与去重、有界重连、动态 token、浏览器 idle timeout |
| 59 | 产品表单与真实登录流程 | 已实现，本地协议验证 | 默认校验字段表单与高级 JSON；OIDC PKCE/callback/续期/退出，Edge 本地协议通过；真实 IdP 待环境 |
| 60 | 前端错误、取消请求和稳定幂等键 | 已本地验证 | 结构化 ApiError/ForgeError、请求取消/超时、不确定写入键按调用者摘要持久保存；跨刷新回归 |
| 61 | 状态和恢复条件显示真实数据 | 已本地验证 | 无伪造百分比，真实 deadline/reserved/UNKNOWN、unknown environment 和 INCONCLUSIVE；后端最终门禁 |
| 62 | 契约类型、适度模块化与发布质量 | 已本地验证 | 公开响应契约及 OpenAPI TS 生成/漂移检查、分页/catalog/auth/events 模块、ty、隔离 wheel 安装与 CLI；CI 门禁 |
| 63 | 故障窗口和状态机测试 | 部分实现 | 已补权限、取消、模型响应落库/应用窗口、补丁与状态重建；增加上传/提交/restore、配额/心跳/SSE/归档执行回归；完整 Runtime 状态机/故障矩阵待补 |
| 64 | 独立效果 oracle 与红队用例 | 部分实现 | 已补同租户读取/控制/共享/撤权/审批/下载矩阵；独立效果计数服务和全面红队尚待实现 |
| 65 | 真实环境互操作发布门禁 | 待实现 | 按原审查清单的验收标准继续实现与验证 |
| 66 | 真实任务集和外部 benchmark | 待实现 | 按原审查清单的验收标准继续实现与验证 |
| 67 | 完整 Harness 消融开关 | 待实现 | 按原审查清单的验收标准继续实现与验证 |
| 68 | 统计、时延和全成本研究报告 | 待实现 | 按原审查清单的验收标准继续实现与验证 |

## 后续部分顺序

1. Runtime 12、14–18：兼容迁移、调度与阻塞 I/O、进度/子任务契约；13 的持久响应基础已先落地。
2. 真实仓库与验证 19–26。
3. 模型、上下文与成本 27–34。
4. Memory、Skills 与演化 35–40。
5. MCP/A2A 与外部效果 41–46。
6. 数据、运维与部署 48–55；47 已先落地。
7. API、控制台与工程维护 56–62。
8. 故障、安全与研究验收 63–68。
