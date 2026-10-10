"""Bounded syntax retrieval; import edges are hints, never semantic/LSP guarantees."""

import ast
import re
import threading
from collections import OrderedDict
from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any

from tree_sitter import Language, Parser
from tree_sitter_typescript import language_tsx, language_typescript

from .domain import Fault, canonical, digest
from .telemetry import observed
from .workspace import body, mode

EXTENSIONS = {".py", ".ts", ".tsx", ".mts", ".cts"}
MAX_FILE_BYTES = 1024 * 1024
MAX_INDEX_BYTES = 2 * 1024 * 1024
MAX_INDEX_FILES = 200
PARSER_VERSION = "syntax-index@2"


class SyntaxCache:
    """Bounded process-local metadata only. Never cache source or share task scopes."""

    def __init__(self, max_entries=1024, max_bytes=8 * 1024 * 1024):
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self.entries: OrderedDict[tuple, tuple] = OrderedDict()
        self.bytes = 0
        self.lock = threading.Lock()

    def get(self, scope, path, checksum):
        with self.lock:
            key = (scope, path)
            value = self.entries.get(key)
            if value and value[0] == (PARSER_VERSION, checksum):
                self.entries.move_to_end(key)
                return deepcopy(value[1])
        return None

    def put(self, scope, path, checksum, record):
        size = len(canonical(record)) + len(str(scope).encode()) + 256
        with self.lock:
            key = (scope, path)
            old = self.entries.pop(key, None)
            if old:
                self.bytes -= old[2]
            if size > self.max_bytes or self.max_entries < 1:
                return
            self.entries[key] = ((PARSER_VERSION, checksum), deepcopy(record), size)
            self.bytes += size
            while len(self.entries) > self.max_entries or self.bytes > self.max_bytes:
                _, value = self.entries.popitem(last=False)
                self.bytes -= value[2]

    def prune(self, scope, paths):
        with self.lock:
            for key in list(self.entries):
                if key[0] == scope and key[1] not in paths:
                    self.bytes -= self.entries.pop(key)[2]

    def clear(self, scope):
        self.prune(scope, set())

    def scopes(self):
        with self.lock:
            return {key[0] for key in self.entries}


def analyze(path: str, source: str) -> dict[str, Any]:
    raw = source.encode("utf-8")
    if len(raw) > MAX_FILE_BYTES:
        raise Fault("READ_LIMIT", "Symbol query exceeds bounded source size")
    suffix = PurePosixPath(path).suffix
    symbols: list[dict[str, Any]] = []
    imports: list[str] = []
    references: set[str] = set()
    if suffix == ".py":
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise Fault("SYNTAX_ERROR", "Source cannot be parsed for symbols", 422) from exc
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                symbols.append({"name": node.name, "kind": type(node).__name__, "line": node.lineno,
                                "end_line": node.end_lineno})
            elif isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = "." * node.level + (node.module or "")
                imports.append(module)
                # from . import helper can identify a module rather than a symbol.
                imports.extend(module + ("." if node.module else "") + alias.name for alias in node.names)
            elif isinstance(node, ast.Name):
                references.add(node.id)
            elif isinstance(node, ast.Attribute):
                references.add(node.attr)
        parser = "python-ast@1"
    elif suffix in EXTENSIONS:
        grammar = language_tsx() if suffix == ".tsx" else language_typescript()
        ts_tree = Parser(Language(grammar)).parse(raw)
        if ts_tree.root_node.has_error:
            raise Fault("SYNTAX_ERROR", "Source cannot be parsed for symbols", 422)
        stack = [ts_tree.root_node]
        declarations = {"function_declaration", "class_declaration", "abstract_class_declaration",
                        "interface_declaration", "type_alias_declaration", "enum_declaration",
                        "method_definition", "method_signature", "function_signature"}
        while stack:
            item = stack.pop()
            name = item.child_by_field_name("name")
            value = item.child_by_field_name("value")
            callable_variable = item.type in {"variable_declarator", "public_field_definition"} and value is not None and value.type in {
                "arrow_function", "function_expression", "generator_function"}
            if name is not None and (item.type in declarations or callable_variable):
                symbols.append({"name": raw[name.start_byte:name.end_byte].decode(), "kind": item.type,
                                "line": item.start_point.row + 1, "end_line": item.end_point.row + 1})
            if item.type in {"import_statement", "export_statement"}:
                target = item.child_by_field_name("source")
                if target is not None:
                    imports.append(raw[target.start_byte:target.end_byte].decode()[1:-1])
            if item.type in {"identifier", "property_identifier", "type_identifier"}:
                references.add(raw[item.start_byte:item.end_byte].decode())
            stack.extend(reversed(item.named_children))
        parser = "tree-sitter-typescript@1"
    else:
        raise Fault("UNSUPPORTED_LANGUAGE", "Symbols support Python and TypeScript/TSX sources", 422)
    return {"path": path, "parser": parser, "symbols": sorted(symbols, key=lambda s: (s["line"], s["name"])),
            "imports": sorted(set(imports)), "references": sorted(references)[:2000], "digest": digest(raw)}


def resolve_import(path: str, target: str, paths: set[str]) -> list[str]:
    if path.endswith(".py"):
        level = len(target) - len(target.lstrip("."))
        parts = list(PurePosixPath(path).parent.parts) if level else []
        if level > len(parts) + 1:
            return []
        if level > 1:
            parts = parts[:-(level - 1)]
        parts.extend(target.lstrip(".").split("."))
        base = "/".join(p for p in parts if p)
        candidates = [base + ".py", base + "/__init__.py"]
    elif target.startswith("."):
        parts = list(PurePosixPath(path).parent.parts)
        for part in target.split("/"):
            if part == "..":
                if not parts:
                    return []
                parts.pop()
            elif part not in {"", "."}:
                parts.append(part)
        base = "/".join(parts)
        # Node-style TS sources often use .js specifiers.
        if base.endswith((".js", ".jsx", ".mjs", ".cjs")):
            base = base.rsplit(".", 1)[0]
        candidates = [base] + [base + ext for ext in (".ts", ".tsx", ".mts", ".cts")] + [base + "/index.ts", base + "/index.tsx"]
    else:
        return []  # Package aliases/dynamic imports need configured resolution, not guesses.
    return [candidate for candidate in candidates if candidate in paths]


@observed("context.code_index")
def retrieve(manifest: dict, query: str, policy: str, relevant_paths=(), limit=12,
             cache: SyntaxCache | None = None, cache_scope=()) -> dict[str, Any]:
    records: dict[str, dict[str, Any]] = {}
    sources: dict[str, str] = {}
    skipped: list[dict[str, str]] = []
    words = set(re.findall(r"[\w]+", query.lower())) - {"the", "and", "for", "with", "from", "fix"}
    terms = [word for word in words if len(word) > 2]
    candidates, lexical_scores = [], {}
    scanned_bytes = 0
    for path, value in sorted(manifest.items()):
        if PurePosixPath(path).suffix not in EXTENSIONS or mode(value) == "120000":
            continue
        raw = body(value)
        if len(raw) > MAX_FILE_BYTES:
            skipped.append({"path": path, "reason": "index_limit"})
            continue
        scanned_bytes += len(raw)
        try:
            source = raw.decode("utf-8")
        except UnicodeDecodeError:
            skipped.append({"path": path, "reason": "unparseable"})
            continue
        lowered = source.lower()
        score = sum(word in lowered for word in terms) + 3 * sum(word in path.lower() for word in terms)
        lexical_scores[path] = score + (8 if path in relevant_paths else 0)
        candidates.append((path, source, raw))
    if cache:
        cache.prune(cache_scope, {path for path, _, _ in candidates})
    size, attempted, hits, parsed = 0, 0, 0, 0
    for path, source, raw in sorted(candidates, key=lambda item: (-lexical_scores[item[0]], item[0])):
        if attempted >= MAX_INDEX_FILES or size + len(raw) > MAX_INDEX_BYTES:
            skipped.append({"path": path, "reason": "index_limit"})
            continue
        attempted += 1
        size += len(raw)
        checksum = digest(raw)
        record = cache.get(cache_scope, path, checksum) if cache else None
        try:
            if record is None:
                parsed += 1
                record = analyze(path, source)
                if cache:
                    cache.put(cache_scope, path, checksum, record)
            else:
                hits += 1
            records[path], sources[path] = record, source
        except Fault:
            skipped.append({"path": path, "reason": "unparseable"})
    paths = set(records)
    edges = {path: sorted({p for target in item["imports"] for p in resolve_import(path, target, paths)})
             for path, item in records.items()}
    scores = {}
    reasons: dict[str, list[str]] = {path: [] for path in records}
    for path, item in records.items():
        score = lexical_scores[path]
        if policy == "structure":
            score += 4 * sum(bool(words & set(re.findall(r"\w+", symbol["name"].lower()))) for symbol in item["symbols"])
        scores[path] = float(score)
    if policy == "structure":
        # Expand only positively matched seeds, one hop, in both directions.
        seeds = sorted((p for p in paths if scores[p] > 0), key=lambda p: (-scores[p], p))[:4]
        seed_scores = dict(scores)
        for seed in seeds:
            for candidate in paths:
                if candidate != seed and (candidate in edges[seed] or seed in edges[candidate]):
                    scores[candidate] += min(6, seed_scores[seed] * 0.75)
                    reasons[candidate].append("import_neighbor:" + seed)
    ranked = sorted((p for p in paths if scores[p] > 0), key=lambda p: (-scores[p], p))[:limit]
    items = []
    for path in ranked:
        item = records[path]
        # Anchor on a matching symbol or source line, preserving bounded line references.
        lines = sources[path].splitlines()
        anchor = next((s["line"] for s in item["symbols"] if s["name"].lower() in words), None)
        if anchor is None:
            anchor = next((i + 1 for i, line in enumerate(lines) if any(w in line.lower() for w in words if len(w) > 2)), 1)
        start = max(0, anchor - 6)
        excerpt = "\n".join(lines[start:start + 80]).encode()[:6000].decode("utf-8", errors="ignore")
        items.append({"path": path, "digest": item["digest"], "symbols": item["symbols"][:40],
                      "imports": edges[path], "score": scores[path], "reasons": reasons[path],
                      "line_start": start + 1, "excerpt": excerpt,
                      "truncated": start > 0 or len(excerpt.splitlines()) + start < len(lines)})
    return {"version": 2, "parser_version": PARSER_VERSION, "policy": policy, "workspace_digest": digest(manifest), "indexed_files": len(records),
            "eligible_files": len(candidates), "scanned_bytes": scanned_bytes, "parsed_files": parsed,
            "cache_hits": hits, "selection": "lexical_relevance_before_bounded_syntax_parse",
            "indexed_bytes": size, "skipped": skipped[:200], "skipped_count": len(skipped), "items": items,
            "limitations": "Syntax imports only; no dynamic import, package alias or semantic type resolution. Excerpts are untrusted."}
