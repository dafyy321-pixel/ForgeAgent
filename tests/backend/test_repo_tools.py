import base64

import pytest
from forgeagent import repo_tools
from forgeagent.domain import Fault, digest
from forgeagent.sandbox import sandbox
from forgeagent.workspace import entry, files


@pytest.fixture
def workspace(tenant):
    content = {"src/main.py": "def first():\n    return 1\n\ndef second():\n    return 2\n",
               "src/raw.bin": entry(b"\x00\xff\x01"), "src/empty": "", "tests/protected.py": "assert True\n"}
    return sandbox.restore(tenant, "tools-fixture", 1, content), content, {"task": {"allowed_paths": ["src"]}}


def test_range_search_symbols_and_binary_recall(workspace):
    root, content, state = workspace
    read, changed = repo_tools.execute(root, "repo.read", {"path": "src/main.py", "line_start": 3, "max_lines": 2}, state)
    assert changed is None and read["content"] == "def second():\n    return 2\n"
    assert read["digest"] == digest(content["src/main.py"].encode()) and read["total_lines"] == 5
    result, _ = repo_tools.execute(root, "repo.search", {"query": "return", "glob": "src/*.py", "max_matches": 1}, state)
    assert result["matches"][0]["line"] == 2 and result["truncated"]
    symbols, _ = repo_tools.execute(root, "repo.symbols", {"path": "src/main.py", "query": "second"}, state)
    assert symbols["symbols"] == [{"name": "second", "kind": "FunctionDef", "line": 4, "end_line": 5}]
    binary, _ = repo_tools.execute(root, "repo.read", {"path": "src/raw.bin", "encoding": "base64"}, state)
    assert base64.b64decode(binary["content"]) == b"\x00\xff\x01"
    with pytest.raises(Fault, match="base64"):
        repo_tools.execute(root, "repo.read", {"path": "src/raw.bin"}, state)


def test_move_delete_and_binary_write_preserve_file_semantics(workspace):
    root, content, state = workspace
    _, changed = repo_tools.execute(root, "repo.move", {"path": "src/raw.bin", "destination": "src/moved.bin",
                                      "expected_digest": digest(b"\x00\xff\x01")}, state)
    assert "src/raw.bin" not in changed and changed["src/moved.bin"] == content["src/raw.bin"]
    _, changed = repo_tools.execute(root, "repo.delete", {"path": "src/empty", "expected_digest": digest(b"")}, state)
    assert "src/empty" not in changed
    receipt, changed = repo_tools.execute(root, "repo.write", {"path": "src/moved.bin",
        "content": base64.b64encode(b"\xffNEW").decode(), "encoding": "base64", "expected_digest": digest(b"\x00\xff\x01")}, state)
    assert receipt["digest"] == digest(b"\xffNEW") and changed["src/moved.bin"] == entry(b"\xffNEW")
    assert sandbox.check_patch(content, changed, sandbox.patch(content, changed))["status"] == "passed"


def test_patch_cas_scope_and_actual_git_application(workspace):
    root, before, state = workspace
    after = {**before, "src/main.py": "def first():\n    return 3\n", "src/new": ""}
    patch = sandbox.patch(before, after)
    receipt, changed = repo_tools.execute(root, "repo.apply_patch", {"patch": patch,
                                             "expected_workspace_digest": digest(before)}, state)
    assert changed == after and set(receipt["changed_paths"]) == {"src/main.py", "src/new"}
    with pytest.raises(Fault, match="Workspace changed"):
        repo_tools.execute(root, "repo.apply_patch", {"patch": patch, "expected_workspace_digest": digest(before)}, state)
    forbidden = {**after, "tests/protected.py": "weakened"}
    with pytest.raises(Fault, match="outside task"):
        repo_tools.execute(root, "repo.apply_patch", {"patch": sandbox.patch(after, forbidden),
                                                   "expected_workspace_digest": digest(after)}, state)
    assert files(root) == after


def test_delete_move_and_write_require_current_preimages(workspace):
    root, content, state = workspace
    for tool, args in [
        ("repo.delete", {"path": "src/main.py", "expected_digest": digest(b"wrong")}),
        ("repo.move", {"path": "src/empty", "destination": "src/main.py", "expected_digest": digest(b"")}),
        ("repo.write", {"path": "src/main.py", "content": "changed", "expected_digest": "absent"}),
    ]:
        with pytest.raises(Fault, match="digest changed"):
            repo_tools.execute(root, tool, args, state)
        assert files(root) == content


def test_attributes_cannot_normalize_away_snapshot_bytes():
    before = {".gitattributes": "*.txt text eol=crlf\n", "a.txt": "old\r\n"}
    after = {**before, "a.txt": "new\r\nmore\n"}
    patch = sandbox.patch(before, after)
    assert sandbox.check_patch(before, after, patch)["status"] == "passed"
