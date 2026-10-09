# 故障、安全与研究验收

本轮完成代码与本地验证。真实模型、Linux Docker/gVisor、S3、组织 IdP 和外部协议环境未提供，因此不宣称真实发布门禁或外部 benchmark 已通过。

## 故障矩阵

| 窗口 / 性质 | 本地回归 |
|---|---|
| 控制、财务、事务回滚、接管随机交错；终态/取消单调；事件重建等于 live | `test_state_machine.py`：真实 PostgreSQL，12 组 × 最多 15 步，Hypothesis 保留失败样本并缩减 |
| 持久模型响应到决策应用之间崩溃；部分 intent 事务回滚；新输入失效旧响应 | `test_response_recovery.py` |
| 文件写入、对象上传、数据库提交窗口；restore 回退与清理；慢存储/心跳 | `test_runtime_upgrades.py`、`test_delivery_regressions.py` |
| 外部提交成功但响应丢失；多次接管不重发；按独立账本对账 | `test_effect_oracle.py` |
| 远程取消 ACK、超时、轮询、输入、回调冲突/重复 | `test_remote_contracts.py` |
| 同租户读取/控制/审批/下载/隐藏正文、授权撤回、JWT roles/expiry | `test_security_regressions.py`、`test_console_contracts.py` |
| 模型文本声称提权、越界删除、未登记工具 | `test_redteam.py`：确定性请求攻击，不能替代真实模型红队评估 |
| 批量动作顺序、写成功后测试失败的两个事实、关闭融合不创建 Action | `test_research.py` |

状态机发现并修复 cancel 后 pause 可将任务重新置为 PAUSED 的漏洞。取消过程中 pause 现在返回 CANCEL_PENDING，持续推进取消/对账。

独立 oracle 是 `scripts/effect_oracle.py`，仅使用标准库、独立 SQLite 和 HTTP，不导入 Runtime、事件或业务账本。业务键去重与真实请求次数分开计数，可以先提交再主动断开响应。令牌取 `FORGE_ORACLE_TOKEN`，默认只绑定 loopback；没有 reset 接口。测试数据库专用，不保存业务参数正文。

## 发布门禁

镜像发布依次依赖 `.github/workflows/runtime.yml` 完整本地契约门禁、`.github/workflows/release-gates.yml` 真实联调门禁。真实联调使用受保护的 `release-integration` 环境和 `self-hosted, linux, forge-release` runner，需要隔离 PostgreSQL/备份管理员、S3 测试桶、真实模型账号与准确费率、四个隔离协议测试服务，以及已安装 runsc 的 Docker daemon。不得指向生产数据库/服务。

环境变量和 secrets 名称见 workflow。四种协议为 MCP 2025-11-25、MCP 2026-07-28、A2A 0.3.0、A2A 1.0。私有连接文件是数组，每项包含 `isolated_test_service: true`、`connection`（tenant_id/kind/protocol/url/认证引用）、`probe`（method/params/required_result_fields）。Probe 必须经维护者审核，面向专用无业务影响测试服务。凭据保存在环境 `FORGE_REMOTE_CREDENTIALS`，按租户/origin 绑定；允许主机通过 `FORGE_REMOTE_HOSTS` 配置。完整协议覆盖失败时不得缩减矩阵以让发布变绿。

Runner 从当前 commit 启动独立 manager 进程，使用短期签名 RPC 和 runsc，测试当前代码；部署时的跨主机 HTTPS/证书/凭据代理仍需另行验收。沙箱镜像在 runner 与 manager 同一 daemon 可见，工作区目录也必须一致。直接 Docker 适配器测试与 manager RPC 测试分开执行。

`scripts/release_evidence.py` preflight 缺环境即失败。发布 JUnit 必须包含全部真实组，任何 skip、failure、error 都拒绝；事实文件绑定源 commit，记录模型 profile 摘要、实际镜像摘要、gVisor 配置及协议协商摘要。生成的 JUnit/证据 JSON 上传为该 SHA 的 artifact，再允许镜像发布。摘要仅提供完整性校验，可信来源是受保护 workflow 的实际执行链；不要把手工 JSON 当作发布证明。当前没有真实联调 artifact。

## 真实仓库任务与 benchmark

`benchmarks/forge-regressions-development.jsonl` 与 `forge-regressions-held_out.jsonl` 包含 6 个真实 ForgeAgent 历史缺陷：越权暂停、隐藏项目测试、空文件创建、隐藏检查点测试、未知账单验证、空文件删除。全部固定初始仓库 commit，验收正文与任务 goal 分离。本地回归已验证这些验收在当前代码通过。

这是公开已知缺陷的 3+3 试点，不是盲测样本；同问题族、同仓库不能用来证明外部泛化或统计非劣。正式研究应另收集跨仓库、按问题族拆分的开发/隔离保留集，预注册停止规则和目标差异，依据先导方差估计样本量。技能发布仍受既有 20 独立任务、两项目、三重复、单变量、结算与非劣/费用门禁约束；不能用这个试点绕过门禁。

`scripts/benchmark.py import-swebench input.jsonl output.jsonl --split held_out` 支持 SWE-bench 格式导入，只保留 problem statement、固定 base_commit 与独立 test_patch，不把 gold solution patch 放入任务。需要自行获取具有使用权限的数据和仓库；本轮没有下载外部任务或给出官方分数。

准备步骤：

1. 审核仓库/许可证、不可变 commit、独立验收补丁、允许修改范围和测试路径。
2. 制作包含全部依赖及必要数据库的无网测试镜像，使用 sha256 摘要；历史 ForgeAgent API 验收需要镜像内的隔离 PostgreSQL，不能依赖宿主机网络。
3. 提供环境 JSON：`image`、`argv`、`allowed_paths`、`acceptance_paths`。执行 `uv run python scripts/benchmark.py prepare reviewed.jsonl prepared.jsonl --repository <local-repository> --environment <environment.json>`。只在临时文件树应用受审 test_patch，不在宿主机执行仓库代码。
4. 将输出 `project` 发到管理员 `/v1/projects`，再以 `case` 登记版本化 `/v1/evaluation-datasets` 和配对 `/v1/experiments`。原始准备文件含内部验收内容，应限制访问，不能上传为模型上下文。
5. 将交付补丁整理为 instance_id/model_name_or_path/model_patch，使用 export-predictions 输出官方 harness JSONL。官方评分必须使用其正式环境，不能用内部 PASS 替代。

适配器遵守现有 24MB bundle、文件数和工作区限制，只覆盖可容纳且可隔离运行的任务子集；不宣称完整 SWE-bench Verified 兼容。基线失败、参考修复通过和正式外部运行仍待环境验证。

## Harness 与统计

Harness 固定在 TaskSpec/semantic binding 中。支持 context_policy、memory、observation_recall，以及：

| 字段 | 实际行为 |
|---|---|
| planning | 开关确定性的约束/路径/验收检查清单；不冒充额外付费模型规划器 |
| summarization | 开关 observation 摘要与阶段 summary；关键约束/错误仍必保留 |
| tool_form | profile/native/structured/json；按真实模型能力检查并构造请求 |
| observation_fusion | 合并 observation envelope，保留每条完整来源/内容，不能丢失错误事实 |
| action_fusion | 允许同轮确定的局部操作，依 proposal 顺序执行，每个 Action 独立权限/receipt；外部效果不能同批融合 |
| delegation | 同时限制模型目录/schema 和后端父子任务创建，不能靠模型自行遵守 |

写入成功后测试失败保留两个 Action 和已经提交的 workspace；禁止整体包装成“无效果失败”再重试。关闭 action_fusion 后，每轮最多一个操作，非法批次进入有界格式修复。

配对研究固定模型 profile、任务/仓库/验收、预算和实现版本，按同一 case 聚类 bootstrap，重复不当成独立样本。单变量比较需保持其他 Harness/Skills 相同；公开 pilot 与正式保留集不能混报。未确定费用的实验保持 awaiting_reconciliation，含零美元/Token-only UNKNOWN；不能提前冻结报告。

`research.py` 记录家族模型账本、所有动作尝试、CPU 上界、存储字节、墙钟、失败分布、人工决定数。全成本以明确的 CostAssumptions 计算，包含 CPU、存储、环境、工具和人工；缺费率/工时或未知账单时 total_cost_usd 为 null，已知部分单列。存储 byte-hour 与 CPU 是保守估计，费率假设不是发票，失败尝试仍计费。

恢复统计区分租约到期到接管、接管到首次持久进展；正常调度 quantum 不计崩溃恢复。旧 recovered 事件语义含混的记录单列排除，不伪造历史时延。未恢复的样本保留 censored/pending，不从报告中静默删除。

`uv run python scripts/research_report.py <evaluation-id> report.md` 导出报告，含 case 聚类区间、失败分布、恢复样本、完整性缺项和摘要。`scripts/local_study.py` 可生成不付费的机制试验。已保存 [本地机制试验](../benchmarks/reports/local-mechanism-2026-10-09.md) 和同名 JSON：4 配置×2 重复、1 个独立 fixture，8/8 完成；区间与费用比均不可推断，总成本不完整。这只证明实验链路可运行，不能推断规划/摘要/融合的收益。
