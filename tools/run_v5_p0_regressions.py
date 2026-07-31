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
    (TESTS / "test_v5_quality_status_integrity.py", "V5QualityStatusIntegrityTests", 6),
    (TESTS / "test_v5_empty_output_recovery.py", "V5EmptyOutputRecoveryTests", 6),
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


def _run_direct(
    case_type: type[unittest.TestCase],
    methods: list[str],
) -> tuple[int, int, int, int]:
    passed = 0
    failures = 0
    errors = 0
    skipped = 0
    setup_class = getattr(case_type, "setUpClass", None)
    teardown_class = getattr(case_type, "tearDownClass", None)
    if callable(setup_class):
        setup_class()
    try:
        for method_name in methods:
            case = case_type(method_name)
            try:
                setup = getattr(case, "setUp", None)
                if callable(setup):
                    setup()
                getattr(case, method_name)()
            except unittest.SkipTest as exc:
                skipped += 1
                print(
                    f"SKIP {case_type.__name__}.{method_name}: {exc}",
                    flush=True,
                )
            except AssertionError:
                failures += 1
                print(
                    f"FAIL {case_type.__name__}.{method_name}\n{traceback.format_exc()}",
                    file=sys.stderr,
                    flush=True,
                )
            except Exception:
                errors += 1
                print(
                    f"ERROR {case_type.__name__}.{method_name}\n{traceback.format_exc()}",
                    file=sys.stderr,
                    flush=True,
                )
            else:
                passed += 1
                print(f"PASS {case_type.__name__}.{method_name}", flush=True)
            finally:
                teardown = getattr(case, "tearDown", None)
                if callable(teardown):
                    try:
                        teardown()
                    except Exception:
                        errors += 1
                        print(
                            f"ERROR {case_type.__name__}.{method_name}.tearDown\n"
                            f"{traceback.format_exc()}",
                            file=sys.stderr,
                            flush=True,
                        )
    finally:
        if callable(teardown_class):
            teardown_class()
    return passed, failures, errors, skipped


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

    passed = failures = errors = skipped = 0
    for case_type, methods in loaded:
        try:
            row = _run_direct(case_type, methods)
        except Exception:
            errors += len(methods)
            print(
                f"ERROR {case_type.__name__}.class_setup\n{traceback.format_exc()}",
                file=sys.stderr,
                flush=True,
            )
            continue
        passed += row[0]
        failures += row[1]
        errors += row[2]
        skipped += row[3]

    executed = passed + failures + errors + skipped
    print(
        "P0 REGRESSION RESULT: "
        f"run={executed}, passed={passed}, failures={failures}, "
        f"errors={errors}, skipped={skipped}",
        flush=True,
    )
    return 0 if (
        executed == expected_total
        and passed == expected_total
        and failures == 0
        and errors == 0
        and skipped == 0
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
