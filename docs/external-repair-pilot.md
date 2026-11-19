# 外部修复案例与配对检索 pilot

### 跨模型复跑

在同一租户登记一次固定开发集与项目，完成第一组实验后，切换 API 和 Worker 的模型配置，再提交同一份单变量实验 JSON。每次启动实验都会绑定当前 profile 摘要。通过 `forge eval report <id>` 保存各自的 JSON，在本地执行 `forge eval compare-profiles first.json second.json`。命令核对数据集摘要、代码实现、配置、种子、重复次数、费用假设和逐项运行记录，按模型及 Harness 输出独立通过数、暂停、延迟、负迁移和全部尝试摊到每次通过的成本。项目绑定含租户内对象引用，跨部署时也要确认报告中的 dataset_digest 相同。未知完整费用保留为空。已有单次使用的 held-out 约束仍适用，应使用开发集进行多轮调参。

#### 可离线复现的合成开发集

`benchmarks/profile-smoke-v1/prepare.py` 生成两个确定性 Git 仓库和四个任务。Python 项目修复库存增减汇总，TypeScript 项目修复半开区间边界；两个修复各有独立验收。另一项只读讨论需要向需求方询问保留期限，最后一项用 1 token 硬额度验证模型调用前的准入暂停。四项都进入分母。公开合成样例只用于开发回归；下文的 h11、trace-mapping 外部历史结果另行报告。

准备阶段需要 Git、Python 3.12 和 Node 24，可离线执行固定的作者样例。输出包括可登记的仓库、私有验收契约、数据集、单变量实验参数以及实际执行的基线失败、参考修改通过和补丁应用记录。参考修改及私有测试均未放进待修仓库。以下 PowerShell 示例使用已构建的 `sandbox/Dockerfile.coding` 镜像，目录须为新建路径。

```powershell
$image = docker image inspect --format '{{.Id}}' forgeagent-coding:local
$sample = ".forge/profile-smoke-v1"
uv run python benchmarks/profile-smoke-v1/prepare.py $sample --image $image
foreach ($project in 'fixture-stock-v1','fixture-window-v1') {
  uv run forge project register $project "$sample/$project/repository" "$sample/$project/acceptance.json"
}
uv run forge eval dataset "$sample/dataset.json"
```

实际配置好模型和沙箱后，每个 profile 各运行一次 `uv run forge eval experiment "$sample/experiment.json"`。每次创建 8 个任务（四项 × recovery 关/开 × 一次），合计硬预算上限为 4 美元。等待结算后保存两个报告，再执行比较。只验证管线时，可使用测试 PostgreSQL 运行 `uv run pytest tests/backend/test_profile_smoke_cli.py -q`。它通过两个脚本化 provider profile 完成实验、保存 API 报告并调用比较 CLI；本机解释器只运行预先写定并核对内容的合成验收代码。脚本化结果不计入真实模型修复率。

本页记录两个公开历史缺陷的受控外部 pilot。h11 和 trace-mapping 已在固定 Linux sandbox 中验证：各自的基线 commit 使独立验收失败，上游参考修复 commit 通过。这个校验证明验收和环境绑定有效，不等于模型已修复；真实模型的每次 patch、usage、费用、失败和人工介入会写入脱敏 evidence bundle。案例来自公开历史开发记录，模型结果不能称为盲测或普遍修复能力。

本轮结果见 [脱敏证据报告](../benchmarks/reports/external-repair-evidence-2026-10-19.json) 及校验文件。`paid-v8` 和 `paid-v9` 保留了资源耗尽、模型请求失败及未知 usage 的失败取证；`paid-v10` 在修复补丁边界清理后交付了两个 recovery-off 结果：Python 与 TypeScript 各一个，独立验收均为 PASS。两条 recovery-on 任务都因没有足够证据建立支持性恢复而安全暂停。模型 usage 在 v10 全部 settled，v8/v9 的未知 usage 仍保留为 UNKNOWN，完整费用仍不完整。

## 两个案例的准备

先各选一个外部 Python 与 TypeScript 多文件缺陷。`scripts/benchmark.py import-external <JSONL> <新输出> --split development` 接受经人工审阅的 issue record：`instance_id`、`repo`（owner/name）、完整 `base_commit`、`problem_statement`、独立 `test_patch`、`language`、`problem_family`。排除金标准 `patch`，保留 source_digest。SWE-bench 导入入口继续保留；没有自动下载、不在宿主执行外部测试。

随后用现有 `prepare` 子命令指定本地仓库与审阅的环境 JSON（image digest、argv、allowed_paths、acceptance_paths），生成绑定固定 commit 的项目与模型任务。隐藏测试在项目保护验收通道；任务输入只有问题、修改范围和来源标识。项目/任务/image 绑定及补丁实际应用由现有注册与验收约束检查。

真实执行使用固定模型配置、内容寻址的离线 Node/Python 镜像与独立 Docker 验收。`scripts/run-external-pilot.py --execute-paid` 固定两个案例、recovery off/on 两组、每组一次；`--total-cap-usd` 可显式设置总预算，默认 20 美元，任务按串行顺序运行，保留失败尝试和未知 usage。参考修复只用于独立验证，绝不进入模型工作区。证据导出只包含 patch、摘要 usage/价格、逐次预算结算、工具错误类别、验收结果、状态和人工事件计数，不导出模型请求正文、凭证或私有日志。

运行前先做不计费验收：

```powershell
uv run python scripts/run-external-pilot.py --validate-only --repositories .forge/external-20261019 --image sha256:... --output .forge/external-20261019/preflight-new
```

付费执行需要在当前干净 tracked commit 上运行，并把输出目录作为待审 evidence；提交后再复制脱敏摘要到 `benchmarks/reports/`。每次运行都应记录实际结算、未知或保留的费用；未知 usage 不能当作 0，也不能用本地重试绕过显式总预算。

## 单变量 pilot：先冒烟，再扩展

`scripts/prepare-retrieval-pilot.py <prepared JSONL> <新计划 JSON> --dataset-id external-pilot@1 --task-budget-usd <单任务硬额度>` 默认三次重复。只生成计划，不注册项目、不启动任务或调用模型。预算上界等于所有任务硬额度 × 两配置 × 重复次数。

20–30 个来源 issue、多仓库与 Python/TS 覆盖只是后续建议。当前 2 项冒烟已经完成第一轮交付验证；它们是开发证据，不足以代表普遍修复能力。可以扩展到 31 项或单语言专项。通用数据集大小为 1–120 项，计划会把样本量和覆盖不足写入 advisories，重复来源、commit/镜像/验收绑定与预算仍严格校验。

默认比较 code_retrieval off/structure；传入 `--variable evidence_recovery` 改为恢复 off/on。基线关闭 memory、planning、summarization、delegation、action fusion；候选只改变所选变量。两组模型、任务、预算、验收与其他 harness 均相同。先研究一个变量，随后用新协议研究检索或 REA 相比普通源码读取的定位轮数、修复率与成本。默认产品检索仍 off，pilot 不代表应启用。

Case 可记录类型化来源：repository、base_commit、task_id、problem_family、language、source_digest。注册核对实际项目 commit，拒绝重复问题换 ID/改写描述来扩大独立样本。带来源的开发/保留数据按仓库与问题族隔离；旧的无来源数据仍采用任务指纹隔离，其“独立性”不能自动认定为审阅完成。

报告分别给出实际 PASS 修复率、正确暂停数量、全部尝试费用、成功尝试费用、人工决策、P50/P95、完整费用缺项、任务层配对区间，以及负迁移任务和分项目修复差值。当前报告明确列出 v8/v9 的未知 usage、v10 的逐案例成本与 patch digest、模型调用数、settled 状态和人工介入记录；不能把已知成本比写成总成本下降。重复运行保留在同一任务簇中，不能当作更多独立任务；来源/问题族标记仍需人工审阅，样本量也不保证统计充分。
