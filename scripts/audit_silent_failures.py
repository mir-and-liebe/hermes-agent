#!/usr/bin/env python3
"""Audit Python source for unannotated silent exception handlers."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import sys
from typing import Iterable, Sequence

_IGNORE_PARTS = {".git", ".venv", "venv", "node_modules", "build", "dist", "__pycache__"}
_ALLOW_MARKER = "hermes-ok-silent:"
_VISIBLE_CALL_NAMES = {"report_failure", "best_effort", "degraded", "critical"}
_VISIBLE_METHOD_NAMES = {"warning", "error", "exception", "critical"}
_EMPTY_RETURNS = {"None", "False", "[]", "{}", "''", '""'}


def _iter_python_files(paths: Iterable[Path]) -> Iterable[Path]:
    for path in paths:
        if not path.exists():
            continue
        if path.is_file():
            if path.suffix == ".py" and not (_IGNORE_PARTS & set(path.parts)):
                yield path
            continue
        for child in path.rglob("*.py"):
            if _IGNORE_PARTS & set(child.parts):
                continue
            yield child


def _allowlist_reason(line: str) -> str | None:
    if _ALLOW_MARKER not in line:
        return None
    return line.split(_ALLOW_MARKER, 1)[1].strip()


def _handler_pattern(handler: ast.ExceptHandler) -> str | None:
    executable = [stmt for stmt in handler.body if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Constant) or not isinstance(stmt.value.value, str)]
    if not executable:
        return "empty"
    first = executable[0]
    if isinstance(first, ast.Pass):
        return "pass"
    if isinstance(first, ast.Continue):
        return "continue"
    if isinstance(first, ast.Return):
        value = ast.unparse(first.value) if first.value is not None else "None"
        if value in _EMPTY_RETURNS:
            return f"return {value}"
    # Also catch handlers that do assignment/comment-only setup then pass/continue/empty return.
    if len(executable) <= 3:
        for stmt in executable[1:]:
            if isinstance(stmt, ast.Pass):
                return "pass"
            if isinstance(stmt, ast.Continue):
                return "continue"
            if isinstance(stmt, ast.Return):
                value = ast.unparse(stmt.value) if stmt.value is not None else "None"
                if value in _EMPTY_RETURNS:
                    return f"return {value}"
    return None


def _returns_json_error(node: ast.AST) -> bool:
    if not isinstance(node, ast.Return) or node.value is None:
        return False
    text = ast.unparse(node.value)
    return "error" in text and ("json.dumps" in text or text.startswith("{") or "tool_error" in text)


def _is_visible_call(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in _VISIBLE_CALL_NAMES or func.id == "tool_error" or "failure" in func.id
    if isinstance(func, ast.Attribute):
        if func.attr in _VISIBLE_METHOD_NAMES:
            return True
        if func.attr in _VISIBLE_CALL_NAMES:
            return True
    return False


def _handler_is_visible(handler: ast.ExceptHandler) -> bool:
    for node in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
        if isinstance(node, ast.Call) and _is_visible_call(node):
            return True
        if _returns_json_error(node):
            return True
    return False


def audit_file(path: Path) -> list[dict[str, object]]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError) as exc:
        return [{"file": str(path), "line": 1, "pattern": "parse_error", "snippet": str(exc)}]

    lines = source.splitlines()
    findings: list[dict[str, object]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        pattern = _handler_pattern(node)
        if pattern is None:
            continue
        line = lines[node.lineno - 1] if 0 <= node.lineno - 1 < len(lines) else ""
        reason = _allowlist_reason(line)
        if reason:
            continue
        if _handler_is_visible(node):
            continue
        findings.append(
            {
                "file": str(path),
                "line": node.lineno,
                "pattern": pattern,
                "snippet": line.strip(),
            }
        )
    return findings


def audit_paths(paths: Sequence[str | Path]) -> list[dict[str, object]]:
    root = Path.cwd()
    findings: list[dict[str, object]] = []
    for file_path in _iter_python_files([Path(p) for p in paths]):
        for finding in audit_file(file_path):
            try:
                finding["file"] = str(Path(str(finding["file"])).resolve().relative_to(root.resolve()))
            except ValueError:
                finding["file"] = str(Path(str(finding["file"])).resolve())
            findings.append(finding)
    return sorted(findings, key=lambda item: (str(item["file"]), int(item["line"]), str(item["pattern"])))


def _finding_key(finding: dict[str, object]) -> tuple[str, str, str]:
    return (str(finding["file"]), str(finding["pattern"]), str(finding.get("snippet", "")))


def _load_baseline(path: Path) -> set[tuple[str, str, str]]:
    data = json.loads(path.read_text())
    raw = data.get("findings", data if isinstance(data, list) else [])
    return {_finding_key(item) for item in raw}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="Files or directories to audit")
    parser.add_argument("--write-baseline", dest="write_baseline")
    parser.add_argument("--baseline")
    parser.add_argument("--show-baseline-only", action="store_true")
    args = parser.parse_args(argv)

    paths = args.paths or ["."]
    findings = audit_paths(paths)

    if args.write_baseline:
        Path(args.write_baseline).write_text(json.dumps({"findings": findings}, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"baseline_written": args.write_baseline, "findings": len(findings)}, sort_keys=True))
        return 0

    output_findings = findings
    if args.baseline:
        baseline = _load_baseline(Path(args.baseline))
        if args.show_baseline_only:
            output_findings = [f for f in findings if _finding_key(f) in baseline]
        else:
            output_findings = [f for f in findings if _finding_key(f) not in baseline]

    print(json.dumps(output_findings, indent=2, sort_keys=True))
    return 1 if output_findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
