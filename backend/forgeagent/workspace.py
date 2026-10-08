"""Git-compatible file manifests, including binary blobs, executable bits and safe links."""

import base64
import json
import os
import posixpath
import stat
import tempfile
from pathlib import Path, PurePosixPath

from pydantic import Field, model_validator

from .config import settings
from .domain import Fault, Strict, uid

DENIED = {".git", ".env", ".ssh", ".aws", ".forge", "node_modules", "__pycache__"}


class FileEntry(Strict):
    data: str = Field(max_length=24_000_000)
    mode: str = Field(pattern=r"^(100644|100755|120000)$")
    encoding: str = "base64"

    @model_validator(mode="after")
    def validate_body(self):
        if self.encoding != "base64":
            raise ValueError("File entries require base64 encoding")
        try:
            base64.b64decode(self.data, validate=True)
        except ValueError:
            raise ValueError("Invalid base64 file body")
        return self


def body(entry):
    return entry.encode("utf-8") if isinstance(entry, str) else base64.b64decode(FileEntry(**entry).data, validate=True)


def mode(entry):
    return "100644" if isinstance(entry, str) else entry["mode"]


def entry(content, file_mode="100644"):
    if file_mode == "100644":
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            pass
    return {"data": base64.b64encode(content).decode("ascii"), "encoding": "base64", "mode": file_mode}


def text(content, path):
    value = content.get(path, "")
    if mode(value) == "120000":
        raise Fault("LINK_READ_DENIED", "Read the link target through an explicit regular path")
    try:
        return body(value).decode("utf-8")
    except UnicodeDecodeError:
        raise Fault("BINARY_FILE", "Use a binary-aware tool for this file", 422)


def safe_path(root: Path, relative: str, allow_leaf_link=False):
    path = PurePosixPath(relative.replace("\\", "/"))
    if not path.parts or path.is_absolute() or ".." in path.parts or ":" in relative or any(p in DENIED for p in path.parts):
        raise Fault("PATH_DENIED", "Path is outside the permitted workspace", 403)
    result = root.joinpath(*path.parts)
    for parent in [result, *result.parents]:
        if parent == root.parent:
            break
        if allow_leaf_link and parent == result and parent.is_symlink():
            continue
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise Fault("PATH_DENIED", "Workspace links cannot be followed by host tools", 403)
    try:
        contained = result.resolve().is_relative_to(root.resolve())
    except (OSError, RuntimeError):
        contained = False
    if not contained:
        raise Fault("PATH_DENIED", "Workspace escape", 403)
    return result


def validate_manifest(content):
    root = settings.data_dir.resolve() / "manifest-validation"
    seen = set()
    for name, value in content.items():
        if "\x00" in name or len(name) > 500 or PurePosixPath(name).as_posix() != name or "\\" in name:
            raise Fault("PATH_DENIED", "Manifest paths must use canonical relative POSIX names", 422)
        safe_path(root, name)
        normalized = name.replace("\\", "/")
        folded = normalized.casefold() if os.name == "nt" else normalized
        if folded in seen:
            raise Fault("PATH_COLLISION", "Workspace paths collide on this filesystem", 422)
        seen.add(folded)
        if isinstance(value, dict):
            FileEntry(**value)
    names = {name.casefold() if os.name == "nt" else name for name in content}
    if any(str(parent) in names for name in names for parent in PurePosixPath(name).parents if str(parent) != "."):
        raise Fault("PATH_COLLISION", "A file or symlink cannot also be a parent directory", 422)
    links = {}
    for name, value in content.items():
        if mode(value) != "120000":
            continue
        try:
            target = body(value).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise Fault("LINK_TARGET_DENIED", "Link targets require UTF-8 paths", 422) from exc
        if not target or target.startswith("/") or any(c in target for c in (":", "\\", "\x00")):
            raise Fault("LINK_TARGET_DENIED", "Symlink target must be a relative POSIX path", 422)
        key = name.casefold() if os.name == "nt" else name
        links[key] = target.casefold() if os.name == "nt" else target
    for name in links:
        pending, seen_links = name.split("/"), set()
        resolved = []
        while pending:
            part = pending.pop(0)
            if part in ("", "."):
                continue
            if part == "..":
                if not resolved:
                    raise Fault("LINK_TARGET_DENIED", "Symlink chain escapes the workspace", 422)
                resolved.pop()
                continue
            if part in DENIED:
                raise Fault("LINK_TARGET_DENIED", "Symlink chain enters a protected path", 422)
            candidate = posixpath.join(*resolved, part) if resolved else part
            if candidate in links:
                if candidate in seen_links:
                    raise Fault("LINK_TARGET_DENIED", "Cyclic symlink chain", 422)
                seen_links.add(candidate)
                pending = links[candidate].split("/") + pending
            else:
                resolved.append(part)
    if sum(len(body(value)) for value in content.values()) > settings.max_object_bytes // 2:
        raise Fault("WORKSPACE_LIMIT", "Workspace snapshot exceeds configured byte limit")


def index_modes(root):
    if not (root / ".git").exists():
        return {}
    from .sandbox import git

    return {item.split(b"\t", 1)[1].decode("utf-8"): item.split(b" ", 1)[0].decode()
            for item in git(root, "ls-files", "--stage", "-z").split(b"\x00") if item}


def files(root):
    result, total = {}, 0
    modes = index_modes(root)
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if any(p in DENIED for p in path.relative_to(root).parts):
            continue
        safe_path(root, rel, allow_leaf_link=True)
        if path.is_symlink():
            value = entry(os.readlink(path).encode(), "120000")
        elif path.is_file():
            data = path.read_bytes()
            actual_mode = "100755" if path.stat().st_mode & stat.S_IXUSR else "100644"
            if modes.get(rel) == "120000":
                raise Fault("LINK_ENVIRONMENT_UNAVAILABLE", "Git symlink was materialized as a regular file")
            value = entry(data, modes.get(rel, actual_mode) if os.name == "nt" else actual_mode)
        elif not path.is_dir():
            raise Fault("UNSUPPORTED_FILE", "Only regular files and safe symlinks are supported")
        else:
            continue
        total += len(body(value))
        if total > settings.max_object_bytes // 2:
            raise Fault("WORKSPACE_LIMIT", "Workspace snapshot exceeds configured byte limit")
        result[rel] = value
    validate_manifest(result)
    return result


def write_tree(root, content):
    validate_manifest(content)
    for name, value in sorted(content.items(), key=lambda item: mode(item[1]) == "120000"):
        path = safe_path(root, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if mode(value) == "120000":
            try:
                os.symlink(body(value).decode("utf-8"), path)
            except OSError as exc:
                raise Fault("LINK_ENVIRONMENT_UNAVAILABLE", "This host cannot materialize repository symlinks") from exc
        else:
            path.write_bytes(body(value))
            path.chmod(0o755 if mode(value) == "100755" else 0o644)
    if (root / ".git").exists():
        from .sandbox import git

        stream = bytearray()
        entries = []
        for ordinal, (name, value) in enumerate(content.items(), 1):
            data = body(value)
            stream.extend(f"blob\nmark :{ordinal}\ndata {len(data)}\n".encode() + data + b"\n")
            entries.append(f"M {mode(value)} :{ordinal} {json.dumps(name, ensure_ascii=False)}\n".encode())
        # Import exact blob bytes without repository/global filters or per-file subprocesses.
        # File attributes remain part of the source tree; they cannot rewrite our snapshot.
        # A temporary commit mark gives its object ID for either SHA-1 or SHA-256,
        # without a format query, separate index clearing, or shared mutable refs.
        ref = "refs/forge-import/" + uid()
        commit_mark = len(content) + 1
        stream.extend(f"commit {ref}\nmark :{commit_mark}\ncommitter Forge <snapshot@example.invalid> 1 +0000\ndata 0\n\ndeleteall\n".encode())
        stream.extend(b"".join(entries) + f"\nreset {ref}\n\ndone\n".encode())
        with tempfile.TemporaryDirectory(prefix="forge-marks-") as temporary:
            marks = Path(temporary) / "marks"
            git(root, "fast-import", "--quiet", "--done", "--export-marks=" + str(marks), input=bytes(stream))
            commit_id = next(line.split(" ", 1)[1] for line in marks.read_text().splitlines() if line.startswith(f":{commit_mark} "))
            git(root, "read-tree", commit_id)


def clear_tree(root):
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if ".git" in path.relative_to(root).parts:
            continue
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            path.rmdir()
        else:
            raise Fault("UNSUPPORTED_FILE", "Unsupported filesystem entry in workspace")
