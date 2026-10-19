# 工程与体验审查修复记录

依据用户提供的《工程与体验审查-2026-10-14》，保留原有 ForgeAgent 视觉、图标及对话体验。采用 empathy-audit 的用户/运行时/开发/支持视角和 investigate 的先复现再修复流程。不修改原始审查文档。

| 审查项 | 实施及验证状态 |
| --- | --- |
| 1 方案确认 | 保存审阅版本及方案摘要；服务端核对并记录到执行证据；11 项后端、4 项对话浏览器回归通过（含本表 2、3） |
| 2 父子取消 | Run 使用 NO KEY UPDATE，取消前按 ID 锁定并刷新子任务；真实 PostgreSQL 死锁回归由失败转通过 |
| 3 授权撤销 | 公共执行授权及创建前后均检查 active；真实 Worker 写入回归由失败转通过 |
| 4 预检竞态 | 预检/登记期间锁定输入，结果展示冻结项目与 commit；延迟响应浏览器回归通过 |
| 5 分页刷新 | 按已加载页深度重新读取服务端数据与新游标；保留任务及事件历史，删除失去访问权限的缓存；浏览器回归通过；更早事件加载后直接展示 |
| 6 OIDC 静默续期 | 独立 silent callback 完成 iframe 握手且不挂载工作区；无 refresh token 的模拟身份服务续期回归通过，真实 IdP 未验证 |
| 7 草稿账号隔离 | 按 issuer/sub/tenant 分区，不迁移旧共享草稿，退出清理当前账号；浏览器账号/租户分区及退出清理回归通过 |
| 8 调度空闲容量 | 按可执行租户的实际槽位占用轮转，允许借用空闲容量；忙租户 + 16 个闲租户由失败转通过，慢/快租户公平回归通过；实际 claim 继续约束租户与根任务额度 |
| 9 advisory 锁序 | 创建入口统一先获取同账号幂等键锁，再取 knowledge 锁；幂等记录绑定操作和来源，兼容已有记录；真实 create/fork 并发回归及跨入口 key 冲突回归通过 |
| 10 加载/空状态 | 共享 QueryState 用于待办及技能库；区分加载、失败重试和真空状态，浏览器回归通过 |
| 11 用户状态与接入模板 | 分离机器 phase、reason_code、用户说明及下一步；模型配置入口、完成说明、Python/Node 独立验收示例下载及逐字段校验；14 项相关后端回归通过 |
| 12 手机触控尺寸 | 项目选择、接入、发送、搜索至少 44px；修复手机搜索的可访问名称；提高输入辅助文字字号；浏览器尺寸回归验证 |
| 业务拆分与关键 API 类型 | 对话路由、共享授权依赖、槽位调度及状态说明分离；调度模块纳入执行版本绑定；AuthConfig、ConversationView、完整 RunView 及嵌套契约生成前端类型，Execution 改用生成类型，api 默认 unknown。旧管理页动态 JSON 仍逐步收紧 |
| 测试配置隔离 | autouse fixture 固定模型配置和费用/凭证；仅显式 opt-in 的 release 模块使用真实模型。人为设置 stream=true / token_count=false 后，77 项关键回归通过，1 项吞吐用例已单独执行 |
| 正确性/性能分离与平台边界 | 20 任务全部完成、有效租约峰值 4，本机约 49.8 秒；JUnit 保留排队、运行、事件间隔的 P50/P95；可选独立性能门槛；平台验证边界见 testing-boundaries.md |
| 浏览器测试地址隔离 | 03e33c8：FORGE_TEST_API_URL 同时控制 Vite 代理及直接 API 请求；指定测试地址时禁止复用旧前端服务，避免连接错误的开发 API |

授权撤销约束后续模型/工具调用；已发出的外部操作仍需按现有回执和对账路径结算。测试不调用真实付费模型。OIDC 真实身份提供方、S3 和生产外部效果须与本地回归范围分别报告。

历史验证（2026-10-14）：前三批提交 a998a25、9fc2b58、295874f 的 GitHub Runtime Contracts 均通过。浏览器完整回归 28 passed；静态、构建及 OpenAPI 检查通过。后端首轮 388 passed / 11 skipped / 1 failed，失败为 Hypothesis 生成 Unicode 样本耗时健康检查，没有业务反例；保留 100 组通用 Unicode 与预算断言，仅移除该功能测试的机器耗时门槛，按报告种子复验。

## 2026-10-19 本地复验

在 Windows 上复核四批实现与新增的测试地址配置。浏览器使用独立的 review-final-20261019 租户、对象与工作目录，API 位于 8019 端口；API/Worker 使用不加载 `.env` 或普通 FORGE 环境变量的 Settings，无真实模型凭证。保留现有 8000 端口进程及用户未提交文件。

| 检查 | 结果 |
| --- | --- |
| 后端全量 | 389 passed / 11 skipped / 0 failed；JUnit：`.forge/review-backend-final.xml` |
| 浏览器全量 | 独立 API、单浏览器 worker，原有超时门槛：28 passed / 0 failed / 0 skipped / 0 flaky；约 2.6 分钟；JSON：`.forge/review-final-browser.json`，HTML：`.forge/review-final-browser-report/` |
| UI 与交互 | 10 页面 × 1440/720/390px，无根页面横向溢出和未处理脚本异常；方案更新拦截、待办加载/失败/空状态、静默续期、账号草稿、分页历史及 44px 触控区域回归通过；项目目录加载后复查首页与手机接入弹窗截图，存于 `.forge/review-evidence-final/` |
| PostgreSQL 并发及授权回归 | 随全量通过：父子取消、跨入口幂等锁序、确认版本/摘要与撤销后 Worker 禁止写入 |
| 调度正确性与观测 | 20 任务完成、有效租约峰值 4；本轮约 62.89 秒，排队 P50/P95 约 4.04/7.46 秒，任务耗时 P50/P95 约 45.25/56.45 秒；非生产性能承诺 |
| 静态、类型和构建 | Ruff、ty、Linux/Win32 进程模块类型、ESLint、Prettier、OpenAPI 类型一致性、TypeScript/Vite 均通过 |
| 安装包 | sdist/wheel 构建、独立环境导入 SDK/契约/CLI 与 `forge --help` 通过 |

浏览器首轮在同时运行后端回归和安装包检查时为 24 passed / 4 failed：两项首次页面加载超过 45 秒，两项任务在 30 秒内尚未完成。持久化记录确认后两项最终验收成功；未修改超时时间，四项定向复跑全部通过。原始报告与 trace 保存在 `.forge/review-final-browser-first/`，定向复跑报告位于 `.forge/review-final-browser-retry-report/`。

本轮 11 项跳过覆盖真实付费模型、Linux Docker/gVisor、S3、备份恢复和主机无符号链接权限等条件；跳过不记为通过。OIDC 验证使用模拟身份服务，未连接真实 IdP。本地复验不替代 Linux CI 或生产环境验收；本轮未推送提交或触发远端 CI。

实施边界：审查列出的 12 项问题已落实，业务拆分和类型强化按建议先覆盖本次关键路径，不进行全量重写。Linux CI 的 Docker 验收与 Windows 本地开发验证分别记账，未验证的真实外部系统不记为通过。
