# MCP/A2A、外部效果与远程产物

## 支持矩阵和验证边界

| 协议 | 发现/绑定 | 调用和恢复 | 本地证据 | 真实服务 |
|---|---|---|---|---|
| MCP 2025-11-25 | 官方 SDK initialize、分页 tools/list、版本/schema 核对 | SDK tools/call；不混用新版 Tasks | 本地 TLS HTTP fixture、官方客户端链路 | 待联调 |
| MCP 2026-07-28 | server/discover supportedVersions、tools、Tasks 扩展、分页目录 | 每请求版本/能力 `_meta`；tools/call、tasks/get/update/cancel | 本地 TLS HTTP fixture、JSON-RPC ID 与状态/恢复故障测试 | 待联调 |
| A2A 0.3.0 | Agent Card protocolVersion/url/JSONRPC | 官方 SDK message/send，tasks/get/cancel | 本地 TLS HTTP fixture 与状态映射 | 待联调 |
| A2A 1.0 | supportedInterfaces 中精确 endpoint/binding/version/tenant | SendMessage/GetTask/CancelTask；ROLE_USER 与成员区分 parts | 本地 TLS HTTP fixture、独立适配器 | 待联调 |

以上 fixture 使用测试专属 loopback/TLS 例外，生产 SSRF 防护不因此放宽。真实身份提供方、远端 Tasks 生命周期和真实服务互操作均未验证。协议内容于 2026-10-09 对照 [MCP schema](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/schema/2026-07-28/schema.ts)、[Tasks draft](https://tasks.extensions.modelcontextprotocol.io/specification/draft/tasks)、[A2A v1 迁移说明](https://github.com/a2aproject/A2A/blob/main/docs/whats-new-v1.md)、[A2A proto](https://github.com/a2aproject/A2A/blob/main/specification/a2a.proto) 核对。

目前采用有界 JSON HTTP 查询。Streaming/SSE/gRPC、客户端 sampling/tool execution 和新版同步 input_required continuation 不在支持范围；这些不能自动获得执行权限或被当作成功。A2A 客户端收到必需扩展时拒绝协商，支持的认证为匿名或外部提供的 Bearer/OAuth/OIDC token。授权/刷新由部署方凭据代理负责，不在远端返回的 URL 上自动执行 OAuth；API-key、mTLS 和其他认证没有对应适配器时拒绝协商。当前产品需要出站委派，不开放 A2A 服务端任务创建入口。

## 连接与审核契约

1. 配置 `FORGE_REMOTE_HOSTS` 公网主机允许列表；端点必须 HTTPS，禁止 URL 用户信息、fragment、私网/metadata/loopback、重定向及环境代理。DNS 校验覆盖全部解析地址，实际连接固定到已校验 IP 并保留原 TLS SNI。
2. 管理员 POST `/v1/connections`，登记协议、URL、可选发现 URL、凭据引用和审核后的 `tools`。初始状态 pending。
3. 管理员 POST `/v1/connections/{id}/discover`，将真实发现证据、时间、审核人及摘要持久化；MCP 工具名和完整输入 schema 必须与审核契约一致。协议/扩展/认证不兼容拒绝激活。工具服务自报只读/幂等注解只作为发现证据，效果由管理员审核。
4. 创建任务时显式填写 `connections: [id]`；控制台运行配置支持选择。固定连接、schema、协议、凭据引用、发现摘要及工具契约进入语义快照；孩子只能选择父任务已有连接。管理员停用连接立即阻止新效果。历史注册但未经发现/未选进任务的连接不能调用。

工具契约示例：

```json
{
  "operation": "publish",
  "description": "Publish a version under a reviewed precondition",
  "effect": "irreversible",
  "input_schema": {
    "type": "object",
    "properties": {
      "content": {"type": "string"},
      "expected_version": {"type": "string"},
      "request_key": {"type": "string"}
    },
    "required": ["content", "expected_version", "request_key"],
    "additionalProperties": false
  },
  "idempotency_field": "request_key",
  "cas_field": "expected_version",
  "reconcile_operation": "lookup_publication",
  "reconcile_key_field": "request_key"
}
```

`lookup_publication` 必须另行登记、发现并审核为 read 工具，其输入包含对应查询字段。CAS 值是下游真实资源版本，由调用方提供并交给下游验证，不能用本地 workspace digest 冒充。业务幂等字段必须确实由下游执行契约保证；仅发送 JSON-RPC ID 不代表幂等。没有幂等/查询契约的工具仍可经逐项审批调用，但未知效果禁止重发，只允许外部证据人工对账。

`read` 需要任务及当前授权均有 `external.read`；`external_write`/`irreversible` 需要 `external.write` 和绑定效果摘要、当前权限 epoch、workspace 前置摘要、有效期的独立审批。获准的目录进入原生工具/结构化输出/JSON 上下文，运行时仍校验 schema 与权限。开放对象或递归 schema 无法适配所选 strict 模型时，在模型调用前暂停并报告，不能悄悄扩大 schema。

`idempotency_field` 在模型目录中隐藏，由 Runtime 根据租户、任务、连接、工具和逻辑动作生成，保存到不可变 intent。手动 `/runs/{id}/remote-actions` 请求需要 body `idempotency_key`，同键同参数返回原动作，参数冲突拒绝。调用前再次核对 MCP 实际 schema。schema 漂移不提交远程写入，异常仍采取保守对账路径。

## 凭据与回调

`FORGE_REMOTE_CREDENTIALS` 是部署端 JSON 映射，连接只保存引用。每个值必须绑定 `tenant_id` 和精确 HTTPS `origin`；可含 `authorization`、`scopes`、`callback_secret`。真实密钥置于部署环境/secret 管理，不写入 Git、连接数据、任务快照或聊天。连接 `credential_ref` 用于出站 Authorization，`callback_key_ref` 用于回调验签；允许各自轮换。

回调 POST `/v1/callbacks/{connection_id}` 使用 `X-Tenant-Id`、`X-Message-Id`、`X-Timestamp`、`X-Signature`。此入口使用连接 HMAC 身份，不要求普通用户 OIDC token。签名是 `callback_signature()` 定义的 HMAC-SHA256：canonical JSON 包含 version=1、tenant、connection、message_id、timestamp 和原始 body 的 sha256 摘要。5 分钟时窗、200 字符消息 ID、64 KiB body；重复 ID 相同正文返回 duplicate，不同正文拒绝。旧全局 callback_secret 的签法不再接受。

Inbox 收到签名消息后持久化；Worker 只消费一次，用于唤醒绑定 task 状态查询，不能凭回调声称 completed 将动作/任务晋级成功。暂停意图不由回调清除，删除后的任务不能消费回调来重建正文。

## 控制、对账与产物

取消使用独立 Outbox：pending → dispatching → sent；发送前落库、确认后立即落库，之后才查询。取消确认不等于 terminal。dispatching 崩溃/发送超时转 unknown，不盲重发，可继续只读查询原 task。原 task 达到终态后结算取消控制；人工 `/outbox/{id}/reconcile` 可确认 occurred 或证明 absent 后明确允许再次取消。

暂停停止新输入与本地决策；恢复只轮询已知远程 handle，禁止重建同一远程任务。迟到 input_required/completed 不覆盖取消/暂停意图。远程输入需要 reviewer 权限、当前任务版本/权限 epoch、原 request keys 和可校验 form schema。Outbox 绑定 inputRequests 摘要；暂停保留 pending，取消或过时输入取消，dispatching/unknown 输入不重发。

UNKNOWN 写动作可由管理员 POST `/actions/{id}/reconcile-query`，只调用审核的只读查询插件，并固定原业务 key。响应作为证据保存，动作仍 UNKNOWN；人工核验后再调用既有 `/reconcile`，避免把远端输出当作可信结算。

POST `/actions/{id}/artifacts` 需要当前版本、stored response 中的 artifact/resource-link `index`，可提供 `expected_digest`。只访问允许列表 HTTPS、无重定向；不同 origin 不转发认证；最多 min(8 MiB, object limit)，受 root 存储配额约束。保存的 Artifact `verified=false`，来源、摘要和 trust 进入事件，不执行/解压/合并正文，也不代替本地验收。证据记录与配额版本提交在同一事务；已擦除任务拒绝重新发布对象。更大产物使用后续部署受控上传链路，不能绕过此上限。
