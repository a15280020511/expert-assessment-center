import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from atomic_work_graph import AtomicWorkGraphError, build_atomic_work_graph  # noqa: E402
from resource_matrix import compile_v5_task_resources  # noqa: E402
from task_resource_artifacts import write_task_resource_artifacts  # noqa: E402
from task_semantic_compiler import compile_task_semantics  # noqa: E402


class TestV5TaskResourceCompiler(unittest.TestCase):
    @staticmethod
    def profile(**overrides):
        values = {
            "domains": ["business", "legal"],
            "primary_domain": "business",
            "secondary_domain": "legal",
            "complexity": "complex",
            "complexity_score": 5,
            "high_stakes": True,
            "chinese": True,
            "long_context": True,
            "requested_context": 32768,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    @staticmethod
    def run_config(task):
        return SimpleNamespace(task=task)

    def test_complex_task_produces_multiple_market_free_interpretations(self):
        result = compile_task_semantics(
            self.profile(),
            self.run_config("比较三个投资项目，完成财务建模、证据核验、法律风险、预测和红队反证。"),
        )
        self.assertGreaterEqual(len(result["interpretations"]), 2)
        self.assertFalse(result["phase_a_invariants"]["model_ids_read"])
        self.assertFalse(result["phase_a_invariants"]["provider_endpoints_read"])
        encoded = json.dumps(result, ensure_ascii=False).casefold()
        self.assertNotIn("selectedexpert", encoded)
        self.assertNotIn("selectedjudge", encoded)
        self.assertNotIn("fixed3+1", encoded)

    def test_every_interpretation_compiles_to_a_dag(self):
        bundle = compile_v5_task_resources(
            self.profile(),
            self.run_config("评估城市公共平台方案，比较政策、商业、法律、数据与实施风险。"),
        )
        self.assertTrue(bundle["atomic_work_graphs"]["all_graphs_are_dag"])
        for graph in bundle["atomic_work_graphs"]["graphs"]:
            self.assertTrue(graph["root_work"])
            self.assertTrue(graph["leaf_work"])
            self.assertEqual(graph["node_count"], len(graph["nodes"]))

    def test_resource_matrices_match_work_and_capability_dimensions(self):
        bundle = compile_v5_task_resources(
            self.profile(),
            self.run_config("对投资方案做定量计算、预测、证据核验和JSON结构化决策报告。"),
        )
        for matrix in bundle["resource_matrices"]["matrices"]:
            self.assertEqual(matrix["shape"]["work_count"], len(matrix["work_index"]))
            self.assertEqual(matrix["shape"]["capability_count"], len(matrix["capability_labels"]))
            self.assertEqual(len(matrix["task_resource_matrix"]), matrix["shape"]["work_count"])
            self.assertTrue(any(row["capability"] == "structured_output" for row in matrix["hard_requirements"]))
            self.assertTrue(any(label.startswith("domain:") for label in matrix["capability_labels"]))

    def test_high_stakes_work_requests_independent_copies(self):
        result = compile_task_semantics(
            self.profile(high_stakes=True),
            self.run_config("高风险政策决策，需要证据核验和独立红队反证。"),
        )
        works = [work for row in result["interpretations"] for work in row["atomic_work"]]
        self.assertTrue(any(work["independence_requirements"]["minimum_independent_copies"] >= 2 for work in works))
        self.assertTrue(any(work["independence_requirements"]["independent_execution_preferred"] for work in works))

    def test_same_input_is_reproducible(self):
        profile = self.profile()
        run = self.run_config("比较商业方案并分析财务、法律、预测和失败风险。")
        first = compile_v5_task_resources(profile, run)
        second = compile_v5_task_resources(profile, run)
        self.assertEqual(
            json.dumps(first, ensure_ascii=False, sort_keys=True),
            json.dumps(second, ensure_ascii=False, sort_keys=True),
        )

    def test_phase_a_artifacts_are_deterministic_and_complete(self):
        bundle = compile_v5_task_resources(
            self.profile(),
            self.run_config("比较投资方案，进行证据、定量、预测和风险分析。"),
        )
        with tempfile.TemporaryDirectory() as temp:
            manifest = write_task_resource_artifacts(bundle, temp)
            names = {row["name"] for row in manifest["artifacts"]}
            self.assertEqual(
                names,
                {"task-interpretations.json", "atomic-work-graph.json", "task-resource-matrix.json"},
            )
            self.assertFalse(manifest["model_market_accessed"])
            self.assertTrue((Path(temp) / "task-resource-manifest.json").exists())

    def test_cycle_is_rejected(self):
        interpretation = {
            "interpretation_id": "i",
            "strategy": "test",
            "atomic_work": [
                {"work_id": "a", "dependencies": ["b"]},
                {"work_id": "b", "dependencies": ["a"]},
            ],
        }
        with self.assertRaises(AtomicWorkGraphError):
            build_atomic_work_graph(interpretation)


if __name__ == "__main__":
    unittest.main()
