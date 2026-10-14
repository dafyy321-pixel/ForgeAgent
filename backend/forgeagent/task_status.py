"""User-facing task status, kept separate from machine phases and diagnostics."""


def presentation(run):
    reason = run.state.get("reason") or ""
    code = reason.split(":", 1)[0]
    code = code if code and code.replace("_", "").isupper() and " " not in code else None
    messages = {
        "QUEUED": "等待执行资源", "SUCCEEDED": "验收已通过", "FAILED": "任务失败，现场已保留",
        "CANCELLED": "任务已取消", "CANCELLING": "正在保存现场并取消任务", "PAUSED": "任务已暂停，请检查恢复条件",
        "WAITING": "等待工具、审批或子任务结果",
    }
    phases = {"INITIALIZING": "正在准备工作区", "PLANNING": "正在阅读项目并整理方案",
              "EXECUTING": "正在执行修改", "VERIFYING": "正在运行独立验收", "DIAGNOSING": "正在收集证据诊断故障",
              "RECOVERING": "正在恢复执行条件", "DISCUSSING": "正在整理回复"}
    message = messages.get(run.status, phases.get(run.phase, "正在处理任务"))
    action = None
    if run.status == "PAUSED":
        known = {
            "MODEL_NOT_CONFIGURED": ("尚未配置模型，请联系管理员完成模型配置", "查看模型配置说明", "/settings"),
            "PERMISSION_REVOKED": ("执行授权已撤销，请联系管理员检查权限", "查看权限设置", "/settings"),
            "BUDGET_EXHAUSTED": ("任务额度已用尽，请检查费用与剩余额度", "查看任务诊断", f"/tasks/{run.id}?view=recovery"),
        }
        if code in known:
            message, label, href = known[code]
            action = {"label": label, "href": href}
        elif run.state.get("execution_id"):
            message = "方案已确认，执行任务已创建"
        elif run.state.get("plan_ready"):
            message = "方案已整理好，等待你确认"
        elif run.state.get("input_required"):
            message = "等待你的回复"
    return {"reason_code": code, "reason_detail": reason, "user_message": message, "next_action": action}
