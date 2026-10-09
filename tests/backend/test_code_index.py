import pytest
from forgeagent.code_index import analyze, resolve_import, retrieve
from forgeagent.context import compile_context
from forgeagent.domain import Fault, Task, digest
from forgeagent.workspace import entry


def test_typescript_real_syntax_covers_arrows_methods_types_and_tsx():
    result = analyze("src/ui.tsx", '''import { amount as money } from './money.js';
export interface Invoice { total(): number }
export type Id = string;
export const sum = (a: number, b: number) => a + b;
export class View { render() { return <div>{money(1)}</div>; } }
// function imaginary() {}
const prose = "class Fiction {}";
''')
    names = {s["name"] for s in result["symbols"]}
    assert names == {"Invoice", "total", "Id", "sum", "View", "render"}
    assert result["imports"] == ["./money.js"] and "money" in result["references"]
    assert all(s["line"] <= s["end_line"] for s in result["symbols"])
    with pytest.raises(Fault, match="parsed"):
        analyze("broken.ts", "export const = {")


def test_relative_import_resolution_respects_repository_boundary():
    paths = {"src/money.ts", "src/util/index.ts", "pkg/core.py", "pkg/helper.py"}
    assert resolve_import("src/ui.tsx", "./money.js", paths) == ["src/money.ts"]
    assert resolve_import("src/ui.tsx", "./util", paths) == ["src/util/index.ts"]
    assert resolve_import("pkg/core.py", ".helper", paths) == ["pkg/helper.py"]
    assert resolve_import("pkg/core.py", "..outside", paths) == []
    assert resolve_import("src/ui.tsx", "../../src/money", paths) == []
    assert resolve_import("src/ui.tsx", "@alias/money", paths) == []


def test_structure_context_follows_imports_and_never_reads_symlinks():
    workspace = {"src/a.ts": "import { quantize } from './z.js';\nexport const invoice = () => quantize(10);",
                 "src/b.ts": "// invoice documentation\nexport const note = 1;",
                 "src/z.ts": "export const quantize = (n: number) => n / 100;",
                 "src/secret.ts": entry(b"../../outside.ts", "120000"), "src/bad.ts": "const =", "src/raw.py": entry(b"\xff")}
    lexical = retrieve(workspace, "invoice", "lexical", limit=2)
    structural = retrieve(workspace, "invoice", "structure", limit=2)
    assert [i["path"] for i in lexical["items"]] == ["src/a.ts", "src/b.ts"]
    assert [i["path"] for i in structural["items"]] == ["src/a.ts", "src/z.ts"]
    assert structural["items"][1]["reasons"] == ["import_neighbor:src/a.ts"]
    assert structural["skipped_count"] == 2
    assert all(i["path"] != "src/secret.ts" for i in structural["items"])
    task = Task(goal="invoice", allowed_paths=["src"]).model_dump()
    state = {"semantic": {"harness": {"code_retrieval": "structure"}}}
    _, manifest = compile_context(task, state, [], [], [], 16000, 1024, workspace)
    code = [i for i in manifest["items"] if i["type"] == "code_context"]
    assert code and all(i["trust"] == "untrusted_repository" for i in code)
    assert any(i["content"]["path"] == "src/z.ts" for i in code)
    assert manifest["code_retrieval"]["workspace_digest"] == digest(workspace)
    assert manifest["estimated_tokens_upper_bound"] <= manifest["input_budget"]
    _, disabled = compile_context(task, {}, [], [], [], 16000, 1024, workspace)
    assert disabled["code_retrieval"] is None


def test_index_limits_are_explicit(monkeypatch):
    import forgeagent.code_index as index
    monkeypatch.setattr(index, "MAX_INDEX_FILES", 1)
    result = retrieve({"a.py": "def a(): pass", "b.py": "def b(): pass"}, "a", "structure")
    assert result["indexed_files"] == 1 and result["skipped"] == [{"path": "b.py", "reason": "index_limit"}]


async def test_worker_persists_the_enabled_code_context(tenant, make_run):
    from forgeagent import db
    from forgeagent.domain import Harness
    from forgeagent.worker import Worker

    run_id = make_run(harness=Harness(code_retrieval="structure"))
    await Worker().once(tenant)
    with db.transaction(tenant) as s:
        manifest = db.rows(s, db.ContextManifest, tenant, run_id=run_id)[0].data
        assert manifest["code_retrieval"]["policy"] == "structure"
        assert any(i["type"] == "code_context" and i["content"]["path"] == "src/calculator.py" for i in manifest["items"])
        assert len(db.rows(s, db.ModelCall, tenant, run_id=run_id)) == 1


async def test_input_changed_during_indexing_discards_stale_context(tenant, make_run, monkeypatch):
    from forgeagent import code_index, db
    from forgeagent.domain import Harness
    from forgeagent.worker import Worker

    run_id = make_run(harness=Harness(code_retrieval="structure"))
    original = code_index.retrieve

    def change_input(*args, **kwargs):
        result = original(*args, **kwargs)
        with db.transaction(tenant) as s:
            run = db.get(s, db.Run, tenant, run_id, True)
            run.state = {**run.state, "input": "updated while indexing", "input_revision": 1}
            db.emit(s, run, "INPUT_ADDED", "New constraint during syntax indexing")
        return result

    monkeypatch.setattr(code_index, "retrieve", change_input)
    await Worker().once(tenant)
    with db.transaction(tenant) as s:
        assert not db.rows(s, db.ModelCall, tenant, run_id=run_id)
        assert not db.rows(s, db.ContextManifest, tenant, run_id=run_id)
