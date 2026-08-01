#!/usr/bin/env python3
"""Patch and run the one-time constitutional cleanup transformer.

The wrapper exists only on the bootstrap branch. It is fetched by GitHub Actions
and never committed to the qualified remediation branch.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRANSFORMER = ROOT / "tools" / ".one_time_constitutional_cleanup.py"


def _load_transformer():
    spec = importlib.util.spec_from_file_location(
        "one_time_constitutional_cleanup_runtime",
        TRANSFORMER,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load cleanup transformer: {TRANSFORMER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _remove_test_methods_with_install_calls(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    nodes: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for parent in tree.body:
        candidates: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
        if isinstance(parent, ast.ClassDef):
            candidates = [
                value
                for value in parent.body
                if isinstance(value, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
        elif isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            candidates = [parent]
        for node in candidates:
            if not node.name.startswith("test_"):
                continue
            if any(
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute)
                and value.func.attr == "install"
                for value in ast.walk(node)
            ):
                nodes.append(node)
    if not nodes:
        return []
    lines = source.splitlines(keepends=True)
    for node in sorted(nodes, key=lambda value: value.lineno, reverse=True):
        del lines[node.lineno - 1 : node.end_lineno]
    path.write_text("".join(lines), encoding="utf-8")
    return sorted(node.name for node in nodes)


def _rewrite_native_p0_runner(module) -> None:
    path = module.ROOT / "tools" / "run_v5_p0_regressions.py"
    source = path.read_text(encoding="utf-8")
    start = source.index("SPECS = (")
    end_marker = "\n\n\ndef _load_module"
    end = source.index(end_marker, start)
    suite = '''SPECS = (
    (TESTS / "test_v5_general_task_planning.py", "V5GeneralTaskPlanningTests", 6),
    (TESTS / "test_v5_planning_scenario_matrix.py", "V5PlanningScenarioMatrixTests", 4),
    (TESTS / "test_v5_general_task_full_planning.py", "V5GeneralTaskFullPlanningTests", 4),
    (TESTS / "test_v5_constitutional_runtime.py", "V5ConstitutionalRuntimeTests", 6),
    (TESTS / "test_v5_task_constraints.py", "TaskConstraintPolarityTests", 3),
    (TESTS / "test_v5_task_constraints.py", "ClosedWorldEvidenceTests", 5),
    (TESTS / "test_v5_task_constraints.py", "DynamicObjectiveTests", 1),
    (TESTS / "test_v5_task_constraints.py", "ActualCompanyAuditTests", 2),
    (TESTS / "test_v5_independent_artifact_revalidation.py", "IndependentArtifactRevalidationTests", 3),
)'''
    path.write_text(source[:start] + suite + source[end:], encoding="utf-8")


def main() -> int:
    module = _load_transformer()
    module.EXPLICIT_LEGACY_MODULES.add("v5_rejection_audit_policy")

    original_replace = module.replace_install_statements
    removed_methods: dict[str, list[str]] = {}

    def replace_install_statements(path: Path) -> int:
        if path.parent == module.TESTS:
            removed = _remove_test_methods_with_install_calls(path)
            if removed:
                removed_methods[str(path.relative_to(module.ROOT))] = removed
        return original_replace(path)

    module.replace_install_statements = replace_install_statements
    module.rewrite_p0_runner = lambda: _rewrite_native_p0_runner(module)

    result = module.main()
    report_path = module.MARKET / "legacy-cleanup-report.json"
    if report_path.is_file() and removed_methods:
        import json

        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["removed_install_test_methods"] = removed_methods
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return result


if __name__ == "__main__":
    raise SystemExit(main())
