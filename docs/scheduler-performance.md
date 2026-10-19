# 独立性能测量

普通 CI 的 180 秒上限用于发现挂起。`.github/workflows/performance.yml` 单独运行性能门槛，默认 45 秒，保留失败报告。它每周运行，也支持手动指定门槛。

先配置专用 Linux x64 runner，标签为 `forge-performance`，安装 Docker、PostgreSQL 客户端及 Git；该机器同一时刻只接受此作业，禁止其他测试、模型任务和构建共用资源。随后设置仓库变量 `FORGE_PERFORMANCE_ENABLED=1`。当前仅交付作业配置，没有声称已经配置或运行 GitHub 专用机器。Python 3.12.10、Node 24.10.0、PostgreSQL 17.6 及 uv.lock 固定；报告保留实现摘要、commit、平台和环境标签。调整机器资源或数据库配置后需建立新基线。

本地可在跟踪文件干净的 commit 上运行：

```powershell
uv run python scripts/profile-scheduler.py --require-clean --environment-id local-windows-shared --max-seconds 45 --output .forge/performance-new.json
```

报告不可覆盖。即使吞吐门槛失败，仍写入 pytest 退出码、20 项调度的排队与执行 P50/P95、有效租约峰值、验收阶段 span、各任务事件时间线、SQL 时长。额外接管样本通过实际过期的租约测量 pickup 和恢复进展时间；只有一个样本，不解释为接管时延分布或进程崩溃测试。外部重复效果未测，保留 null。

性能样本不调用模型；验收为 fixture 的 AST 检查，不代表 Docker 验收吞吐。事件间隔含排队，SQL 时长含服务器执行和等待，嵌套 span 不可相加。共享 Windows 机器的报告只能说明当次观测，不能用来认定性能回退；CI 绿也不能替代专用环境的门槛结果。工作流按 commit 上传 JSON，保留 90 天，历史基线应另行归档。
