# Memory、Skills 与离线演化

Memory 先用任务目标、范围和验收说明的英文词/中文二元字组检索，按相关性排序，最多固定 20 项到任务快照。不预设向量召回的收益。`valid_from`、`expires_at` 必须有时区，`repository_revision` 必须匹配固定仓库 revision。同 subject 的冲突即使超出召回上限也会标注；冲突事实需要来源审核。显式 `supersedes_id` 只可替代同项目、同 subject 的活动版本，旧记录失效、新版本递增。来源是已审核的人类资料，不能提升模型权限。

`DELETE /v1/memories/{id}` 撤回后，活动任务移除该快照、增加 input revision、取消未执行意图，并使旧持久模型决策和原生 continuation 失效。撤回前 observation 不再进入未来上下文，也不能通过 observation.read 再召回。完成任务保留历史验收事实，并标记撤回；历史记录仍保留，不能把撤回等同于物理删除。

管理员可调用 `POST /v1/memories/{id}/purge`，提供 `{"erase_derived_runs":true}`，明确擦除整个派生 Run family。血缘沿 memory snapshot/source_refs/supersession，以及 skill source_runs 和技能消费快照闭包传播。任务创建和知识生命周期使用租户级事务锁，防止并发创建漏入血缘。未知账单、未完成外部效果或活动租约存在时拒绝擦除，先结算。

擦除事务先隔离任务，再清理检查点、上下文、模型正文引用、任务说明、回执、Inbox/Outbox 正文、派生技能及评测报告；只保留无正文的财务/效果标识和摘要审计。数据库迁移 `0005` 仅允许已登记、已结算且已隔离的隐私擦除删除旧事件，常规事件仍只追加。隔离后的任务不能恢复、fork 或验收。对象和本地工作区清理在事务后执行，失败保留 pending 记录，重复调用原 purge 可继续清理；S3 包含历史版本与 delete markers，返回逐项错误时不报告完成。备份及模型供应商已留存数据须走其单独删除/保留过程，不声明当前 API 能擦除外部副本。完整后台维护/GC 与备份治理由后续数据运维部分补齐。

技能注册支持 `resources` 字典：UTF-8 正文或 base64 FileEntry（100644/100755），禁止链接、路径逃逸、非规范路径和大小写冲突，最多 1 MiB/100 项。技能版本不可覆盖，包摘要固定正文和资源；上下文仅加载名称、描述和资源摘要，`skill.read {skill_id,path}` 按需读取任务已固定的 SKILL.md/资源。脚本不自动执行。管理员可用 `GET /v1/skills/{id}/package` 导出包含 SKILL.md frontmatter、资源、模式与摘要 manifest 的 ZIP。

`POST /v1/skills/proposals` 使用开发 Run IDs、name 和 version，离线聚类持久 failure category/signature，提炼后续成功 observation 的工具序列和来源摘要，生成候选方法包。它不读取隐藏验证日志/提示正文/凭据，也不调用付费模型；候选不是已验证的因果修复。来源为已擦除或 held-out 轨迹时拒绝生成；手动注册技能和发布 Memory 的显式来源同样拒绝 held-out。

保留集任务 fingerprint 不能与开发集或其他保留集重叠，保留集只允许一次配对实验，列表接口只披露元信息。技能发布须与 baseline 只相差一个目标技能，Harness 完全一致且 memory 关闭，并满足既有真实固定模型、20 独立任务/两项目/三重复、结算、非劣效与成本门禁。fixture 无法通过生产发布门禁。

发布支持 `rollout_percent`，以 tenant/Run/skill 的稳定哈希分配无技能 baseline 或已选技能 cohort，结果固定在任务的 skill_assignments；实验显式使用所选候选，避免被灰度比例稀释。退役只影响新任务。`POST /v1/skills/{id}/rollback` 提供 target_id 和 review，只能回到同名、过去有评测摘要和审核记录的已发布版本，并追加回退历史；已擦除版本不得重新发布。运行中的任务继续使用固定版本，回退不热改旧任务。

本地验证覆盖真实 PostgreSQL、Worker 工具链路、并发擦除，以及版本化 S3 删除协议 mock。真实 S3 删除、真实模型技能增益及生产灰度仍待测试环境；不能将 mock 或 fixture 作为这些结果。
