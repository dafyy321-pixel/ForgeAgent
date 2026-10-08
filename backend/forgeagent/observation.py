"""One observation shape for tool receipts, logs, ranges and persisted references."""

from .domain import canonical, digest, now


def project(action):
    receipt = action.receipt or {}
    return {"action_id": action.id, "tool": action.tool, "args": action.args,
            "status": action.status, "ref": receipt.get("ref"), "exit_code": receipt.get("exit_code"),
            "paths": receipt.get("paths", receipt.get("changed_paths", [action.args["path"]] if "path" in action.args else [])),
            "result": receipt}


def envelope(value, tool, state):
    value = dict(value)
    text = value.get("output", value.get("content", canonical(value).decode()))
    if not isinstance(text, str):
        text = canonical(text).decode()
    value["_observation"] = {"schema_version": 1, "tool": tool, "observed_at": now().isoformat(),
                             "workspace_digest": state["workspace_digest"], "input_revision": state.get("input_revision", 0),
                             "artifact_version": state["artifact_version"], "text_digest": digest(text.encode()),
                             "text_bytes": len(text.encode()), "line_start": value.get("line_start", 0),
                             "total_lines": value.get("total_lines", len(text.splitlines())),
                             "stored_lines": len(text.splitlines()),
                             "truncated": bool(value.get("truncated"))}
    return value


def read(value, ref, start, count):
    text = value.get("output", value.get("content", canonical({k: v for k, v in value.items() if k != "_observation"}).decode()))
    if not isinstance(text, str):
        text = canonical(text).decode()
    lines = text.splitlines(keepends=True)
    selected = "".join(lines[start:start + count]).encode()
    return {"exit_code": 0, "content": selected[:65536].decode("utf-8", errors="ignore"), "line_start": start,
            "total_lines": len(lines), "source_digest": ref["digest"], "source_ref": ref,
            "text_digest": digest(text.encode()), "truncated": start + count < len(lines) or len(selected) > 65536}
