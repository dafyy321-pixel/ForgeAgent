# 固定仓库、依赖构建与独立验收

CLI `uv run forge project register PROJECT_ID REPOSITORY acceptance.json --commit COMMIT` 导出已提交 Git 对象，不包含未提交文件。服务端接收有大小限制的 bundle，校验固定 commit，建立租户隔离的 bare mirror；各任务及验收使用独立 detached worktree。支持 SHA-1/SHA-256 仓库、UTF-8、二进制、空文件、执行位和安全符号链接。gitlink/submodule 必须注册为独立固定项目，未自动联网拉取；非 UTF-8 文件名拒绝。Windows 无创建符号链接权限时返回明确环境错误，不能将链接伪装成普通文件。

内部 manifest 的普通文本保持字符串；二进制、执行文件、链接使用 `{data: BASE64, encoding: "base64", mode: "100644|100755|120000"}`。校验路径规范、平台大小写冲突、父目录冲突、链接链与循环。Git index 和补丁按精确 blob 字节处理，不执行 hooks、用户过滤器或全局 Git 配置。补丁在临时仓库中执行 `git apply --check`、实际应用和 index 应用，再对照完整 manifest；临时 info/attributes 禁止 checkout 过滤器改写字节，原仓库属性保留在交付树中。

仓库工具提供范围读取、base64 读取、字面搜索、Python AST/基础 JS 符号查询、Git apply_patch、删除、移动及 binary/mode 写入。修改工具有摘要 CAS、任务范围和完整快照检查；失败及上传/提交故障恢复已提交快照。工具正文最多 64KiB，查询有行数/匹配数上限，Python 符号解析最多 1MiB；这些上限不替代模型上下文预算。

## 注册依赖和验收

`acceptance.json` 支持下面的契约，`build` 可省略。`lock_digest` 是文件原始字节的 SHA-256；镜像必须提前安装锁定依赖、使用实际内容地址，并带 `forge.lock.digest` 标签。运行时不安装依赖、不联网、也不自动拉镜像。提供 Python hash-locked wheelhouse 和 Node offline npm-cache 的 Dockerfile 示例；构建命令须传固定基础镜像，安装产物可复用 Docker layer cache。uv/pnpm 等项目同样可以注册固定 lock 与预构建镜像，镜像制作由项目选择。

```json
{
  "id": "python@2",
  "argv": ["python", "-m", "pytest", "-q"],
  "timeout": 120,
  "success_exit_codes": [0],
  "failure_exit_codes": [1],
  "conditions": [{"kind": "file_exists", "path": "src/main.py"}],
  "protected_paths": ["configs/test/**"],
  "protected_tests": {},
  "build": {
    "ecosystem": "python",
    "lockfile": "requirements.lock",
    "lock_digest": "sha256:实际的64位摘要",
    "dependency_image": "sha256:实际的64位镜像ID",
    "argv": ["python", "-m", "compileall", "src"],
    "timeout": 120,
    "persistent_session": true,
    "cache_bytes": 33554432
  }
}
```

镜像安装产物是主要依赖缓存。可写 `/cache` 是有容量上限的 tmpfs，供编译结果与中间文件使用；`/scratch` 和 `/tmp` 也有独立容量上限，容器内存受限。持久 session 在同一任务 epoch、镜像和 lock 下复用 `/cache`，最长 300 秒，到期销毁；它不会跨租户/epoch 共享。返回 session 的调用按完整生命周期 CPU 上限计费。独立验收始终创建新的容器；构建与验收在这个容器内顺序运行，单个参数使用 shell 安全引用，既不丢失构建输出，也不复用 agent 的运行进程。构建输出须写入 `/cache` 或 `/scratch`，源码只读。

验收条件支持文件存在、文件摘要和输出包含；任务可通过 `task.acceptance_conditions` 增加条件。`criteria` 保留为人类说明，不会被声称已经自动证明。必需 deliverables 是 patch/test_report/summary 的类型枚举；`task.publication` 要求指定工具/效果摘要的已确认业务回执，本地测试通过不能代替发布成功。尚未产生的远程发布需要通过远程动作及对账链路获得回执，不会自动伪造。

既有测试、依赖锁、pytest/Node 测试配置、conftest、CI 配置和注册 protected_paths 受到完整树对照保护，新增治理配置也受保护。默认退出 0 为通过、1 为业务失败；超时、截断、未配置退出码和基础设施错误为 INCONCLUSIVE。pytest 2–5 等环境/收集失败不会直接计为业务验收 FAIL。业务失败可进入有限修复；硬约束失败停止；环境不可判定暂停。隐藏测试正文和输出不会进入公开证据。

EvidenceBundle v2 包含开始/完成时间、耗时、固定 commit、镜像和 lock、实际沙箱配置、TaskSpec ID、输入/产物版本、semantic 摘要、执行命令、构建结果、日志对象摘要及引用。日志作为独立产物下载；隐藏验收日志先脱敏后保存公开记录。fixture 环境明确记录为 AST，不能当作容器执行证据。

## 资源与管理器

Budget 增加 `max_cpu_seconds`、`max_storage_bytes`，并在 root 账本中由父子任务共同消耗。CPU 调度前扣除最坏上限，可信耗时返回后返还差额；中断时保留保守扣费，session 不返还其生命周期上限。检查 root 创建时间，限制子任务和执行超时。持久对象按 run/digest 去重收费，包括基线、模型上下文/响应、workspace、动作回执和交付证据；上传失败保留预扣，同字节重试不重复扣费。临时工作树另有单快照大小上限，tmpfs 按容器内存和容量限额管理；旧 epoch 与历史对象保留/回收由后续维护模块处理。

共享部署禁止 Worker 直接使用 Docker，必须设置 `FORGE_SANDBOX_MANAGER_URL=https://...` 和至少 32 字符的 `FORGE_SANDBOX_MANAGER_SECRET`。独立进程运行 `uv run uvicorn forgeagent.sandbox_manager:app --host 127.0.0.1 --port 8010`，由 TLS 反向代理接入；管理器设置 `FORGE_SANDBOX_RUNTIME=runsc`，只挂载受管理 workspace。Worker 仅需共享 workspace 路径，不需 Docker socket。签名凭据最多 300 秒，并绑定操作类型和完整请求摘要；执行 operation 持久落盘，重复请求返回已持久回执，未决/冲突请求不能重复 dispatch。

凭据代理 `/credentials/proxy` 仅支持预配置 HTTPS GET 路由，不跟随重定向，响应上限 1MiB。`FORGE_CREDENTIAL_ROUTES` 将路由映射为 `{url, token_env}`，真实上游 token 只位于管理器环境；调用者使用绑定 route 的短期签名，响应中的 token 会脱敏。沙箱仍保持断网，不获得上游长效凭据；有写效果的网络工具继续经过审批与对账机制。

本地 PostgreSQL、真实 Git 和管理器协议回归用于代码验证。Linux Docker/gVisor、符号链接、依赖镜像及真实外部服务仍需要发布环境门禁；当前没有这些环境，不将 mock 或 fixture 声称为真实联调通过。
