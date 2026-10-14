# 测试配置与平台边界

普通后端测试通过 autouse fixture 重置模型协议、流式开关、计数开关、上下文窗口、费用和凭证，不读取开发者 `.env` 中的模型配置。数据库、存储与 Docker 测试仍使用明确配置的测试基础设施。运行 `uv run pytest tests/backend -q` 即可，不需要手动清空模型变量。真实模型仅在显式设置 `FORGE_TEST_RELEASE=1` 的 release integration 模块启用。

前端集成测试必须连接无真实模型凭证的独立 API/Worker，或使用完整请求拦截。不要将普通 Playwright 测试指向配置了付费模型的工作空间。正式 release gate 保留单独的凭证与基础设施要求。

20 任务调度用例验证全部完成、租户及根任务限流。180 秒是检测挂起的上限，不是性能承诺。JUnit 的 scheduler_profile 属性记录总耗时、排队等待及执行耗时 P50/P95、事件间隔和最大有效租约数。设置 `FORGE_SCHEDULER_MAX_SECONDS` 可单独启用指定机器上的吞吐门槛。阶段间隔包含排队等待，不能当作纯 CPU 或模型延迟。

运行指标还提供 `forge_queue_wait_seconds` 和 `forge_worker_slot_utilization_ratio`；后者包含任务探测的占用采样，不能直接等同于有效工具利用率。结合有效租约峰值、阶段 Histogram 与队列时间一起分析。禁止将租户名、提示词或凭证放入指标标签。

Linux GitHub CI 是后端、Docker 沙箱、REA 和浏览器集成的自动验证平台。Windows 提供本地 PostgreSQL、API/Worker、浏览器与进程监督开发路径；本次以 Windows + Edge 执行本地回归。平台特定进程模块在 CI 中同时检查 Linux/Win32 类型，但这不是 Windows 全栈 CI。实际代码验收仍通过 Linux Docker 镜像执行，不承诺 Windows 容器或原生 Windows 沙箱兼容。真实 IdP、S3、备份恢复及生产外部效果由单独的 release gate 验证。

创建事务遵循“同账号幂等键 → knowledge 生命周期 → 源任务行”的锁顺序。子任务执行已有父任务租约，进入创建时同样使用集中式幂等键与生命周期函数。Run 的非主键更新用 NO KEY UPDATE，父取消在修改子任务前按 ID 锁定并刷新子行。所有入口复用服务授权与事务；路由拆分不能绕过这些规则。

对话路由已拆到 conversation_api，Worker 的槽位调度拆到 scheduling，用户状态说明拆到 task_status。后续可按同一模式分离项目和运维路由；本次不进行全量文件搬迁。鉴权、任务详情、对话和执行返回值已有 OpenAPI 契约及生成类型；旧管理页面的动态 JSON 仍有兼容类型，后续按业务边界逐步收紧。
