import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_live_benchmark_r8 as stage_d
import v5_r8_executor as runtime
import v5_stage_d_ranking_parity as parity


class V5StageDRankingParityTests(unittest.TestCase):
    def test_paid_strategy_has_no_live_catalog_or_endpoint_replanning(self):
        source = inspect.getsource(parity.production_parity_v5_strategy)
        frozen = inspect.getsource(parity._frozen_plan)
        combined = source + frozen
        self.assertIn("zero-call-preflight", inspect.getsource(parity._frozen_plan))
        self.assertIn("selected_candidate_ids", frozen)
        self.assertIn("selected_ids != approved_ids", frozen)
        self.assertIn("live_catalog_refetched", frozen)
        self.assertNotIn("fetch_catalog(", combined)
        self.assertNotIn("fetch_live_endpoint_payloads(", combined)
        self.assertNotIn("compile_and_optimize_v5(", combined)
        self.assertNotIn("ranked[:24]", combined)
        self.assertNotIn("maximum_models=24", combined)

    def test_stage_d_calls_the_r8_entrypoint_directly(self):
        module_source = inspect.getsource(parity)
        strategy_source = inspect.getsource(parity.production_parity_v5_strategy)
        self.assertNotIn("import v5_executor", module_source)
        self.assertIn("runtime.resilient_execute_v5_graph(", strategy_source)
        self.assertNotIn("v5_executor.execute_v5_graph(", strategy_source)
        self.assertEqual(
            runtime.resilient_execute_v5_graph.__module__,
            "v5_r8_executor",
        )

    def test_output_directory_exists_before_first_evidence_write(self):
        source = inspect.getsource(parity.production_parity_v5_strategy)
        mkdir = source.index("root.mkdir(parents=True, exist_ok=True)")
        first_write = source.index("base._write_json(")
        execution = source.index("runtime.resilient_execute_v5_graph(")
        self.assertLess(mkdir, first_write)
        self.assertLess(mkdir, execution)

    def test_stage_d_root_is_resolved_from_strategy_directory(self):
        root = Path("/tmp/run/tasks/task-id/v5_joint_graph")
        self.assertEqual(parity._stage_d_root(root), Path("/tmp/run"))
        with self.assertRaises(parity.FrozenGraphEvidenceError):
            parity._stage_d_root(Path("/tmp/run/task-id"))

    def test_gate_requires_zero_call_paid_authorization(self):
        gate = {
            "gate": parity.EXPECTED_GATE,
            "status": "passed",
            "paid_inference_allowed": True,
            "model_inference_calls": 0,
            "tasks": [{
                "task_id": "task-a",
                "passed": True,
                "blockers": [],
                "exact_runtime_preflight": {"selected_candidate_ids": ["node-a"]},
            }],
        }
        approved = parity._approved_task(gate, "task-a")
        self.assertTrue(approved["passed"])
        gate["model_inference_calls"] = 1
        with self.assertRaises(parity.FrozenGraphEvidenceError):
            parity._approved_task(gate, "task-a")

    def test_frozen_resource_bundle_reads_only_preflight_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = {
                "task-interpretations.json": {"interpretations": []},
                "atomic-work-graph.json": {"graphs": []},
                "task-resource-matrix.json": {"matrices": []},
            }
            for name, value in fixtures.items():
                (root / name).write_text(json.dumps(value), encoding="utf-8")
            bundle = parity._resource_bundle(root)
            self.assertFalse(bundle["model_market_accessed"])
            self.assertEqual(bundle["task_semantics"], fixtures["task-interpretations.json"])

    def test_parity_strategy_is_installed_before_stage_d_annotation(self):
        source = inspect.getsource(stage_d.install_r8_stage_d)
        self.assertLess(source.index("ranking_parity.install()"), source.index("_annotate_v5_strategy()"))

    def test_stage_d_keeps_original_cost_and_call_safety_bounds(self):
        self.assertEqual(stage_d.MAX_STRATEGY_COST_USD, 0.25)
        self.assertEqual(stage_d.MAX_GLOBAL_COST_USD, 1.50)
        self.assertEqual(stage_d.MAX_GLOBAL_CALLS, 45)
        limits = stage_d._r8_limits(
            max_nodes=16,
            max_edges=64,
            max_stages=8,
            max_model_calls=16,
            max_retries=5,
            max_replacements=5,
            max_budget_usd=0.25,
        )
        self.assertEqual(limits.max_nodes, 9)
        self.assertEqual(limits.max_model_calls, 9)
        self.assertEqual(limits.max_retries, 1)
        self.assertEqual(limits.max_replacements, 2)


if __name__ == "__main__":
    unittest.main()
