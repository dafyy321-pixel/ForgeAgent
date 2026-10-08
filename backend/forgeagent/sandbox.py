import asyncio
import os
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from .config import settings
from .domain import Fault, digest, uid

DENIED = {".git", ".env", ".ssh", ".aws", ".forge", "node_modules", "__pycache__"}


def safe_path(root: Path, relative: str):
    path = PurePosixPath(relative.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or ":" in relative or any(p in DENIED for p in path.parts):
        raise Fault("PATH_DENIED", "Path is outside the permitted workspace", 403)
    result = root.joinpath(*path.parts)
    # Do not follow symlinks or junctions, even if their current target is inside the workspace.
    for parent in [result, *result.parents]:
        if parent == root.parent:
            break
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise Fault("PATH_DENIED", "Workspace links are not supported", 403)
    if not result.resolve().is_relative_to(root.resolve()):
        raise Fault("PATH_DENIED", "Workspace escape", 403)
    return result


def files(root):
    result = {}
    total = 0
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if any(p in DENIED for p in path.relative_to(root).parts):
            continue
        safe_path(root, rel)
        if path.is_file():
            body = path.read_bytes()
            total += len(body)
            if total > settings.max_object_bytes // 2:
                raise Fault("WORKSPACE_LIMIT", "Workspace snapshot exceeds configured byte limit")
            try:
                result[rel] = body.decode("utf-8")
            except UnicodeDecodeError:
                raise Fault("UNSUPPORTED_FILE", f"Text workspace provider cannot snapshot binary file: {rel}")
    return result


class Sandbox:
    def root(self, tenant, run_id, epoch):
        scope = digest(tenant)[7:23]
        return settings.data_dir.resolve() / "workspaces" / scope / run_id / str(epoch)

    def restore(self, tenant, run_id, epoch, content):
        root = self.root(tenant, run_id, epoch)
        base = settings.data_dir.resolve() / "workspaces"
        if not root.resolve().is_relative_to(base) or root.is_symlink() or root.is_junction():
            raise Fault("PATH_DENIED", "Workspace root is outside managed storage", 403)
        root.parent.mkdir(parents=True, exist_ok=True)
        stage = root.with_name(root.name + ".stage-" + uid())
        backup = root.with_name(root.name + ".backup-" + uid())
        try:
            stage.mkdir()
            write_tree(stage, content)
            if root.exists():
                os.replace(root, backup)
            try:
                os.replace(stage, root)
            except BaseException:
                if backup.exists():
                    os.replace(backup, root)
                raise
        finally:
            for path in (stage, backup):
                if path.exists():
                    shutil.rmtree(path)
        return root

    async def image_digest(self):
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "image",
                "inspect",
                settings.sandbox_image,
                "--format",
                "{{.Id}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(proc.communicate(), 20)
            if proc.returncode:
                raise Fault("SANDBOX_UNAVAILABLE", "Docker sandbox image is unavailable")
            return out.decode().strip()
        except (TimeoutError, OSError):
            raise Fault("SANDBOX_UNAVAILABLE", "Docker sandbox is unavailable")

    async def execute(self, root, argv, image, timeout=60, readonly=False):
        if not argv or len(argv) > 100 or not all(isinstance(a, str) for a in argv):
            raise Fault("INVALID_COMMAND", "argv must be a nonempty string array", 422)
        name = "forge-" + uid()
        mount = f"type=bind,src={root.resolve()},dst=/workspace" + (",readonly" if readonly else "")
        cmd = [
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=128",
            "--memory=512m",
            "--cpus=1",
            "--user=10001:10001",
            "--runtime",
            settings.sandbox_runtime,
            "--tmpfs=/tmp:rw,nosuid,nodev,size=64m",
            "--mount",
            mount,
            "-w",
            "/workspace",
            image,
            *argv,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
            )
        except OSError:
            raise Fault("SANDBOX_UNAVAILABLE", "Docker executable is unavailable")
        output = bytearray()
        truncated = False

        async def collect():
            nonlocal truncated
            while chunk := await proc.stdout.read(65536):
                remaining = 1024 * 1024 - len(output)
                output.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    truncated = True
                    return
            await proc.wait()

        timed_out = False
        try:
            await asyncio.wait_for(collect(), min(timeout, 300))
        except TimeoutError:
            timed_out = True
        finally:
            # A container is the process-group boundary, including detached descendants.
            cleanup = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", name, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            await cleanup.wait()
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
        return {
            "exit_code": proc.returncode if not timed_out and not truncated else -1,
            "output": output.decode("utf-8", errors="replace"),
            "truncated": truncated,
            "timed_out": timed_out,
        }

    def patch(self, baseline, current):
        with tempfile.TemporaryDirectory(prefix="forge-patch-") as directory:
            root = Path(directory)
            git(root, "init", "--quiet")
            write_tree(root, baseline)
            git(root, "add", "--force", "--all")
            baseline_tree = git(root, "write-tree").decode().strip()
            for name in baseline:
                safe_path(root, name).unlink()
            # File/directory replacements need the old empty directory removed first.
            for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                if ".git" not in path.relative_to(root).parts and path.is_dir():
                    path.rmdir()
            write_tree(root, current)
            git(root, "add", "--force", "--all")
            return git(root, "diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv", baseline_tree).decode()

    def check_patch(self, baseline, current, patch):
        """Check and apply the actual deliverable, then compare its complete tree with the verified tree."""
        with tempfile.TemporaryDirectory(prefix="forge-apply-") as directory:
            root = Path(directory)
            git(root, "init", "--quiet")
            write_tree(root, baseline)
            if patch:
                git(root, "apply", "--check", "--whitespace=nowarn", "-", input=patch.encode())
                git(root, "apply", "--whitespace=nowarn", "-", input=patch.encode())
            if files(root) != current:
                raise Fault("PATCH_TREE_MISMATCH", "Delivered patch does not reproduce the verified workspace")
        return {"name": "交付补丁可应用", "status": "passed", "evidence": "git apply --check; applied tree digest matches"}


def write_tree(root, content):
    for name, body in content.items():
        path = safe_path(root, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8", newline="")


def git(root, *args, input=None):
    try:
        result = subprocess.run(
            ["git", "-c", "core.autocrlf=false", "-c", "core.safecrlf=false", "-c", "core.quotePath=true", *args],
            cwd=root, input=input, capture_output=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Fault("GIT_UNAVAILABLE", "Git operation could not complete") from exc
    if result.returncode:
        raise Fault("INVALID_PATCH", "Git rejected the deliverable: " + result.stderr.decode(errors="replace")[:1000])
    return result.stdout


sandbox = Sandbox()
