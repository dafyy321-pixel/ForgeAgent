# Runtime 轨迹关联

已有 OpenTelemetry / Prometheus 入口继续使用，不引入新服务依赖。配置 `OTEL_EXPORTER_OTLP_ENDPOINT` 后，API 与 Worker 的现有启动配置器启用有界 BatchSpanProcessor。没有 collector 的本地验证使用 SDK 内存 exporter，不代表真实 OTLP 联调通过。

每个根任务使用租户与 root_id 摘要派生的稳定 trace ID，跨 epoch、进程重启和子任务保持一致；各量子是独立 span，挂在合成的 lineage 父 context 下。这是运行分组标识，合成父节点不是一个持续运行的实际 span，也不表示外部效果重放。领取/控制事务在自己的入口 trace 下通过 link 与 run_trace_id 关联根任务。

量子上下文由 ContextVar 传递给 asyncio 任务与 to_thread。覆盖决策上下文、tokenization、代码索引、数据库事务（含提交）、模型派发/响应消费、工具、对象、checkpoint、补丁应用、验收和预算预留/结算。模型派发与消费带 call_id，工具及其子操作带 action_id，结算标记费用是否已知及金额。UNKNOWN 不产生猜测金额。事件保留 run_trace_id 和可用的 trace_id/span_id，便于从账本定位 trace。

导出字段白名单只包含内部标识、epoch、事件序号、金额和状态；不自动导出参数、消息、文件路径、SQL、请求头、provider endpoint 或异常正文。Prometheus 仍只用固定操作名称/结果标签，不把 run/call/action ID 当 metric label。

telemetry.py 纳入语义版本绑定；现存任务可使用捕获的旧执行版本或通过现有显式迁移入口升级。没有跨 collector 的投递与采样容量结论，账本仍是费用与状态的持久依据；导出延迟或 trace 缺失不能作为重试外部效果的授权。
