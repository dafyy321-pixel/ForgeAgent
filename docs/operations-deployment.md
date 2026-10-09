# 数据与部署运维

## 对象、保留与删除

对象发布按 64KiB 读取，内存 spool 上限默认 4MiB，超限转临时磁盘；单对象上限仍由 `FORGE_MAX_OBJECT_BYTES` 控制。S3 默认 8MiB 分片，失败执行 abort；bucket 应另配 7 天未完成分片清理。S3 读取区分缺失、配置/授权错误与暂时不可用，错误正文不暴露凭据或 endpoint。所有读取与发布核对字节数和 SHA256。

`POST /v1/operations/objects/gc` 默认预览；`apply=true` 才删除。引用图扫描每张租户表的 JSON 列，按 recovery、deliverable、knowledge、audit 分类。所有仍被引用的证据永久保留；孤儿对象须属于已终结、无租约、无未结算账单或外部效果的任务，并经过至少 24 小时宽限。S3 删除涵盖每个历史版本和 delete marker，每批最多 1000 项。活动任务、项目、executor 与未注册命名空间不会自动清理。

没有隐式日志到期策略：有引用的日志/模型请求也是审计证据；缩短保留期限须先通过管理员的家族擦除操作。`POST /v1/operations/runs/{id}/purge` 从整个 root 开始计算任务、派生 memory/skill/evaluation 血缘闭包；先要求账单/效果结算并登记数据库擦除，保留不含正文的费用审计，再删除对象版本与工作目录。操作不可恢复；API 失败后持久 erasure 维护任务会重试。第三方提供方、外部备份须按自身保留流程处理。

`POST /v1/operations/workspaces/gc` 同样先预览，回收已结算终态任务的目录，以及活动任务已经被 fencing 的旧 epoch、verify、stage/backup 目录；保留活动任务当前及后续 epoch；Git worktree 使用 Git remove。Docker 由 manager 标签绑定共享数据根与工作区，只回收超过持久硬截止时间的受管容器；不按容器名字猜测所有权。

## 维护任务、遥测与告警

`POST /v1/operations/jobs` 接受 objects/workspaces/containers、apply、min_age_hours 和 idempotency_key。`GET /v1/operations/jobs` 查询状态。`python -m forgeagent.maintenance_jobs` 独立执行，数据库领取、租约恢复、有界退避、最多五次后 dead_letter；`POST /v1/operations/jobs/{id}/retry` 由管理员审核后重试并追加审计。此目录仅允许可重复的清理，不接收模型调用、远程写入或未知效果重发。

模型、工具、验证、恢复、存储和备份有 operation span、完成计数和耗时 histogram。标签只有固定操作与结果，禁采参数、提示、路径、HTTP header、异常正文。`/metrics` 需管理员认证；进程指标与租户持久 run/account 指标分别解释，重启不会抹掉账单与任务状态。Worker 的链路指标通过 OTLP 输出；多进程部署不能把 API 进程计数当作 Worker 汇总。

`/v1/operations/health` 返回可机器处理的 alert、runbook、未知效果最大年龄（目标 900 秒）与已终结成功率/样本数。暂停本身不告警；未知账单/效果、过期租约/审批、异常 Outbox 和维护死信须进入监控系统。部署中的 collector 默认 basic debug exporter；生产接入组织的 OTLP 后端与规则，禁把正文加回 span。

## 联合备份与隔离恢复

1. 停 API 写入、Worker、maintenance 和 manager 请求，等待租约和外部效果处理达到明确边界。备份期间不要运行迁移。
2. 私有凭据管理配置 `FORGE_BACKUP_DATABASE_URL`，使用离线管理员/BYPASSRLS 身份；日常 API/Worker 始终用 NOSUPERUSER、NOBYPASSRLS 的 forge 用户。使用与服务端相同主版本的 PostgreSQL `pg_dump`、`pg_restore`。
3. 执行 `forge admin backup <新备份目录>`。程序持有租户对象生命周期锁与全表 SHARE 锁，导出 REPEATABLE READ 快照；pg_dump 使用同一 snapshot，引用对象逐个流式复制并验证。只有全部成功才写 manifest。备份短暂停写且有锁超时，锁冲突应退出后重试。
4. 将目录整体存到加密、异地且有保留策略的备份仓库；manifest SHA256 是完整性检查，不是防篡改签名，备份仓库权限与加密由部署系统负责。
5. `forge admin restore-drill <备份目录> forge_restore_<唯一名字> <新目录>`。先验 manifest、dump 和全部对象，创建新数据库，保留原 owner/ACL/RLS，恢复后逐项核对对象引用和 schema，写 restore-report.json。不覆盖现有数据库、不启动 Worker、不触发远程副作用。目标数据库应已有备份中的 forge 等角色。
6. 演练后由运维检查记录、成本、检查点和恢复条件；生产切换必须更换连接/数据目录，重新确认模型/镜像/凭据可用且旧 Worker 已停止。恢复不能证明外部副作用不存在，UNKNOWN 保持人工对账。

S3 备份导出使用与本地相同的引用图，保存实际已校验字节，不依赖 bucket 原版本持续可用。对象存储恢复演练使用独立本地目录；恢复至生产 S3 时先逐项上传/校验，核对引用图后才切换。备份中的已删除正文需要独立到期与擦除治理。

## 发布与完整部署

Dockerfile 提供 runtime、manager、web 三个 target；发布 workflow 在 v* tag 或人工触发时输出 GHCR 镜像摘要、SBOM 和 provenance。日常 main push 不发布。部署始终固定 `@sha256:` 摘要，并在发布门禁通过后填写 `FORGE_RUNTIME_IMAGE`、`FORGE_MANAGER_IMAGE`、`FORGE_WEB_IMAGE`。

`compose.deploy.yaml` 包含 PostgreSQL、一次性迁移、API、Worker、独立 maintenance、HTTPS manager、TLS web gateway、OTLP collector 和可选 MinIO。先准备私有环境/密码文件、TLS 证书和绝对共享目录，设置 Compose 所需变量。运行 `docker compose -f compose.deploy.yaml config --quiet` 检查配置，然后启动。不要输出完整 config：其中可能包含展开后的私有环境。宿主机必须安装 Docker 和 runsc；宿主机/Worker/manager 的共享目录使用同一绝对路径，目录属主 10001。

runtime 私有环境至少包含 FORGE_DATABASE_URL（连接 postgres:5432/forge）、OIDC issuer/audience/JWKS、模型配置与费率、允许的工具 host、S3 配置（可省略用本地）、manager 签名 secret。manager 使用独立环境，只含相同签名 secret 与可选只读凭据 routes，不授予数据库/模型/S3 密钥；Docker socket 只挂给 manager，设置宿主 Docker group GID。其 TLS 证书须含 sandbox-manager DNS 名字；客户端 CA 文件为 ca.crt，web 的 web.crt/web.key 由浏览器信任。远程工具与模型需要网络出站，容器内网络仍被禁止。

OIDC 使用现有组织身份提供方；JWT 必须含 tenant_id、sub、exp、正确 issuer/audience 和顶层 roles（管理员角色 forge-admin），先 provision 租户/身份。提供方 login UI/PKCE 客户端接入由控制台升级部分处理。不能用本地认证公开部署。

启用 `--profile s3` 时，MinIO 私有环境必须有 root credentials 与 `MINIO_KMS_SECRET_KEY`（SSE-S3 所需 KMS；保存在私有 secret 文件）。bootstrap 环境含 `MC_HOST_forge=http://<root-user>:<root-password>@minio:9000` 及独立应用 S3 access/secret；bootstrap 创建 bucket、版本化、未完成上传清理和仅 forgeagent bucket 的应用策略。runtime 使用应用账户而非 root。也可直接接入组织的 S3 和 KMS；外部 S3、实际镜像/gVisor 必须真实联调，模拟测试不替代。

升级：先备份并完成隔离恢复演练；记录上一组镜像摘要与迁移号；停止写入/领取并等待持久边界；拉取新摘要，单独执行 migrate；确认 readiness 的 schema 版本后启动 API/Worker/maintenance，再做小范围任务与遥测检查。新增约束和擦除迁移不支持 downgrade。回退必须使用同数据库版本兼容的旧镜像；不兼容时停止所有写入，从联合备份恢复到新实例，经外部效果对账后切换。不要让旧代码直接写新 schema。

本地验证与真实联调分开记录。当前缺少 Linux Docker/gVisor、S3 与真实模型测试环境，不能据本地协议回归声明生产部署已通过。
