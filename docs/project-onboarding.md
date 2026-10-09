# 接入固定 Git 仓库

普通 API 与 Nginx JSON 请求限制为 2 MiB。CLI 不再把整个 Git bundle 放进 JSON：先经专用 PUT 接口流式上传，再提交 SHA256、字节数和固定 commit。专用接口、对象存储和 Nginx 上限均为 16 MiB（服务端对象限制更小时取较小值）；展开的工作区仍受约 8 MiB、20,000 文件和安全路径限制。不会无限放宽配额。

1. 管理员准备仓库和验收 JSON，含 `id`、`argv`、独立 `protected_tests`，可含 `build`：锁文件、其 sha256、无网依赖镜像摘要和构建命令。
2. 执行 `forge project register demo-v1 /path/to/repo acceptance.json --commit <完整commit> --dry-run`。检查文件数、bundle 大小、元数据大小和基线摘要；不会上传、执行代码或创建任务。
3. 去掉 `--dry-run`，CLI 上传、服务端预检后登记。也可用控制台设置页“接入 Git 仓库”：选择固定 commit 的 `.bundle` 和同一验收 JSON，先预检再登记。
4. 创建该项目的任务，选择已配置模型和预算；真实代码的测试只在配置的沙箱运行。预检不等于测试通过，环境未就绪时必须保留不可判定状态。

上传需管理员权限，校验实际字节摘要，拒绝未知或跨租户对象引用、大小/摘要不一致及非 Git 内容。相同上传内容得到相同引用，可安全重试；不接收任意对象 key。未引用的上传按既有孤儿对象保留/GC 策略回收；被项目引用的 bundle 保留。不要上传包含密钥或未审核历史的仓库。

浏览器准备 bundle 的 Git 示例：在专用克隆中把 HEAD 固定到受审 commit，然后 `git bundle create reviewed.bundle HEAD`。CLI 自动使用临时 bare 仓库，不修改原 checkout。

真实模型、依赖镜像执行和共享 TLS/S3 上传仍需对应环境联调。当前本地验证覆盖流式上限、JSON 上限、摘要、真实 Git 导入、权限与向导流程。
