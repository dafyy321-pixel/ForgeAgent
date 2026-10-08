import os

import pytest
from forgeagent import db, repository
from forgeagent.domain import Fault, digest
from forgeagent.sandbox import git, sandbox
from forgeagent.workspace import entry, files, validate_manifest


def make_repository(path, object_format="sha1"):
    path.mkdir()
    git(path, "init", "--quiet", "--object-format=" + object_format)
    (path / "src").mkdir()
    (path / "src" / "main.py").write_text("print('fixed commit')\n", encoding="utf-8", newline="")
    (path / "asset.bin").write_bytes(b"\x00\xff\x01\xfe")
    (path / "empty").write_bytes(b"")
    (path / "run.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8", newline="")
    git(path, "add", "--all")
    git(path, "update-index", "--chmod=+x", "--", "run.sh")
    git(path, "-c", "user.name=Forge fixture", "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "fixed")
    return repository.bundle_from_directory(path)


def test_fixed_commit_import_and_isolated_worktree_roundtrip(tenant, tmp_path):
    spec = make_repository(tmp_path / "source")
    (tmp_path / "source" / "src" / "main.py").write_text("uncommitted working copy", encoding="utf-8")
    spec2 = repository.bundle_from_directory(tmp_path / "source")
    assert spec2.commit == spec.commit
    pinned, baseline = repository.register(tenant, "git-fixture", spec)
    assert baseline["src/main.py"] == "print('fixed commit')\n"
    assert baseline["asset.bin"] == entry(b"\x00\xff\x01\xfe")
    assert baseline["run.sh"]["mode"] == "100755"
    first = sandbox.restore(tenant, "first", 1, baseline, pinned)
    second = sandbox.restore(tenant, "second", 1, baseline, pinned)
    assert git(first, "rev-parse", "HEAD").decode().strip() == spec.commit
    assert files(first) == files(second) == baseline
    changed = {**baseline, "asset.bin": entry(b"\xff\x00NEW"), "src/main.py": "print('edited')\n"}
    changed.pop("empty")
    first = sandbox.restore(tenant, "first", 1, changed, pinned)
    assert files(first) == changed and files(second) == baseline
    assert git(first, "rev-parse", "HEAD").decode().strip() == spec.commit
    patch = sandbox.patch(baseline, changed)
    assert "GIT binary patch" in patch and "deleted file mode" in patch
    assert sandbox.check_patch(baseline, changed, patch)["status"] == "passed"
    assert not list(first.parent.glob("*.stage-*")) and not list(first.parent.glob("*.backup-*"))


def test_executable_mode_change_is_preserved_by_delivered_patch():
    baseline = {"run.sh": entry(b"#!/bin/sh\nexit 0\n", "100755")}
    changed = {"run.sh": "#!/bin/sh\nexit 0\n"}
    patch = sandbox.patch(baseline, changed)
    assert "old mode 100755" in patch and "new mode 100644" in patch
    assert sandbox.check_patch(baseline, changed, patch)["status"] == "passed"


@pytest.mark.parametrize("before,after", [(entry(b"\xffOLD"), "new"), ("old", entry(b"\xffNEW")),
                                        (entry(b"\xffOLD"), entry(b"\xffNEW"))])
def test_non_utf8_transitions_and_unicode_binary_paths(before, after):
    baseline, current = {"文件 空格.bin": before}, {"文件 空格.bin": after}
    patch = sandbox.patch(baseline, current)
    assert "GIT binary patch" in patch
    assert sandbox.check_patch(baseline, current, patch)["status"] == "passed"


@pytest.mark.parametrize("name", ["a[1].bin", "!hash.bin", "#hash.bin"])
def test_binary_attribute_patterns_are_literal(name):
    baseline, current = {name: entry(b"\xffOLD")}, {name: entry(b"\xffNEW")}
    patch = sandbox.patch(baseline, current)
    assert sandbox.check_patch(baseline, current, patch)["status"] == "passed"


def test_sha256_fixed_commit_worktree_and_patch(tenant, tmp_path):
    spec = make_repository(tmp_path / "sha256", "sha256")
    assert len(spec.commit) == 64
    pinned, baseline = repository.register(tenant, "sha256-fixture", spec)
    root = sandbox.restore(tenant, "sha256-run", 1, baseline, pinned)
    assert files(root) == baseline and git(root, "rev-parse", "HEAD").decode().strip() == spec.commit
    current = {**baseline, "src/main.py": "changed\n"}
    assert sandbox.check_patch(baseline, current, sandbox.patch(baseline, current, pinned), pinned)["status"] == "passed"


def test_repository_api_derives_baseline_from_pinned_commit(client, tenant, tmp_path):
    spec = make_repository(tmp_path / "source")
    response = client.post("/v1/projects", json={"id": "git-project", "name": "Git project",
        "repository": spec.model_dump(), "acceptance_id": "python@1", "verification_argv": ["python", "src/main.py"]})
    assert response.status_code == 201, response.text
    with db.transaction(tenant) as s:
        project = db.get(s, db.Project, tenant, "git-project")
        assert project.data["repository"]["commit"] == spec.commit
        assert project.data["baseline_digest"] == digest(project.data["baseline"])
    assert client.post("/v1/projects", json={"id": "conflict", "name": "Conflict", "repository": spec.model_dump(),
        "baseline": {"injected": "not in commit"}, "acceptance_id": "python@1",
        "verification_argv": ["python", "src/main.py"]}).status_code == 422


@pytest.mark.parametrize("manifest", [
    {"../outside": "x"}, {".git/config": "x"}, {"./same": "x", "same": "y"},
    {"src": "x", "src/file": "y"}, {"link": entry(b"../outside", "120000")},
    {"link": entry(b".env", "120000")}, {"link": {"data": "!", "mode": "100644"}},
])
def test_manifest_rejects_escaping_or_ambiguous_entries(manifest):
    with pytest.raises((Fault, ValueError)):
        validate_manifest(manifest)


def test_safe_symlink_manifest_roundtrip_when_host_supports_links(tenant, tmp_path):
    try:
        os.symlink("target", tmp_path / "probe")
    except OSError:
        pytest.skip("Host cannot create symlinks; Linux release gate must exercise this file semantic")
    baseline = {"target": "old", "link": entry(b"target", "120000")}
    changed = {"target": "new", "link": entry(b"target", "120000")}
    root = sandbox.restore(tenant, "link-fixture", 1, baseline)
    assert files(root) == baseline
    assert sandbox.check_patch(baseline, changed, sandbox.patch(baseline, changed))["status"] == "passed"
