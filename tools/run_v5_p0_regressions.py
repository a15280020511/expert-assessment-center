#!/usr/bin/env python3
"""Run mandatory V5 P0 regressions without discovery or suite pollution."""
from __future__ import annotations

import importlib.util
import sys
import traceback
import unittest
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "open-model-market"))
sys.path.insert(0, str(TESTS))

SPECS = (
    (TESTS / "test_v5_general_task_planning.py", "V5GeneralTaskPlanningTests", 5),
    (TESTS / "test_v5_planning_scenario_matrix.py", "V5PlanningScenarioMatrixTests", 4),
    (TESTS / "test_v5_general_task_full_planning.py", "V5GeneralTaskFullPlanningTests", 4),
)


def _load_module(path: Path, index: int) -> ModuleType:
    module_name = f"v5_p0_regression_{index}_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load regression module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_case(path: Path, class_name: str, expected: int, index: int):
    module = _load_module(path, index)
    case_type = getattr(module, class_name, None)
    if not isinstance(case_type, type) or not issubclass(case_type, unittest.TestCase):
        raise RuntimeError(f"{class_name} is not a unittest.TestCase in {path}")
    methods = sorted(
        name
        for name in dir(case_type)
        if name.startswith("test_") and callable(getattr(case_type, name, None))
    )
    if len(methods) != expected:
        raise RuntimeError(
            f"{class_name} expected {expected} tests but registered {len(methods)}: {methods}"
        )
    print(f"REGISTERED {class_name}: {len(methods)}", flush=True)
    return case_type, methods


def _run_case_class(
    case_type: type[unittest.TestCase],
    methods: list[str],
    result: unittest.TestResult,
) -> None:
    setup_class = getattr(case_type, "setUpClass", None)
    teardown_class = getattr(case_type, "tearDownClass", None)
    if callable(setup_class):
        setup_class()
    try:
        for method in methods:
            before_failures = len(result.failures)
            before_errors = len(result.errors)
            before_skipped = len(result.skipped)
            case = case_type(method)
            case.run(result)
            if len(result.failures) > before_failures:
                state = "FAIL"
            elif len(result.errors) > before_errors:
                state = "ERROR"
            elif len(result.skipped) > before_skipped:
                state = "SKIP"
            else:
                state = "PASS"
            print(f"{state} {case_type.__name__}.{method}", flush=True)
    finally:
        if callable(teardown_class):
            teardown_class()


def main() -> int:
    loaded: list[tuple[type[unittest.TestCase], list[str]]] = []
    expected_total = 0
    for index, (path, class_name, expected) in enumerate(SPECS):
        if not path.is_file():
            raise RuntimeError(f"missing regression file: {path}")
        case_type, methods = _load_case(path, class_name, expected, index)
        loaded.append((case_type, methods))
        expected_total += expected
    print(f"REGISTERED TOTAL: {expected_total}", flush=True)

    result = unittest.TestResult()
    try:
        for case_type, methods in loaded:
            _run_case_class(case_type, methods, result)
    except Exception:
        traceback.print_exc()
        return 1

    for case, detail in result.failures:
        print(f"FAILURE DETAIL {case.id()}\n{detail}", file=sys.stderr)
    for case, detail in result.errors:
        print(f"ERROR DETAIL {case.id()}\n{detail}", file=sys.stderr)

    print(
        "P0 REGRESSION RESULT: "
        f"run={result.testsRun}, failures={len(result.failures)}, "
        f"errors={len(result.errors)}, skipped={len(result.skipped)}",
        flush=True,
    )
    return 0 if (
        result.testsRun == expected_total
        and not result.failures
        and not result.errors
        and not result.unexpectedSuccesses
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
