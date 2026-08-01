#!/usr/bin/env python3
"""Compatibility wrapper for the V4 contract-isolation transformer."""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRANSFORMER = ROOT / "tools" / ".one_time_v4_contract_isolation_fix.py"


def load_transformer():
    spec = importlib.util.spec_from_file_location("v4_contract_fix_runtime", TRANSFORMER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {TRANSFORMER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def patch_constraints(module) -> None:
    path = module.MARKET / "v5_task_constraints.py"
    source = path.read_text(encoding="utf-8")
    old_units = '    r"kilometers?|kg|people|times?)(?![A-Za-z0-9_])",\n'
    new_units = (
        '    r"kilometers?|kg|people|times?|元|块|人民币|rmb|cny|yuan|美元|美金|usd)"\n'
        '    r"(?![A-Za-z0-9_])",\n'
    )
    if source.count(old_units) != 1:
        raise RuntimeError(
            f"expected one quantity unit marker, got {source.count(old_units)}"
        )
    source = source.replace(old_units, new_units, 1)
    old_alias = '''        "sla": "sla",
        "%": "%",
'''
    new_alias = '''        "sla": "sla",
        "元": "yuan",
        "块": "yuan",
        "人民币": "yuan",
        "rmb": "yuan",
        "cny": "yuan",
        "yuan": "yuan",
        "美元": "usd",
        "美金": "usd",
        "usd": "usd",
        "%": "%",
'''
    if source.count(old_alias) != 1:
        raise RuntimeError(
            f"expected one quantity alias marker, got {source.count(old_alias)}"
        )
    path.write_text(source.replace(old_alias, new_alias, 1), encoding="utf-8")


def replace_nested_class_method(
    path: Path,
    name: str,
    replacement: str,
) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    matches: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for top_level in tree.body:
        if not isinstance(top_level, ast.ClassDef):
            continue
        for node in top_level.body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name
            ):
                matches.append(node)
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one class method {name} in {path}, got {len(matches)}"
        )
    node = matches[0]
    start_line = node.lineno
    if node.decorator_list:
        start_line = min(value.lineno for value in node.decorator_list)
    lines = source.splitlines(keepends=True)
    lines[start_line - 1 : node.end_lineno] = [replacement.rstrip() + "\n\n"]
    path.write_text("".join(lines), encoding="utf-8")


def main() -> int:
    module = load_transformer()
    module.patch_constraints = lambda: patch_constraints(module)
    original_replace = module.replace_top_level_function

    def replace_function(path: Path, name: str, replacement: str) -> None:
        if name == "_actual_company_audit":
            replace_nested_class_method(path, name, replacement)
            return
        original_replace(path, name, replacement)

    module.replace_top_level_function = replace_function
    return module.main()


if __name__ == "__main__":
    raise SystemExit(main())
