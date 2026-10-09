# 编码案例与结构检索复现

2026-10-09：代码与本地验证，尚无真实模型或 Linux 容器运行证据。

## 生成两个固定仓库

要求 Python 3.12+、Git、Node 24+，依赖使用 `uv sync --frozen`。运行：

```powershell
uv run python scripts/prepare-coding-cases.py .forge/coding-cases-new
```

目录必须不存在。脚本生成 Python 金额舍入和 TypeScript 小数位两个多文件缺陷仓库、固定 commit、Git bundle、独立 acceptance、人工参考 patch、dataset/experiment 配置及原始报告。参考 patch 和私有验收放在 repository 外，上传仓库时不会进入模型工作区。脚本仅执行自编基线与人工参考修复，不能用于执行任意模型产物；模型产物走已有独立沙箱验收。

两例均验证：缺陷基线 exit 1、参考修复 exit 0；`git apply --check` 和实际应用后的树摘要一致。Python 验收检查半分舍入、负调整、每单位舍入再乘数量；TS 检查一位小数、整价、两位小数、负数与非法精度。

原始报告：[controlled-coding-2026-10-09.json](../benchmarks/reports/controlled-coding-2026-10-09.json)。这些是公开、自编的 development 案例；不是两个盲测独立仓库或模型已解决的真实 Issue。10 条断言也不等于 10 个独立任务。

## 登记与真正运行

接入 CLI 可直接使用生成目录：

```powershell
uv run forge project register python-invoice-rounding .forge/coding-cases-new/python-invoice-rounding/repository .forge/coding-cases-new/python-invoice-rounding/acceptance.json --dry-run
uv run forge project register typescript-invoice-fraction .forge/coding-cases-new/typescript-invoice-fraction/repository .forge/coding-cases-new/typescript-invoice-fraction/acceptance.json --dry-run
```

去掉 `--dry-run` 才会上传、预检和登记；它不执行验收。实际任务要求同时有 Python 3.12/Node 24 的 Linux 沙箱，可按 `sandbox/Dockerfile.coding` 构建并记录镜像 digest，再设置服务端 `FORGE_SANDBOX_IMAGE`。此镜像尚未在本机构建或验证。两例无第三方运行依赖，Node 使用原生可擦除 TS；需要 npm/编译器的真实项目仍必须使用锁文件与固定 dependency_image。

只有配置模型与价格、沙箱后才执行：

```powershell
uv run forge eval dataset .forge/coding-cases-new/dataset.json
uv run forge eval experiment .forge/coding-cases-new/experiment.json
```

实验默认 configured 模型，每 case/配置重复 3 次，两配置同预算、同验收、memory/delegation 关闭，仅改变 code_retrieval。总任务硬预算 6 美元；真实调用会收费。当前未执行这一命令。没有 CPU/存储/工具/人工费假设时，完整费用保持不完整。

## 检索实现和测量边界

- `Harness.code_retrieval` 为 `off`（兼容默认）、`lexical` 或 `structure`，绑定进执行版本。
- Python 用 AST，TypeScript/TSX 用 Tree-sitter，支持箭头函数、方法、接口与类型；忽略注释中的伪声明。`repo.symbols` 返回解析器、符号位置、导入与有界标识符引用。
- 相同源集合与片段配额上比较关键词排序和符号/导入一跳扩展；不跟随符号链接，单文件 1 MiB、总 2 MiB、最多 200 文件；跳过项与原因可查。返回最多 12 个片段，每片段最多 80 行/6000 字节，仍受总上下文预算限制。
- 模型可见片段有文件摘要与一基行号，标为 untrusted_repository。manifest 记录检索策略、扫描范围及 workspace digest；repo.read 的 line_start 仍为零基。
- 对象读取和解析复用已 fenced 的快照，在数据库事务及知识锁外执行；消费前核对工作区、输入、任务与语义绑定，变化则丢弃本次上下文，不派发付费调用。
- 只解析静态导入关系，不承诺动态导入、TS 路径别名、Python sys.path、类型/重载或跨包语义解析；无 LSP/向量库。

本地两个自编任务的目标文件 Recall@2：Python 词法 0.5 / 结构 1.0，TS 两者均 1.0。片段本地 token 分别为 228→297、424→440，结构策略增加了输入长度。这里没有模型成功率、模型计费或定位时延证据，不据此默认启用。下一步应对不参与实现调优的外部任务预登记目标文件/验收，在相同模型和输入预算下记录修复率、完整费用缺项、P95 和失败原因；保留无收益结果。

## 控制台证据

评测页优先配对实验，确定性运行时自检保留为辅助入口；表格区分修复成功和正确暂停，显示区间缺失、费用预留、完整成本缺项和失败分布。原始报告可展开/下载。

任务的“案例回放”按固定事件上界分页读取摘要，显示任务约束、commit、固定配置、故障与接管事件，并链接已有补丁/验收页。该 GET 接口不重放执行、不返回历史 workspace 正文或 event transition。导出明确仅含已加载事件；运行中的摘要是读取时状态，后续进展需要重新打开。
