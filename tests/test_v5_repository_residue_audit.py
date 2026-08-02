from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"


class RepositoryResidueAuditTests(unittest.TestCase):
    def test_obsolete_local_planning_files_are_absent(self) -> None:
        forbidden = (
            MARKET / "v5_general_task_planning.py",
            MARKET / "task_semantic_compiler.py",
            MARKET / "resource_matrix.py",
            MARKET / "atomic_work_graph.py",
            MARKET / "v5_single_pass_advisory.py",
            MARKET / "team_policy.json",
            ROOT / "tools" / "v5_fixture_planning_diagnostics.py",
            ROOT / "tests" / "test_v5_general_task_planning.py",
            ROOT / "tests" / "test_v5_task_resource_compiler.py",
            ROOT / "tests" / "test_v5_planning_scenario_matrix.py",
            ROOT / "tests" / "test_v5_tabletop_production_semantics.py",
            ROOT / "tests" / "test_v5_single_pass_advisory.py",
        )
        existing = [str(path.relative_to(ROOT)) for path in forbidden if path.exists()]
        self.assertEqual([], existing)

    def test_runtime_dependency_set_has_no_numeric_planning_stack(self) -> None:
        requirements = (ROOT / "requirements-runtime.txt").read_text(
            encoding="utf-8"
        ).casefold()
        self.assertNotIn("numpy", requirements)
        self.assertNotIn("ortools", requirements)
        self.assertNotIn("scipy", requirements)

    def test_production_source_has_no_removed_imports_or_artifacts(self) -> None:
        forbidden = (
            "import resource_matrix",
            "from resource_matrix",
            "task_semantic_compiler",
            "v5_general_task_planning",
            "atomic_work_graph",
            "compile_v5_task_resources",
            "compile_task_semantics",
            "v5-task-resources.json",
            "v5-task-semantics.json",
            "v5-atomic-work-graphs.json",
            "v5-resource-matrices.json",
            "import numpy",
            "from numpy",
            "cp_model",
            "optimize_execution_graph",
            "pareto_prune",
        )
        offenders: list[str] = []
        for path in MARKET.glob("*.py"):
            text = path.read_text(encoding="utf-8").casefold()
            for fragment in forbidden:
                if fragment.casefold() in text:
                    offenders.append(f"{path.name}:{fragment}")
        self.assertEqual([], offenders)

    def test_catalog_layer_is_task_agnostic(self) -> None:
        text = (MARKET / "model_market.py").read_text(encoding="utf-8")
        for fragment in (
            "class TaskProfile",
            "def classify_task",
            "POLICY_FILE",
            "complexity_score",
            "high_stakes",
            "long_context",
        ):
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, text)

    def test_pipeline_uses_minimal_task_envelope_only(self) -> None:
        text = (MARKET / "v5_pipeline.py").read_text(encoding="utf-8")
        self.assertIn("from v5_task_envelope import build_task_envelope", text)
        self.assertIn('output / "v5-task-envelope.json"', text)
        self.assertNotIn("profile =", text)
        self.assertNotIn("resources =", text)
        self.assertNotIn("compact_resources", text)

    def test_materializer_does_not_score_or_repair(self) -> None:
        text = (MARKET / "v5_proposal_materializer.py").read_text(
            encoding="utf-8"
        ).casefold()
        for fragment in (
            "capability_weights",
            "demand_vector",
            "confidence_vector",
            "interpretation_id",
            "quality_per_cost",
            "fixed_weight",
            "repair_proposal",
        ):
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, text)
        self.assertIn('"proposal_repaired_by_validator": false', text)


if __name__ == "__main__":
    unittest.main()
