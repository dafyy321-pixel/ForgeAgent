# 模型、上下文与成本

配置固定 `FORGE_MODEL_ID` 与实际供应商费率（USD / 百万 token，数值等于 micros/token）。普通输入、输出、缓存读取和缓存写入分别配置 `FORGE_INPUT_PRICE`、`FORGE_OUTPUT_PRICE`、`FORGE_CACHED_INPUT_PRICE`、`FORGE_CACHE_WRITE_PRICE`。推理 token 已包含在输出中，不重复收费。未知、不一致、缺失用量或未配置的正缓存费用保留 UNKNOWN 账单和预留；返回型号、tier 或 hosted tool 与绑定不一致也不能自动结算。

`FORGE_MODEL_PROTOCOL=responses|chat`，`FORGE_MODEL_OUTPUT_MODE=native|structured|json`。Anthropic 使用 Messages。原生目录只暴露获授权工具，运行时再次检查权限；strict schema 使用封闭对象，协议结束、拒绝和截断均单独处理。持久 response receipt 和 call IDs 支持跨恢复的工具结果对话，Responses 加密 reasoning item 原样传回。SDK 内部重试关闭；429 按持久 Retry-After/退避重试，超时和 5xx 保留未知账单。

`FORGE_MODEL_TOKENIZER` 指定真实 tiktoken encoding，默认 `o200k_base`，应按实际模型校准。生成前使用供应商完整请求计数接口计入工具/对话；Chat 或显式关闭 `FORGE_MODEL_TOKEN_COUNT` 时使用保守 UTF-8 上限预留。无法计数时在收费 dispatch 前暂停。

上下文固定任务约束、当前输入、失败和未解决事项；按相关性和依赖路径选择其他材料。大成功回执可替换为带原引用的确定性摘要，必须 recall 原文才能据其决策；错误引用始终保留。摘要绑定任务/输入/工作区/证据摘要，未发起收费摘要调用。ObservationEnvelope 统一正文摘要、时间、行号、截断与范围读取。

系统提示和工具排序稳定。OpenAI 使用租户和 Run 隔离的哈希 cache key；不擅自选择型号专有缓存政策或付费预热。`GET /v1/runs/{id}/context-cost` 报告实际缓存读写收益（可能为负）与本地压缩估算；fixture 排除，不将估算当实测收益。

管理员可用 `POST /v1/runs/{id}/budget/{operation_id}/query`（Control version）查询已知 `resp_` 原响应。该请求只 retrieve，不重发、不自动结算；`store=False` 及供应商策略可能使响应不可取。仍需在 reconcile 提交人工审核的账单证据。

本地测试覆盖真实 PostgreSQL Worker 路径和模拟 SDK 协议。真实模型账号不可用，供应商互操作、实际收费及缓存经济性仍待真实环境门禁。

参考官方资料：

- [工具调用](https://developers.openai.com/api/docs/guides/function-calling)
- [结构化输出](https://developers.openai.com/api/docs/guides/structured-outputs)
- [运行支出控制](https://developers.openai.com/cookbook/articles/per_run_spending_controller_responses_api)
- [提示缓存](https://developers.openai.com/api/docs/guides/prompt-caching)
