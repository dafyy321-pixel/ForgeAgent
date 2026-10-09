# API 与控制台

升级前执行 `uv run alembic upgrade head`，当前迁移为 0008。新增 tenant、run、project、status 的 keyset 查询索引；历史迁移保持冻结。

## 有界读取

`GET /v1/workspace` 返回最近 50 个可见任务及有界 metadata。参数支持 `project`、`status`、`q`、`limit`（1–100）、`cursor` 和焦点 `run_id`。产物正文通过 `/v1/artifacts/{id}/download` 按需读取，技能正文通过 `/v1/skills/{id}/content` 读取。动作列表批量查询 Outbox/Attempt。

`GET /v1/runs` 返回 `{items,next_cursor}`。`GET /v1/catalog/{collection}` 对 projects、connections、evaluations、skills、memories、artifacts、approvals、events、actions、checkpoints、maintenance-jobs、datasets 返回同一分页格式，支持 `limit/cursor/status/run_id`。不支持的 status 筛选返回 422。每一页重新检查当前授权，授权过滤先于 LIMIT；游标按 created_at/id 倒序，绑定租户、调用者、集合和筛选条件，不能跨用户或改条件复用。列表不是事务快照，新数据在下次首屏同步显示。旧数组端点保留最近 100 项兼容窗口，完整历史请使用 catalog，再按 ID 读取详情。

共享部署必须配置不少于 32 字符的 `FORGE_CURSOR_SECRET`；轮换后旧游标失效，需要从第一页重读。本地模式自动生成持久本地随机密钥。不要在客户端记录或生成签名密钥。

控制台初次、路由变化和写入成功后读取 workspace，平时通过轻量 revision 检查变化，隐藏标签降低频率。焦点任务使用 SSE；任务列表提供加载更多，焦点记录提供加载更早记录。后端变化会重新读取最近窗口，以刷新权限、状态和事实。事件元数据排除内部恢复投影与隐藏测试正文。

## 身份与流

配置 `FORGE_AUTH_MODE=oidc`、HTTPS `FORGE_OIDC_ISSUER`、JWKS、audience、client_id 与 scope。IdP 中登记公共浏览器客户端、授权码 + PKCE、精确回调 `<console-origin>/auth/callback` 和允许的退出后地址。访问令牌需包含 exp、sub、iss、aud、tenant_id；管理员 roles 必须是包含 forge-admin 的数组。数据库 Authorization 必须已登记且 active；管理员声明不绕过已撤销的工作空间身份。

`/v1/auth/config` 公开浏览器所需配置，不返回密钥。浏览器使用 oidc-client-ts 管理 state/nonce、PKCE、续期与 callback，令牌仅存 sessionStorage；禁止将 client secret 放入浏览器。前端退出清理本地身份与令牌，IdP 的组织会话由 IdP 自行管理。真实 IdP 需部署后另行验证；本地协议 fixture 不是组织联调证据。

`GET /v1/runs/{id}/events` 使用 Last-Event-ID 续传；服务端每批重查授权，原令牌到期发 auth_expired，权限撤回发 authorization_error，终态发 end，连接最长 300 秒。SDK 与浏览器解析完整帧、去重并有界重连（最多连续 8 次失败），认证错误停止；SDK token 可传同步或异步 callable。浏览器 45 秒未收到字节主动重连，另保留 revision 同步。

## 请求与类型

前端 ApiError/SDK ForgeError 保留 code、message、retryable、correlation_id 和校验详情。请求支持取消及超时；写入不会自动重发。不确定写入按调用者/方法/路径/正文摘要持久保存幂等键，刷新后可使用同一键显式重试；确认成功或确定的客户端错误后消费该键。仅后端明确支持幂等的端点保证重试去重。

进度未知时显示 indeterminate，验收成功才为 100%；预算包含预留和未知账单，环境未探测为 unknown，环境问题的验收为 INCONCLUSIVE。恢复诊断显示这些真实事实，最终恢复门禁仍由后端决定。

`npm run types:api` 从当前 OpenAPI 生成 `src/generated/api-schema.d.ts`；`npm run types:check` 检查漂移。CI 执行 Ruff、SDK/公共响应契约的 ty 检查、TypeScript/生产构建、后端/浏览器回归与 Python sdist/wheel 构建。类型检查范围按模块逐步扩大，尚未宣称全仓库严格类型通过。
