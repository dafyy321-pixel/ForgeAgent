import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .config import settings
from .domain import Fault, digest, uid
from .telemetry import observed
from .workspace import body, clear_tree, files, import_commits, write_tree
from .workspace import safe_path as safe_path


class Sandbox:
    def __init__(self):
        self.sessions = {}

    async def expire_session(self, name, seconds):
        try:
            await asyncio.sleep(seconds)
        finally:
            cleanup = await asyncio.create_subprocess_exec("docker", "rm", "-f", name,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await cleanup.wait()
            self.sessions.pop(name, None)

    def root(self, tenant, run_id, epoch):
        scope = digest(tenant)[7:23]
        return settings.data_dir.resolve() / "workspaces" / scope / run_id / str(epoch)

    @observed("workspace.restore")
    def restore(self, tenant, run_id, epoch, content, repository=None):
        root = self.root(tenant, run_id, epoch)
        base = settings.data_dir.resolve() / "workspaces"
        if not root.resolve().is_relative_to(base) or root.is_symlink() or root.is_junction():
            raise Fault("PATH_DENIED", "Workspace root is outside managed storage", 403)
        root.parent.mkdir(parents=True, exist_ok=True)
        stage = root.with_name(root.name + ".stage-" + uid())
        backup = root.with_name(root.name + ".backup-" + uid())
        stale = [p for p in root.parent.iterdir() if p.name.startswith((root.name + ".stage-", root.name + ".backup-"))]
        installed = False
        repository_root = None
        try:
            if repository:
                from .repository import add_worktree, move_worktree

                repository_root = add_worktree(tenant, repository, stage)
                clear_tree(stage)
            else:
                stage.mkdir()
            write_tree(stage, content)
            if root.exists():
                if repository_root and (root / ".git").is_file():
                    move_worktree(repository_root, root, backup)
                else:
                    os.replace(root, backup)
            try:
                if repository_root:
                    move_worktree(repository_root, stage, root)
                else:
                    os.replace(stage, root)
                installed = True
            except BaseException:
                if backup.exists():
                    if repository_root and (backup / ".git").is_file():
                        move_worktree(repository_root, backup, root)
                    else:
                        os.replace(backup, root)
                raise
        finally:
            # Preserve the backup if both installation and rollback failed. A later restore
            # can rebuild the durable snapshot; cleanup must never delete the only old tree.
            cleanup = [stage, *stale] if installed else [stage]
            if installed or root.exists():
                cleanup.append(backup)
            for path in cleanup:
                if path.exists():
                    if path.is_symlink() or path.is_junction():
                        raise Fault("PATH_DENIED", "Restore residue contains a workspace link", 403)
                    if repository_root and (path / ".git").is_file():
                        from .repository import remove_worktree

                        remove_worktree(repository_root, path)
                    else:
                        shutil.rmtree(path)
        return root

    async def image_digest(self, image=None):
        if settings.sandbox_manager_url:
            from .sandbox_manager import remote_image

            return await remote_image(image or settings.sandbox_image)
        if settings.auth_mode != "local":
            raise Fault("SANDBOX_MANAGER_REQUIRED", "Shared image inspection requires the independent manager")
        return await self.inspect_image(image or settings.sandbox_image)

    async def inspect_image(self, image):
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "image",
                "inspect",
                image,
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

    async def execute(self, root, argv, image, timeout=60, readonly=False, build=None):
        if settings.sandbox_manager_url:
            from .sandbox_manager import remote_execute

            return await remote_execute(root, argv, image, timeout, readonly, build)
        if settings.auth_mode != "local":
            raise Fault("SANDBOX_MANAGER_REQUIRED", "Shared deployments require an independent gVisor sandbox manager")
        from .concurrency import admission

        async with admission("sandbox", settings.sandbox_concurrency) as admitted:
            if not admitted:
                raise Fault("SANDBOX_BACKPRESSURE", "Sandbox capacity exhausted; no container was started", retryable=True)
            return await self.execute_with_slot(root, argv, image, timeout, readonly, build)

    async def execute_with_slot(self, root, argv, image, timeout=60, readonly=False, build=None, operation=None):
        if not argv or len(argv) > 100 or not all(isinstance(a, str) for a in argv):
            raise Fault("INVALID_COMMAND", "argv must be a nonempty string array", 422)
        started = time.monotonic()
        name = "forge-" + (operation or uid())
        workspace_base = settings.data_dir.resolve() / "workspaces"
        tenant_scope = root.resolve().relative_to(workspace_base).parts[0] if root.resolve().is_relative_to(workspace_base) else "unscoped"
        mount = f"type=bind,src={root.resolve()},dst=/workspace" + (",readonly" if readonly else "")
        cmd = [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--name",
            name,
            "--label=forge.managed=true",
            "--label=forge.storage=" + digest(str(settings.data_dir.resolve())),
            "--label=forge.workspace=" + digest(str(root.resolve())),
            "--label=forge.tenant=" + tenant_scope,
            "--label=forge.deadline=" + str(int(time.time()) + min(timeout, 300) + 30),
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
            "--tmpfs=/scratch:rw,nosuid,nodev,size=128m,uid=10001,gid=10001",
            "--env=HOME=/scratch",
            "--env=PYTHONPYCACHEPREFIX=/scratch/pycache",
            "--env=npm_config_cache=/scratch/npm",
            "--mount",
            mount,
            "-w",
            "/workspace",
            image,
            *argv,
        ]
        persistent = bool(build and build.get("persistent_session"))
        if build and build.get("ecosystem", "none") != "none":
            if build["dependency_image"] != image:
                raise Fault("DEPENDENCY_IMAGE_MISMATCH", "Build environment differs from its locked image")
            inspection = await asyncio.create_subprocess_exec("docker", "image", "inspect", image,
                "--format={{index .Config.Labels \"forge.lock.digest\"}}",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            label, _ = await asyncio.wait_for(inspection.communicate(), 20)
            if inspection.returncode or label.decode().strip() != build["lock_digest"]:
                raise Fault("DEPENDENCY_ENVIRONMENT_UNAVAILABLE", "Prebuilt image is not labeled for this exact dependency lock")
            cache_key = digest([str(root.resolve()), image, build["lock_digest"]])[7:]
            cache_bytes = build.get("cache_bytes", 33554432)
            cmd[cmd.index("-w"):cmd.index("-w")] = [f"--tmpfs=/cache:rw,nosuid,nodev,size={cache_bytes},uid=10001,gid=10001"]
        if persistent:
            # Session lifetime is bounded by root quotas and explicit cleanup, and scoped
            # to this workspace epoch and locked environment. Verification never reuses it.
            name = "forge-session-" + cache_key[:32]
            inspect = await asyncio.create_subprocess_exec("docker", "inspect", "--format={{.State.Running}}", name,
                                                          stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            existing, _ = await inspect.communicate()
            if inspect.returncode or existing.strip() != b"true":
                start = cmd[:cmd.index(image)]
                start[start.index("--name") + 1] = name
                start += ["--detach", "--entrypoint=/bin/sh", image, "-c", "while :; do sleep 3600; done"]
                process = await asyncio.create_subprocess_exec(*start, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                _, err = await process.communicate()
                if process.returncode:
                    raise Fault("SANDBOX_UNAVAILABLE", "Persistent sandbox could not start")
                self.sessions[name] = asyncio.create_task(self.expire_session(name, min(timeout, 300)))
            elif name not in self.sessions:
                # A manager restart cannot adopt an unbounded old session.
                cleanup = await asyncio.create_subprocess_exec("docker", "rm", "-f", name,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                await cleanup.wait()
                raise Fault("SANDBOX_SESSION_EXPIRED", "Old session removed; retry with a new resource admission")
            cmd = ["docker", "exec", "-w", "/workspace", name, *argv]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
            )
        except OSError:
            raise Fault("SANDBOX_UNAVAILABLE", "Docker executable is unavailable")
        output = bytearray()
        truncated = False

        assert proc.stdout is not None
        stdout = proc.stdout

        async def collect():
            nonlocal truncated
            while chunk := await stdout.read(65536):
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
            if not persistent or timed_out or truncated or proc.returncode is None:
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
            "elapsed_seconds": time.monotonic() - started,
            "image_digest": image,
            "dependency_lock": build.get("lock_digest") if build else None,
            "session": name if persistent else None,
            "sandbox_profile": {"runtime": settings.sandbox_runtime, "network": "none", "readonly": readonly,
                                "cpu_limit": 1, "memory_bytes": 536870912, "pids_limit": 128, "uid": 10001},
        }

    async def reap(self, apply=False, scope=None):
        """Only containers owned by this manager/storage root with an expired hard deadline."""
        proc = await asyncio.create_subprocess_exec("docker", "ps", "-aq", "--filter=label=forge.managed=true",
            "--filter=label=forge.storage=" + digest(str(settings.data_dir.resolve())),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        output, _ = await asyncio.wait_for(proc.communicate(), 20)
        if proc.returncode:
            raise Fault("SANDBOX_UNAVAILABLE", "Container inventory is unavailable", 503)
        import re

        candidates = []
        for container in output.decode().split():
            if not re.fullmatch(r"[a-f0-9]{12,64}", container):
                raise Fault("SANDBOX_INVENTORY", "Invalid container inventory", 503)
            inspect = await asyncio.create_subprocess_exec("docker", "inspect", container,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            raw, _ = await asyncio.wait_for(inspect.communicate(), 20)
            if inspect.returncode:
                continue
            entry = json.loads(raw)[0]
            labels = entry.get("Config", {}).get("Labels", {}) or {}
            if (labels.get("forge.managed") != "true" or labels.get("forge.storage") != digest(str(settings.data_dir.resolve()))
                or (scope is not None and labels.get("forge.tenant") != scope)
                or not labels.get("forge.deadline", "").isdigit() or int(labels["forge.deadline"]) > time.time()):
                continue
            candidates.append(container)
            if apply:
                cleanup = await asyncio.create_subprocess_exec("docker", "rm", "-f", container,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                if await cleanup.wait():
                    raise Fault("SANDBOX_CLEANUP", "Container cleanup failed; retry the managed inventory", 503)
        return {"dry_run": not apply, "containers": candidates}

    def patch(self, baseline, current, repository=None):
        with tempfile.TemporaryDirectory(prefix="forge-patch-") as directory:
            root = Path(directory)
            patch, _ = patch_tree(root, baseline, current, repository)
            return patch

    @observed("delivery.generate_and_check")
    def delivery(self, baseline, current, repository=None):
        """Generate and independently apply the delivered bytes in one disposable Git repository."""
        with tempfile.TemporaryDirectory(prefix="forge-delivery-") as directory:
            root = Path(directory)
            patch, baseline_tree = patch_tree(root, baseline, current, repository)
            # Reset both index and worktree to the original blobs before testing the
            # actual patch. Exact-byte attributes disable filters during checkout.
            git(root, "read-tree", "--reset", "-u", baseline_tree)
            return patch, check_patch_tree(root, current, patch)

    def check_patch(self, baseline, current, patch, repository=None):
        """Check and apply the actual deliverable, then compare its complete tree with the verified tree."""
        with tempfile.TemporaryDirectory(prefix="forge-apply-") as directory:
            root = Path(directory)
            git(root, "init", "--quiet", "--object-format=" + ("sha256" if repository and len(repository["commit"]) == 64 else "sha1"))
            write_tree(root, baseline)
            raw_attributes(root, baseline, current)
            return check_patch_tree(root, current, patch)


def patch_tree(root, baseline, current, repository):
    git(root, "init", "--quiet", "--object-format=" + ("sha256" if repository and len(repository["commit"]) == 64 else "sha1"))
    baseline_tree, current_tree = import_commits(root, [baseline, current])
    raw_attributes(root, baseline, current)
    patch = git(root, "diff", "--binary", "--no-ext-diff", "--no-textconv", baseline_tree, current_tree).decode()
    return patch, baseline_tree


def check_patch_tree(root, current, patch):
    if patch:
        git(root, "apply", "--check", "--whitespace=nowarn", "-", input=patch.encode())
        git(root, "apply", "--whitespace=nowarn", "-", input=patch.encode())
        git(root, "apply", "--cached", "--whitespace=nowarn", "-", input=patch.encode())
    if files(root) != current:
        raise Fault("PATCH_TREE_MISMATCH", "Delivered patch does not reproduce the verified workspace")
    return {"name": "交付补丁可应用", "status": "passed", "evidence": "git apply --check; applied tree digest matches"}
def raw_attributes(root, *contents):
    """Temporary patch trees operate on exact Git blob bytes, without checkout filters."""
    attributes = root / ".git" / "info" / "attributes"
    attributes.parent.mkdir(parents=True, exist_ok=True)
    lines = ["* diff -text -filter -ident -working-tree-encoding"]
    for name, value in (item for content in contents for item in content.items()):
        data = body(value)
        try:
            data.decode("utf-8")
            binary = b"\x00" in data
        except UnicodeDecodeError:
            binary = True
        if binary:
            pattern = "".join("\\" + char if char in "*?[]!" else char for char in name)
            lines.append(json.dumps(pattern, ensure_ascii=False) + " -diff")
    attributes.write_text("\n".join(lines) + "\n", encoding="utf-8")


def git(root, *args, input=None):
    try:
        result = subprocess.run(
            ["git", "-c", "core.autocrlf=false", "-c", "core.safecrlf=false", "-c", "core.quotePath=true",
             "-c", "core.filemode=false", "-c", "core.symlinks=true", "-c", "core.hooksPath=" + os.devnull, *args],
            cwd=root, input=input, capture_output=True, timeout=30,
            env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Fault("GIT_UNAVAILABLE", "Git operation could not complete") from exc
    if result.returncode:
        raise Fault("INVALID_PATCH", "Git rejected the deliverable: " + result.stderr.decode(errors="replace")[:1000])
    return result.stdout


sandbox = Sandbox()
