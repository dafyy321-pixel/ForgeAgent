# 外部修复案例与配对检索 pilot

本页记录两个公开历史缺陷的受控外部 pilot。h11 和 trace-mapping 已在固定 Linux sandbox 中验证：各自的基线 commit 使独立验收失败，上游参考修复 commit 通过。这个校验证明验收和环境绑定有效，不等于模型已修复；真实模型的每次 patch、usage、费用、失败和人工介入会写入脱敏 evidence bundle。案例来自公开历史开发记录，模型结果不能称为盲测或普遍修复能力。

本轮结果见 [脱敏证据报告](../benchmarks/reports/external-repair-evidence-2026-10-19.json) 及校验文件。四次 paid pilot 均暂停，没有可接受 patch；一次 live recovery 也因相同失败证据暂停。已知模型费用约 0.01228 美元，未结算项仍保留为 incomplete。

## 两个案例的准备

先各选一个外部 Python 与 TypeScript 多文件缺陷。`scripts/benchmark.py import-external <JSONL> <新输出> --split development` 接受经人工审阅的 issue record：`instance_id`、`repo`（owner/name）、完整 `base_commit`、`problem_statement`、独立 `test_patch`、`language`、`problem_family`。排除金标准 `patch`，保留 source_digest。SWE-bench 导入入口继续保留；没有自动下载、不在宿主执行外部测试。

随后用现有 `prepare` 子命令指定本地仓库与审阅的环境 JSON（image digest、argv、allowed_paths、acceptance_paths），生成绑定固定 commit 的项目与模型任务。隐藏测试在项目保护验收通道；任务输入只有问题、修改范围和来源标识。项目/任务/image 绑定及补丁实际应用由现有注册与验收约束检查。

真实执行使用固定模型配置、内容寻址的离线 Node/Python 镜像与独立 Docker 验收。`scripts/run-external-pilot.py --execute-paid` 固定两个案例、recovery off/on 两组、每组一次，硬上限为 4 美元；任务按串行顺序运行，保留失败尝试和未知 usage。参考修复只用于独立验证，绝不进入模型工作区。证据导出只包含 patch、摘要 usage/价格、验收结果、状态和人工事件计数，不导出模型请求正文、凭证或私有日志。

运行前先做不计费验收：

```powershell
uv run python scripts/run-external-pilot.py --validate-only --repositories .forge/external-20261019 --image sha256:... --output .forge/external-20261019/preflight-new
```

付费执行需要在当前干净 tracked commit 上运行，并把输出目录作为待审 evidence；提交后再复制脱敏摘要到 `benchmarks/reports/`。总预算还要为 live recovery 的 0.25 美元保留余量，不能用本地重试突破 5 美元上限。

## 单变量 pilot：先冒烟，再扩展

`scripts/prepare-retrieval-pilot.py <prepared JSONL> <新计划 JSON> --dataset-id external-pilot@1 --task-budget-usd <单任务硬额度>` 默认三次重复。只生成计划，不注册项目、不启动任务或调用模型。预算上界等于所有任务硬额度 × 两配置 × 重复次数。

20–30 个来源 issue、多仓库与 Python/TS 覆盖只是后续建议。当前先做 2 项冒烟；可以扩展到 31 项或单语言专项。通用数据集大小为 1–120 项，计划会把样本量和覆盖不足写入 advisories，重复来源、commit/镜像/验收绑定与预算仍严格校验。

默认比较 code_retrieval off/structure；传入 `--variable evidence_recovery` 改为恢复 off/on。基线关闭 memory、planning、summarization、delegation、action fusion；候选只改变所选变量。两组模型、任务、预算、验收与其他 harness 均相同。先研究一个变量，随后用新协议研究检索或 REA 相比普通源码读取的定位轮数、修复率与成本。默认产品检索仍 off，pilot 不代表应启用。

Case 可记录类型化来源：repository、base_commit、task_id、problem_family、language、source_digest。注册核对实际项目 commit，拒绝重复问题换 ID/改写描述来扩大独立样本。带来源的开发/保留数据按仓库与问题族隔离；旧的无来源数据仍采用任务指纹隔离，其“独立性”不能自动认定为审阅完成。

报告分别给出实际 PASS 修复率、正确暂停数量、全部尝试费用、成功尝试费用、人工决策、P50/P95、完整费用缺项、任务层配对区间，以及负迁移任务和分项目修复差值。重复运行保留在同一任务簇中，不能当作更多独立任务；来源/问题族标记仍需人工审阅，样本量也不保证统计充分。
