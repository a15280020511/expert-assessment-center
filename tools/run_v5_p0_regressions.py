#!/usr/bin/env python3
"""Run the mandatory V5 P0 regressions without discovery ambiguity."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "open-model-market"))
sys.path.insert(0, str(TESTS))

SPECS = (
    (
        TESTS / "test_v5_general_task_planning.py",
        "V5GeneralTaskPlanningTests",
        5,
    ),
    (
        TESTS / "test_v5_planning_scenario_matrix.py",
        "V5PlanningScenarioMatrixTests",
        4,
    ),
    (
        TESTS / "test_v5_general_task_full_planning.py",
        "V5GeneralTaskFullPlanningTests",
        4,
    ),
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


def _suite_for(path: Path, class_name: str, expected: int, index: int) -> unittest.TestSuite:
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
    print(f"REGISTERED {class_name}: {len(methods)}")
    return unittest.TestSuite(case_type(method) for method in methods)


def main() -> int:
    suite = unittest.TestSuite()
    expected_total = 0
    for index, (path, class_name, expected) in enumerate(SPECS):
        if not path.is_file():
            raise RuntimeError(f"missing regression file: {path}")
        suite.addTests(_suite_for(path, class_name, expected, index))
        expected_total += expected
    actual_total = suite.countTestCases()
    if actual_total != expected_total:
        raise RuntimeError(
            f"P0 regression total mismatch: expected={expected_total}, actual={actual_total}"
        )
    print(f"REGISTERED TOTAL: {actual_total}")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(
        "P0 REGRESSION RESULT: "
        f"run={result.testsRun}, failures={len(result.failures)}, errors={len(result.errors)}"
    )
    return 0 if result.wasSuccessful() and result.testsRun == expected_total else 1


if __name__ == "__main__":
    raise SystemExit(main())
