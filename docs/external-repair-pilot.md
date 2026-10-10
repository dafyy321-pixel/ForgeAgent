# 外部修复案例与配对检索 pilot

目前没有真实模型 API 或 Linux 沙箱环境。本轮完成离线准备与校验入口；没有新增两份真实模型修复结果，也没有 20–30 任务的真实修复率或费用收益数据。自编案例与 metadata 单元测试不作为真实仓库效果证据。

## 两个案例的准备

先各选一个外部 Python 与 TypeScript 多文件缺陷。`scripts/benchmark.py import-external <JSONL> <新输出> --split development` 接受经人工审阅的 issue record：`instance_id`、`repo`（owner/name）、完整 `base_commit`、`problem_statement`、独立 `test_patch`、`language`、`problem_family`。排除金标准 `patch`，保留 source_digest。SWE-bench 导入入口继续保留；没有自动下载、不在宿主执行外部测试。

随后用现有 `prepare` 子命令指定本地仓库与审阅的环境 JSON（image digest、argv、allowed_paths、acceptance_paths），生成绑定固定 commit 的项目与模型任务。隐藏测试在项目保护验收通道；任务输入只有问题、修改范围和来源标识。项目/任务/image 绑定及补丁实际应用由现有注册与验收约束检查。

真实执行仍需固定模型配置、可用的依赖镜像与独立沙箱。使用现有实验启动、案例回放、artifact 下载、费用账本及新 trace 取证入口，保留所有尝试、UNKNOWN 对账、人工输入与恢复事件。不得把人工参考补丁写成 model_patch。分别记录模型配置、原始 usage 与账单、实际交付 patch、独立验收、失败和人工耗时；未执行标为 not_run。

## 20–30 任务 pilot

`scripts/prepare-retrieval-pilot.py <prepared JSONL> <新计划 JSON> --dataset-id external-pilot@1 --task-budget-usd <单任务硬额度>` 默认三次重复。只生成计划，不注册项目、不启动任务或调用模型。预算上界等于所有任务硬额度 × 两配置 × 重复次数。

计划要求 20–30 个来源 issue、至少两个仓库、Python/TS 两种语言。基线关闭 memory、planning、summarization、delegation、action fusion；候选仅把 code_retrieval 从 off 改为 structure。两组模型、任务、预算、验收与其他 harness 均相同。默认产品检索仍 off，pilot 不代表应启用。

Case 可记录类型化来源：repository、base_commit、task_id、problem_family、language、source_digest。注册核对实际项目 commit，拒绝重复问题换 ID/改写描述来扩大独立样本。带来源的开发/保留数据按仓库与问题族隔离；旧的无来源数据仍采用任务指纹隔离，其“独立性”不能自动认定为审阅完成。

报告分别给出实际 PASS 修复率、正确暂停数量、全部尝试费用、成功尝试费用、人工决策、P50/P95、完整费用缺项、任务层配对区间，以及负迁移任务和分项目修复差值。重复运行保留在同一任务簇中，不能当作更多独立任务；来源/问题族标记仍需人工审阅，样本量也不保证统计充分。
