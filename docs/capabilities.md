# 当前能力与证据

更新：2026-10-10。本表用于区分实现、机制测试、基础设施互操作与真实任务效果；不以功能数量推断质量。本轮提交及验收见 [二轮实施记录](ForgeAgent-二轮优化实施记录-2026-10-10.md)，旧结果保留在 [上一轮记录](ForgeAgent-面试建议实施记录.md)。

| 能力 | 实现位置 | 本地机制验证 | 真实环境与效果证据 |
|---|---|---|---|
| 持久执行、租约、事件重建 | worker/db/reducer/service | PostgreSQL、崩溃窗口与状态机；最终全量含原 45 秒调度通过 | 生产恢复/SLO 未验收；历史超时保留，不宣称生产高并发容量 |
| 权限与隐藏验收隔离 | auth/api/verification | 同租户权限矩阵、正文过滤、红队契约 | 真实 IdP/恶意多租户部署待验证 |
| 外部效果与 UNKNOWN | remote/remote_contracts | 独立 SQLite/HTTP oracle 提交后丢响应、TLS 协议契约 | 四协议真实服务/凭证代理待环境；不保证任意服务 exactly-once |
| Git 工作区与接入 | repository/repo_tools/ProjectWizard | 固定 commit、二进制/空文件/模式、补丁真实应用、流式上传预检 | 子模块须单独登记；真实大型仓库和 S3 上传待环境 |
| 类型化验收、有限修复回路 | verification/domain | 独立验收契约、测试保护、INCONCLUSIVE、反馈修复 | 真实模型 Python/TS 修复效果未测 |
| 模型协议、计费和 Token | model_protocol/billing/tokenization | native/structured/JSON、缓存/推理、未知费用、真实 BPE | 提供方计数/计费/缓存收益待真实 API |
| root 资源与子任务契约 | resources/service | CPU 上界、存储、墙钟、分配/join/返还 | gVisor 实际计量/隔离待 Linux；默认单 Agent |
| 检索、摘要、规划 | context/code_index/context_summary/knowledge | Python AST、TS/TSX Tree-sitter、内容摘要缓存、相关排序后有界解析、来源保留与固定检查清单 | 结构检索默认关闭；解析缓存与自编案例测量不代表模型收益；不是语义规划器 |
| 技能包与离线候选提炼 | knowledge/skill_evolution/evaluations | 包摘要、读取、来源轨迹与项目/commit 范围、负迁移审核、灰度/回退 | 未证明持续自主学习或质量增益；真实独立任务收益待测 |
| Agent 轨迹关联 | telemetry/worker/service | 根任务 trace、epoch/call/action、事务/验收/费用、白名单；内存 exporter 与最终阶段样本 | 外部 collector 待联调；SQL 操作时间未独立分离锁等待 |
| 控制台与登录 | catalog/dashboard/console | 完整权限聚合、独立分页、OIDC PKCE、SSE、错误与幂等 | 真实组织 IdP 待环境；notifications 为兼容保留字段，无外部通知投递 |
| 存储生命周期与联合备份 | maintenance/backup/storage | 本地对象、引用图、删除、隔离 PostgreSQL 恢复 | S3/gVisor/镜像发布 live 门禁未运行 |
| 评测与研究 | evaluations/research/benchmarks/pilot | 固定外部来源与重复拒绝、配对结果表、全成本/人工/负迁移、只读回放、SWE-bench 准备与离线 pilot 计划 | 现有公开历史缺陷与自编案例不是新增真实模型效果；两个外部案例及 20–30 独立任务 pilot 尚未运行 |

## 验证基线和复现

- 基线 `662605e` 的历史验收：后端 304 通过、9 跳过，Edge 14 通过。该数字不代表后续 commit 的测试结果；本轮增量验证见实施记录。
- 当前 CI 已扩大为 `uv run ty check backend/forgeagent`，并检查 Ruff、TypeScript、OpenAPI 生成、React Hooks/危险语法 lint 和 Prettier。
- 最终业务版本 `089726e`：后端 340 通过 / 9 跳过，含原调度时限；Edge 单 worker 全量 18 通过。最终阶段样本及在线构建失败后离线 wheel/隔离安装结果见二轮实施记录。
- 普通 JSON 2 MiB，bundle 专用上传 16 MiB，展开工作区约 8 MiB。见 [接入说明](project-onboarding.md)。
- 使用 `npm run start:local` 启动；使用 `npm run test:backend`、`npm run test:ui` 验证。模型与容器未配置时只运行机制自检，不计为模型修复效果。

真实发布要求见 [研究与发布验收](research-acceptance.md)；部署组合与回退见 [运维部署](operations-deployment.md)。旧方案、旧验收报告和资料摘录保留历史语境，当前结论以本表、实现和对应 commit 的实际运行记录为准。

案例、检索单变量配置和测量局限见 [编码案例复现](coding-cases-and-retrieval.md)。
