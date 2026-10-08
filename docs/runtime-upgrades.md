# Runtime 升级与恢复操作

Runtime 按 state/runtime/decision/effects/verification 固定执行模块及执行依赖版本；API 路由、界面、CLI 与配置文件的文本变化不直接改变执行绑定。模型、上下文、技能、验收、基线与沙箱镜像仍使用任务各自的固定绑定。授权和租户配置每次执行时读取当前值。

## 显式迁移

先暂停任务并等待 Worker 释放租约，再调用 `POST /v1/runs/{id}/migrate`，提供当前 `expected_version` 与审核 `reason`。仅支持 schema 1/2；未知账单、未结算动作与活动子任务阻止迁移。迁移保留 TaskSpec、基线、验收与已提交工作区，记录旧、新 semantic digest 和分段；取消旧未派发意图、过期旧审批、废弃未消费响应并要求重新决策、重新验收。预算原有上限保留，新安全字段使用明确默认值。

迁移后任务仍为 PAUSED，刷新版本，再调用 resume。迁移没有重写历史事件或旧 SemanticManifest；旧报告和产物保留为历史记录，但失去当前版本的验证资格。

## 使用归档执行器

新任务创建时捕获服务端当前完整 Python 模块，保存在内部内容寻址对象中。执行绑定和归档摘要在首次加载时固定，后续磁盘改动不会把已加载 Worker 伪装成新实现。旧任务在已有归档、运行环境匹配时可用 `resume` 的 `executor: "archived"` 继续：

```json
{"expected_version": 12, "reason": "使用已审核原执行版本继续", "executor": "archived"}
```

运维端使用数据库访问权限运行：

```powershell
uv run forge admin executor local TASK_ID --quanta 10
```

该命令以独立子进程加载已校验的归档模块，只领取指定任务，沿用数据库 fencing、授权、账单与验收门禁。安装的 Python、平台和执行依赖必须匹配归档；不匹配时返回 EXECUTOR_DEPENDENCIES，不自动安装或放宽版本。归档前创建的 legacy 任务没有凭空可恢复的旧源码，需显式迁移或 fork。新 Worker 不领取归档执行绑定不匹配的普通任务；取消仍可由当前 Worker 接管。

## 调度与背压

`FORGE_WORKER_SLOTS` 控制单进程工作槽；Tenant settings.concurrency 与 Budget.max_concurrent_runs 在 PostgreSQL 领取事务中控制租户和 root 并发。按租户轮换，限制每个租户的槽占用；空闲租户份额暂时保留，下一轮再领取。

`FORGE_PROVIDER_CONCURRENCY` 按 provider/endpoint/model 限制跨 Worker 模型请求；`FORGE_SANDBOX_CONCURRENCY` 限制共享数据库集群的容器调用。使用独立 PostgreSQL 会话 advisory lock，进程退出会释放；长请求不占用状态事务连接池。共享数据库的所有 Worker 必须配置一致的全局额度。提供方满额时持久调度延迟，尚未预留账单或发请求。沙箱满额返回可重试的 SANDBOX_BACKPRESSURE。

Worker 的同步事务、对象操作、本地文件操作和 Git 补丁生成在有界线程池中执行，模型和协议客户端在原异步循环中运行。检查点的完整 manifest 保存在不可变数据库记录中并校验摘要；历史对象式 manifest 保持兼容。模型请求对象先在行锁外发布，再重新检查 input/workspace/semantic/control 并预留账单。子任务复用父任务已提交的基线与验收引用，避免在父决策事务内重复 S3 上传。

## 子任务与进度

CreateRun.child_contract 支持 required、deadline_seconds、join(report/integrate) 和必需 deliverables。子任务 cost/token 预算是 root 内的明确预留分配，不是额外预算；并发分配不可超卖，未知使用仍占预留，终止后未用容量返还。并行委托请显式给出子任务预算，默认完整 Budget 不会自动扩大 root。

可选子任务失败可继续父任务。完成父任务前取消并等待尚在执行的可选任务结算，避免父任务成功后仍发生账单或效果；必需集成任务必须先合并，再验收父工作区。子任务与父任务的墙钟 deadline 取更早值，子 Worker 会独立执行截止检查。

持久 progress 保存有限 journal、证据摘要、失败类别/签名与无进展计数。新的成功读取、测试和集成证据可重置计数；不同写入参数、失败调用和重复读取不能无限重置。补充输入按 revision 重置判断；Budget.max_no_progress_turns 控制上限，最大模型轮数仍是独立限制。

工作区仅在动作成功、对象发布且数据库提交后更新。上传/提交失败恢复安全本地动作到 READY，恢复时从已提交快照重建；外部未知效果不自动重发。restore 使用 stage/backup 原子切换；安装与回退同时失败时保留备份，下一次成功恢复才清理残留。

## 验证范围

test_runtime_upgrades.py 包含真实 PostgreSQL 的 20 活动 fixture Run、公平调度、共享额度、子预算与 join、事件重建、对象/提交/restore 故障，以及真实子进程归档执行。fixture 证明运行机制，不能作为真实模型或容器性能结果。真实 S3、Linux/gVisor 与模型联调仍需对应环境验收。
