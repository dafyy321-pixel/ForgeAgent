"""Bounded repository operations with explicit preimages and declared file scope."""

import base64
import fnmatch
import tempfile
from pathlib import Path

from .domain import Fault, digest
from .sandbox import git, raw_attributes
from .workspace import body, clear_tree, entry, files, mode, safe_path, text, validate_manifest, write_tree


def scope(name, state):
    if not any(name == p or name.startswith(p.rstrip("/") + "/") for p in state["task"]["allowed_paths"]):
        raise Fault("SCOPE_DENIED", "Mutation outside task allowed paths", 403)


def publish(root, before, after, state):
    validate_manifest(after)
    changed = sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))
    for name in changed:
        scope(name, state)
    clear_tree(root)
    write_tree(root, after)
    return {"exit_code": 0, "changed_paths": changed, "workspace_digest": digest(after)}, files(root)


def execute(root, tool, args, state):
    if tool == "repo.read":
        path = safe_path(root, args["path"])
        data = path.read_bytes()
        if len(data) > 8 * 1024 * 1024:
            raise Fault("READ_LIMIT", "File exceeds bounded range-reader size", 422)
        receipt = {"exit_code": 0, "path": args["path"], "digest": digest(data), "bytes": len(data)}
        if args.get("encoding") == "base64":
            receipt.update(content=base64.b64encode(data[:65536]).decode(), encoding="base64", truncated=len(data) > 65536)
        else:
            try:
                lines = data.decode("utf-8").splitlines(keepends=True)
            except UnicodeDecodeError:
                raise Fault("BINARY_FILE", "Choose base64 encoding for a binary file", 422)
            start, count = args.get("line_start", 0), args.get("max_lines", 200)
            selected = "".join(lines[start:start + count])
            selected_bytes = selected.encode("utf-8")
            receipt.update(content=selected_bytes[:65536].decode("utf-8", errors="ignore"), encoding="utf8", line_start=start, total_lines=len(lines),
                           truncated=start + count < len(lines) or len(selected_bytes) > 65536)
        return receipt, None
    before = files(root)
    if tool == "repo.list":
        return {"files": sorted(before), "entries": [{"path": p, "mode": mode(v), "bytes": len(body(v))}
                 for p, v in before.items()], "exit_code": 0}, None
    if tool == "repo.search":
        matches, truncated = [], False
        for name, value in before.items():
            if not fnmatch.fnmatchcase(name, args.get("glob", "*")) or mode(value) == "120000":
                continue
            try:
                lines = body(value).decode("utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            for line, content in enumerate(lines):
                if args["query"] in content:
                    if len(matches) >= args.get("max_matches", 100):
                        truncated = True
                        break
                    matches.append({"path": name, "line": line + 1, "text": content[:300]})
            if truncated:
                break
        return {"exit_code": 0, "matches": matches, "truncated": truncated,
                "source_digest": digest([digest(before), args["query"], matches])}, None
    if tool == "repo.symbols":
        from .code_index import analyze

        if args["path"] not in before:
            raise Fault("NOT_FOUND", "Source path does not exist", 404)
        source = text(before, args["path"])
        if len(source.encode()) > 1024 * 1024:
            raise Fault("READ_LIMIT", "Symbol query exceeds bounded source size")
        index = analyze(args["path"], source)
        symbols = index["symbols"]
        selected = [s for s in symbols if args.get("query", "") in s["name"]]
        return {"exit_code": 0, "symbols": selected[:200], "truncated": len(selected) > 200,
                "parser": index["parser"], "imports": index["imports"], "references": index["references"],
                "source_digest": digest([body(before[args["path"]]), selected[:200]])}, None
    after = dict(before)
    if tool == "repo.apply_patch":
        if args["expected_workspace_digest"] != digest(before):
            raise Fault("PRECONDITION_FAILED", "Workspace changed before patch application")
        with tempfile.TemporaryDirectory(prefix="forge-tool-patch-") as temporary:
            stage = Path(temporary)
            object_format = git(root, "rev-parse", "--show-object-format").decode().strip() if (root / ".git").exists() else "sha1"
            git(stage, "init", "--quiet", "--object-format=" + object_format)
            write_tree(stage, before)
            raw_attributes(stage, before)
            git(stage, "apply", "--check", "--whitespace=nowarn", "-", input=args["patch"].encode())
            git(stage, "apply", "--whitespace=nowarn", "-", input=args["patch"].encode())
            git(stage, "apply", "--cached", "--whitespace=nowarn", "-", input=args["patch"].encode())
            for name in git(stage, "ls-files", "-z").split(b"\x00"):
                if name:
                    safe_path(stage, name.decode("utf-8"), allow_leaf_link=True)
            after = files(stage)
    else:
        name = args["path"].replace("\\", "/")
        safe_path(root, name, allow_leaf_link=tool in {"repo.delete", "repo.move"})
        current = digest(body(before[name])) if name in before else "absent"
        if tool == "repo.write":
            try:
                data = base64.b64decode(args["content"], validate=True) if args.get("encoding") == "base64" else args["content"].encode()
            except ValueError as exc:
                raise Fault("INVALID_ENCODING", "Invalid base64 write body", 422) from exc
            selected_mode = args.get("mode") or (mode(before[name]) if name in before else "100644")
            value = entry(data, selected_mode)
            if current != args["expected_digest"] and before.get(name) != value:
                raise Fault("PRECONDITION_FAILED", "File digest changed")
            after[name] = value
        elif tool in {"repo.delete", "repo.move"}:
            if current != args["expected_digest"]:
                raise Fault("PRECONDITION_FAILED", "Source digest changed")
            value = after.pop(name)
            if tool == "repo.move":
                destination = args["destination"].replace("\\", "/")
                safe_path(root, destination, allow_leaf_link=True)
                target = digest(body(before[destination])) if destination in before else "absent"
                if target != args.get("expected_target_digest", "absent"):
                    raise Fault("PRECONDITION_FAILED", "Destination digest changed")
                after[destination] = value
        else:
            raise Fault("TOOL_UNAVAILABLE", "Unknown repository operation")
    receipt, changed = publish(root, before, after, state)
    if tool == "repo.write":
        receipt.update(path=name, digest=digest(body(after[name])))
    return receipt, changed
