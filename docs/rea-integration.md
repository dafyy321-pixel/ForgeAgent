# REA 静态分析接入

这次接入固定为 `rea-agents@6.4.0`，与本地 `D:\PAI\github\rea` 源码文档核对，并使用已发布包的真实 CLI 联调。只开放惰性 JavaScript/Electron 源码分析、模块分页和特征追踪，不执行参照程序。完整原生 Evidence 保存在对象存储中，REA 的 ID、authority、confidence、environment、coverage、limitations 和 unknowns 不改写为 ForgeAgent 的事实或验收结论。

## 配置

```powershell
npm run rea:install
$env:FORGE_REA_RUNTIME_DIR = 'D:/PAI/ForgeAgent/.forge/rea'
$env:FORGE_REA_TARGET_ROOTS = '["D:/PAI/reference-apps"]'
```

安装器使用仓库内的锁文件运行 `npm ci --ignore-scripts`，不会运行 `rea setup`、下载 native provider 或修改个人 Agent 配置。Node 需满足 REA 的要求：22.19+、24.11+ 或 26+ 的受支持版本。API 和 Worker 必须使用相同配置和只由管理员维护的本地 runtime 目录。

管理员还需通过既有 `POST /v1/authorization` 为任务创建者授予 `reference.read`；该端点替换 capabilities，更新时应保留仍需使用的原权限。升级不会自动为既有账号扩权。

在“工具连接 → REA 参照目标”登记属于某项目的目录，或调用：

```http
POST /v1/reference-targets
{"project_id":"my-project","label":"Reference app v1","path":"D:/PAI/reference-apps/app-v1"}
```

目录必须在管理员配置的范围内。链接、目录逃逸、大文件和超大快照拒绝；`.git`、`.env`、`.ssh`、`.aws`、`.forge`、`.venv`、`node_modules` 等被排除，覆盖信息随目标返回。首期最多 1000 个文件、单文件 2 MiB、总源文件 8 MiB，并受对象通道限额约束。因此这是受控源码快照分析，不能声称覆盖完整已安装应用及依赖。

目标 ID 由注册内容决定，相同注册可重复提交。修改原目录不会改变已固定快照；新版本需重新登记。停用目标会阻止后续调用。目标注册是管理员共享项目资产，其保留期独立于某个任务；任务的分析结果按既有知识擦除流程删除。

创建任务时选择参照目标，并启用证据驱动恢复。API 对应：

```json
{
  "reference_targets": ["reference-RETURNED_ID"],
  "capabilities": ["repo.read", "workspace.write", "tests.run", "reference.read"],
  "harness": {"evidence_recovery": true}
}
```

上述片段需合并到正常 CreateRun 的 project_id、task、budget 等字段。子任务只能使用父任务已选目标。

## 调用与证据

- `rea.analyze {target_id}`：在独立临时目录恢复固定快照，保存完整 Evidence，再返回 REA 原生 summary view。
- `rea.view {action_id, collection:"modules", offset, limit}`：从本任务成功分析的 Evidence 获取原生模块页。
- `rea.trace {action_id, kind, value, direction}`：从已保存 Evidence 追踪 literal seed。开放 string/module/api/channel/node-id，方向 both/outgoing/incoming；无任意 shell、宿主路径、URL 或 provider 参数。
- `GET /v1/runs/{id}/reference-evidence/{action_id}`：导出完整原生 Evidence，可用于独立 REA CLI 的 inline Evidence 工作流。历史导出保留原任务绑定，不宣称证据仍与当前工作区一致。

三个工具复用 Action/Attempt、租户权限、运行 epoch、工具额度和检查点；诊断页关联 Evidence 与原始回执。目标摘要、Node 二进制和 REA 部署树的内容摘要固定进语义清单。每次派发重新核对，版本漂移、权限撤销、目标停用或任务绑定失效均停止调用。运行时目录的 `.bin` 调度器不参与摘要，因为适配器只直接执行固定 Node 与 REA 入口。

原生 Evidence 先写对象存储并关联 Attempt，再请求摘要。摘要失败时完整证据仍可由 Attempt 审计定位；已成功分析可通过导出端点获取。Worker 接管会重新执行安全静态读取，使用新的 Attempt 和额度；不会恢复动态进程或宣称 bundle 恢复了运行环境。重复分析同一目标与选择器不重置无进展计数。

## 资源和平台边界

数据库 admission 限制整个部署同时运行一个 REA 静态动作。每个动作限定总时限和输出通道，原始输出最多 16 MiB，并受现有对象大小/根任务存储额度约束；输出超限拒绝，不把截断 JSON 当完整 Evidence。宿主 CPU 按所有逻辑核同时使用的保守上界预留，缺少可靠进程族计量时保留全额。该上界可能缩短高核数机器上的分析时限。

Windows 在进程仍挂起时绑定专属 Job Object，再启动 REA；Job 限制进程族 1 GiB、最多 8 个进程并在关闭时清理子进程。POSIX 使用专属进程组；Node 堆有界，但尚未提供整个进程族的硬内存限制。Linux 生产部署如需硬内存隔离，应增加经过验证的容器/cgroup runner。命令不继承模型密钥等环境，只传递运行 Node 所需的基础变量与专属临时目录。

`GET /v1/rea/readiness` 区分静态配置就绪和动态能力。配置检查不保证任意目标都能成功分析。静态关系、空结果或 SQLite/FTS 字符串不能证明离线搜索可运行；最终仍以固定、独立的可执行验收为准。

## 验证与后续

真实测试覆盖 CLI 分析、保存后重新加载、模块分页、特征追踪、Evidence 导出、unknown/coverage 保留及惰性解析。机制测试覆盖注册范围、跨任务访问、目标停用、任务失配、超时、输出限额、取消和子进程清理。CI 安装固定运行时并执行这些测试；没有运行时的开发环境会明确跳过真实 CLI 测试。

动态观察仍未开放。按照适配评审，只有提供实际参照应用与观察场景后，才逐项加入 OS/provider runner、动态效果授权和长会话恢复。当前实现不声称 Windows/macOS 原生应用、GUI、浏览器驱动或进程注入已完成端到端验证。
