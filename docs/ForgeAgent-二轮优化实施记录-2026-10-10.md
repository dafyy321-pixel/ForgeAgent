# 二轮优化实施与本地验收（2026-10-10）

按二次面试评估分部分修改，每部分在 main 提交并推送。最终业务版本为 `089726e`。本轮没有真实模型 API、Linux Docker/gVisor 或 S3 测试环境，因此只完成代码、本地机制验证和真实实验准备；没有把 fixture、人工补丁或离线计划记为真实修复成功。

## 分部分交付

| 部分 | main 提交 | 改动与证据 |
| --- | --- | --- |
| 调度与补丁性能 | `90c5221` | 同事务复用已校验事件投影、工具工作区按需恢复、租户空领取退避；一次导入两棵 Git 树，保留实际补丁检查、应用及完整语义比较。见 [性能复盘](scheduler-performance-2026-10-10.md)。 |
| 增量代码索引 | `3374c04`、`d826d9a` | 按租户/任务/路径与内容摘要缓存语法 metadata，先对已准入工作区排序再有界解析；变更、删除及暂停清理。首次新增暂停回归失败后修正，10 项索引测试通过。见 [索引边界](incremental-code-index.md)。 |
| Agent 轨迹观测 | `1d9569c` | 根任务稳定 trace，关联 epoch、模型 call、工具 action、事务、上下文、checkpoint、验收和费用；白名单属性与事件引用。21 项相关回归通过；未联调外部 collector。见 [运行轨迹](runtime-tracing.md)。 |
| 外部案例与配对 pilot 准备 | `9ca9a3f` | 固定来源/commit、重复问题拒绝、开发与保留集隔离；20–30 独立任务离线计划，单 Agent 与结构检索单变量配对；报告与控制台展示全部尝试成本、人工决策和负迁移。26 项后端专项及浏览器比较回归通过。见 [执行准备](external-repair-pilot.md)。 |
| 有证据的经验复用 | `089726e` | 失败轨迹候选保存来源证据，默认绑定项目/commit；独立评测后扩展范围，负迁移阻断，发布历史与回退恢复范围。24 项相关回归通过。仍是确定性规则提炼，未证明自主学习收益。见 [适用范围](evidence-based-skill-scope.md)。 |

## 最终验证

以下为最终业务版本的同机本地结果；后端、调度采集与浏览器全量错开运行。没有放宽原调度断言或浏览器用例时限。

| 检查 | 实际结果 |
| --- | --- |
| 后端全量 | **340 passed / 9 skipped / 5 warnings，178.15 秒**；包含原 45 秒、20 任务、双租户、4 槽调度回归。 |
| 最终调度单独采集 | **1 passed，20.85 秒**；调用阶段含任务创建为 20.234 秒，20 项全部 SUCCEEDED。保存 3,098 条 span，丢弃 0；任务 P50/P95 为 16.186/18.697 秒，包含创建与排队。 |
| Edge 浏览器全量 | **18 passed，约 1.2 分钟**；单 worker，原 45 秒用例时限，无失败后重跑合并计数。 |
| 静态与前端发布检查 | 全后端 ty、Ruff、ESLint、Prettier、OpenAPI 类型一致性、TypeScript 与 Vite 构建通过。 |
| Python 包 | wheel 首次在线构建因 PyPI 超时失败；使用已有缓存离线构建成功，随后无依赖安装到隔离目录，`python -I` 校验 8 个模块均来自安装目录，执行源码归档包含 54 个模块及 pilot，绑定 schema 为 2。此项不替代干净机器依赖安装。 |

原始最终测量：[scheduler-final-traces-2026-10-10.json](../benchmarks/reports/scheduler-final-traces-2026-10-10.json)。其实现摘要与 `089726e` 绑定，保存每任务事件时间线、阶段原始样本与分位、run/epoch/call/action 关联。span 只保存内部标识和白名单属性，不记录模型正文、路径、SQL 参数或凭据。

复现命令：

```powershell
npm run start:local
uv run pytest tests/backend -q
.venv/Scripts/python.exe scripts/profile-scheduler.py --output .forge/scheduler-new-sample.json
$env:FORGE_BROWSER_PATH='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
npx playwright test --workers=1
npm run check
npm run typecheck:backend
npm run lint
npm run format:check
npm run types:check
npm run build
uv build --wheel --offline --out-dir .forge/release-check
```

性能采集应单独执行，不与全量测试同时运行。SQL 分类时间包含服务端执行与等待，未独立测出锁等待；嵌套 span 总时间不能相加。旧版两轮同机隔离测量也通过，因此保留历史超时与中间慢样本，不能将本轮结果解释成稳定提速比例或生产高并发容量证明。

## 尚未执行的效果验收

- 两个外部 Python/TypeScript 仓库的真实模型补丁、独立沙箱验收、实际 usage/费用和失败恢复仍待环境。现有自编案例与人工参考补丁不满足该要求。
- 20–30 独立任务检索 pilot 尚未运行，计划明确 `model_run`、`sandbox_run` 为 `not_run`，不自动启动或付费；没有修复率、总成本或负迁移收益结论。
- 真实失败经验候选的独立任务收益、灰度结果以及外部 OpenTelemetry collector 互操作未验证。
- 后端 9 项跳过涉及真实发布基础设施、Docker/S3、隔离备份入口或主机无法创建的安全链接；跳过不计为通过。真实模型、gVisor、组织 IdP 和外部服务发布门禁仍按已有验收要求执行。

演示主线仍是接入固定仓库、修复、独立验收、补丁与费用、一次失败恢复。获得真实环境后，应优先补齐上述证据，再决定检索或经验候选是否值得启用；默认结构检索保持关闭。
