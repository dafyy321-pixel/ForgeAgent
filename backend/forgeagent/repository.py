"""Import a complete, bounded Git bundle and create isolated worktrees at a fixed commit."""

import base64
import os
import shutil
from pathlib import Path

from pydantic import Field, model_validator

from .config import settings
from .domain import Fault, Strict, digest, uid
from .storage import objects
from .workspace import entry, validate_manifest


class RepositoryInput(Strict):
    commit: str = Field(pattern=r"^[0-9a-f]{40}([0-9a-f]{24})?$")
    bundle_base64: str | None = Field(None, max_length=24_000_000)
    bundle_digest: str | None = Field(None, pattern=r"^sha256:[a-f0-9]{64}$")
    bundle_bytes: int | None = Field(None, ge=1, le=16 * 1024 * 1024)

    @model_validator(mode="after")
    def source(self):
        if (self.bundle_base64 is None) == (self.bundle_digest is None):
            raise ValueError("Choose exactly one inline bundle or uploaded bundle digest")
        if (self.bundle_digest is None) != (self.bundle_bytes is None):
            raise ValueError("Uploaded bundles require their verified byte count")
        return self


def mirror(tenant, repository):
    from .sandbox import git

    base = settings.data_dir.resolve() / "repositories"
    root = base / digest(tenant)[7:23] / repository["bundle_ref"]["digest"][7:]
    if not root.resolve().is_relative_to(base) or any(p.is_symlink() for p in [root, root.parent, base]):
        raise Fault("REPOSITORY_PATH", "Repository cache is outside managed storage")
    if root.exists():
        git(root, "cat-file", "-e", repository["commit"] + "^{commit}")
        return root
    root.parent.mkdir(parents=True, exist_ok=True)
    stage = root.with_name(root.name + ".stage-" + uid())
    bundle = root.with_name(root.name + ".bundle-" + uid())
    try:
        bundle.write_bytes(objects.get(tenant, repository["bundle_ref"]))
        object_format = "sha256" if len(repository["commit"]) == 64 else "sha1"
        git(root.parent, "init", "--bare", "--quiet", "--object-format=" + object_format, str(stage))
        git(stage, "bundle", "verify", str(bundle))
        git(stage, "fetch", "--no-tags", "--", str(bundle), "HEAD")
        git(stage, "cat-file", "-e", repository["commit"] + "^{commit}")
        if root.exists():
            return root
        os.replace(stage, root)
        return root
    finally:
        bundle.unlink(missing_ok=True)
        if stage.exists():
            shutil.rmtree(stage)


def commit_files(root, commit):
    from .sandbox import git

    records = [record for record in git(root, "ls-tree", "-rz", "--full-tree", commit).split(b"\x00") if record]
    if len(records) > 20000:
        raise Fault("REPOSITORY_LIMIT", "Repository exceeds the bounded file catalog")
    catalog = []
    for record in records:
        if not record:
            continue
        header, name = record.split(b"\t", 1)
        file_mode, kind, object_id = header.decode().split(" ")
        if kind != "blob" or file_mode not in {"100644", "100755", "120000"}:
            raise Fault("REPOSITORY_FILE_UNSUPPORTED", "Gitlinks/submodules require a separately registered fixed project", 422)
        try:
            path = name.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise Fault("REPOSITORY_PATH_ENCODING", "Repository paths require UTF-8", 422) from exc
        catalog.append((path, file_mode, object_id))
    request = "".join(object_id + "\n" for _, _, object_id in catalog).encode()
    sizes = git(root, "cat-file", "--batch-check=%(objectsize)", input=request).splitlines() if catalog else []
    if sum(int(size) for size in sizes) > settings.max_object_bytes // 2:
        raise Fault("REPOSITORY_LIMIT", "Expanded Git blobs exceed the workspace byte limit")
    blobs = git(root, "cat-file", "--batch", input=request) if catalog else b""
    content, cursor = {}, 0
    for (path, file_mode, object_id), size in zip(catalog, sizes, strict=True):
        end = blobs.index(b"\n", cursor)
        header = blobs[cursor:end].decode().split()
        length = int(size)
        if header != [object_id, "blob", str(length)]:
            raise Fault("REPOSITORY_CORRUPT", "Git blob batch differs from fixed tree")
        content[path] = entry(blobs[end + 1:end + 1 + length], file_mode)
        cursor = end + 2 + length
    validate_manifest(content)
    return content


def register(tenant, namespace, spec):
    if spec.bundle_digest:
        import hashlib

        key = f"{hashlib.sha256(tenant.encode()).hexdigest()}/repository-upload/{spec.bundle_digest[7:]}"
        # The uploader has verified this exact digest; no caller-supplied key or namespace is accepted.
        reference = {"key": key, "digest": spec.bundle_digest, "bytes": spec.bundle_bytes}
        objects.verify(tenant, reference)
    else:
        try:
            bundle = base64.b64decode(spec.bundle_base64, validate=True)
        except ValueError as exc:
            raise Fault("REPOSITORY_BUNDLE", "Invalid repository bundle encoding", 422) from exc
        reference = objects.put(tenant, namespace, bundle)
    repository = {"commit": spec.commit, "bundle_ref": reference,
                  "file_semantics": ["utf8", "binary", "executable", "safe_symlink"], "submodules": "separate_projects"}
    root = mirror(tenant, repository)
    content = commit_files(root, spec.commit)
    repository["tree"] = git_tree(root, spec.commit)
    repository["manifest_digest"] = digest(content)
    return repository, content


def git_tree(root, commit):
    from .sandbox import git

    return git(root, "rev-parse", commit + "^{tree}").decode().strip()


def bundle_from_directory(directory, commit="HEAD"):
    import tempfile

    from .sandbox import git

    source = Path(directory).resolve()
    resolved = git(source, "rev-parse", "--verify", commit + "^{commit}").decode().strip()
    # Advertise HEAD at the chosen immutable commit in a temporary bare repository.
    with tempfile.TemporaryDirectory(prefix="forge-bundle-") as temporary:
        target = Path(temporary)
        bare = target / "source.git"
        git(target, "clone", "--bare", "--no-hardlinks", "--quiet", "--", str(source), str(bare))
        git(bare, "update-ref", "refs/heads/forge-export", resolved)
        git(bare, "symbolic-ref", "HEAD", "refs/heads/forge-export")
        bundle = target / "source.bundle"
        git(bare, "bundle", "create", str(bundle), "HEAD")
        return RepositoryInput(commit=resolved, bundle_base64=base64.b64encode(bundle.read_bytes()).decode())


def add_worktree(tenant, repository, stage):
    from .sandbox import git

    root = mirror(tenant, repository)
    git(root, "worktree", "add", "--force", "--detach", "--no-checkout", str(stage), repository["commit"])
    return root


def move_worktree(repository_root, source, destination):
    from .sandbox import git

    git(repository_root, "worktree", "move", str(source), str(destination))


def remove_worktree(repository_root, path):
    from .sandbox import git

    git(repository_root, "worktree", "remove", "--force", str(path))
