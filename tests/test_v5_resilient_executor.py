import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import ExecutionGraph, GraphLimits  # noqa: E402
from v5_executor import V5ExecutionError  # noqa: E402
from v5_resilient_executor import execute_v5_graph  # noqa: E402


class TestV5ResilientExecutor(unittest.TestCase):
    @staticmethod
    def run_config():
        return SimpleNamespace(parallel_workers=4, api_key=None, model_timeout_seconds=30)

    @staticmethod
    def node(node_id, work, functions, model, provider, *, cost=0.001, failure=0.05, contract=None):
        return {
            "node_id": node_id,
            "assigned_work": [work],
            "professional_capabilities": {},
            "functions": functions,
            "prompt_profile": {"modules": ["scope_control", "structured_delivery"]},
            "reasoning_profile": {},
            "parameter_profile": {},
            "model": model,
            "provider_endpoint": f"{model}@{provider}",
            "output_contract": contract or {},
            "estimated_quality": 0.82,
            "quality_uncertainty": 0.08,
            "estimated_cost": cost,
            "failure_probability": failure,
            "request_config": {
                "provider": {
                    "order": [provider],
                    "only": [provider],
                    "allow_fallbacks": False,
                }
            },
        }

    @classmethod
    def three_node_graph(cls):
        nodes = [
            cls.node("analysis", "w1", ["analysis"], "alpha/a", "p1"),
            cls.node("red", "w2", ["adversarial"], "beta/b", "p2"),
            cls.node("final", "w3", ["synthesis"], "gamma/c", "p3"),
        ]
        return ExecutionGraph.from_mapping({
            "nodes": nodes,
            "edges": [
                {"source": "analysis", "target": "final", "relation_type": "synthesis", "payload_type": "validated-node-output", "visibility_policy": "declared-upstream-only"},
                {"source": "red", "target": "final", "relation_type": "synthesis", "payload_type": "validated-node-output", "visibility_policy": "declared-upstream-only"},
            ],
            "execution_stages": [["analysis", "red"], ["final"]],
            "entry_nodes": ["analysis", "red"],
            "final_nodes": ["final"],
            "required_work": ["w1", "w2", "w3"],
            "estimated_quality": 0.82,
            "quality_floor": 0.78,
            "estimated_total_cost": 0.003,
            "metadata": {"recovery_pool": {}},
        })

    @staticmethod
    def answer():
        return "结论、依据、假设、不确定性、风险、建议和验收条件均已明确。" * 80

    def test_optional_node_failure_yields_degraded_deliverable(self):
        def fake_call(run, payload):
            if payload["model"] == "beta/b":
                return {
                    "model": payload["model"],
                    "choices": [{"finish_reason": "stop", "message": {"content": ""}}],
                    "usage": {"cost": 0.001},
                }, 0.01
            return {
                "model": payload["model"],
                "choices": [{"finish_reason": "stop", "message": {"content": self.answer()}}],
                "usage": {"cost": 0.001},
            }, 0.01

        result = execute_v5_graph(
            self.three_node_graph(), self.run_config(), "测试任务", call_fn=fake_call,
            limits=GraphLimits(max_retries=0, max_replacements=0, max_budget_usd=0.10),
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["completion_class"], "degraded_success")
        self.assertAlmostEqual(result["required_work_coverage"], 2 / 3, places=5)
        self.assertTrue(result["final_answer"])

    def test_all_nodes_failed_remains_a_hard_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(V5ExecutionError):
                execute_v5_graph(
                    self.three_node_graph(), self.run_config(), "测试任务",
                    call_fn=lambda run, payload: (_ for _ in ()).throw(RuntimeError("429 upstream")),
                    output_dir=temp,
                    limits=GraphLimits(max_retries=0, max_replacements=0, max_budget_usd=0.10),
                )
            summary = json.loads((Path(temp) / "v5-execution-summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "failed")
            self.assertIsNone(summary["final_answer"])

    def test_risk_adjusted_budget_blocks_before_any_paid_call(self):
        graph = self.three_node_graph()
        called = {"count": 0}

        def fake_call(run, payload):
            called["count"] += 1
            raise AssertionError("preflight must stop before a paid call")

        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(V5ExecutionError):
                execute_v5_graph(
                    graph, self.run_config(), "测试任务", call_fn=fake_call, output_dir=temp,
                    limits=GraphLimits(
                        max_retries=0,
                        max_replacements=0,
                        max_budget_usd=0.002,
                        cost_risk_multiplier=4.0,
                    ),
                )
            self.assertEqual(called["count"], 0)
            preflight = json.loads((Path(temp) / "v5-preflight.json").read_text(encoding="utf-8"))
            self.assertIn("preflight-risk-adjusted-cost-above-hard-budget", preflight["blockers"])

    def test_429_uses_different_provider_replacement(self):
        selected = self.node("final", "w1", ["synthesis"], "alpha/a", "p1")
        replacement = {
            **self.node("replacement", "w1", ["synthesis"], "beta/b", "p2"),
            "candidate_id": "replacement",
            "coverage_keys": ["w1#0"],
            "copy_indices": [0],
            "interpretation_id": "i1",
            "provider_slug": "p2",
            "independence_groups": [],
        }
        graph = ExecutionGraph.from_mapping({
            "nodes": [selected],
            "edges": [],
            "execution_stages": [["final"]],
            "entry_nodes": ["final"],
            "final_nodes": ["final"],
            "required_work": ["w1"],
            "estimated_quality": 0.82,
            "quality_floor": 0.78,
            "estimated_total_cost": 0.001,
            "metadata": {"recovery_pool": {"final": [replacement]}},
        })
        calls = []

        def fake_call(run, payload):
            calls.append((payload["model"], payload["provider"]["order"][0]))
            if payload["model"] == "alpha/a":
                raise RuntimeError("HTTP 429 upstream provider")
            return {
                "model": payload["model"],
                "provider": "p2",
                "choices": [{"finish_reason": "stop", "message": {"content": self.answer()}}],
                "usage": {"cost": 0.001},
            }, 0.01

        result = execute_v5_graph(
            graph, self.run_config(), "测试任务", call_fn=fake_call,
            limits=GraphLimits(max_retries=0, max_replacements=1, max_budget_usd=0.10),
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(calls, [("alpha/a", "p1"), ("beta/b", "p2")])
        self.assertTrue(result["recovery_used"])

    def test_truncated_invalid_json_is_salvaged_as_degraded_text(self):
        contract = {"machine_readable_required": True, "required_fields": ["conclusion", "risks"]}
        graph = ExecutionGraph.from_mapping({
            "nodes": [self.node("final", "w1", ["synthesis"], "alpha/a", "p1", contract=contract)],
            "edges": [],
            "execution_stages": [["final"]],
            "entry_nodes": ["final"],
            "final_nodes": ["final"],
            "required_work": ["w1"],
            "estimated_quality": 0.82,
            "quality_floor": 0.78,
            "estimated_total_cost": 0.001,
            "metadata": {"recovery_pool": {}},
        })
        broken = '{"conclusion":"可执行结论","risks":"主要风险"' + "，补充分析" * 120

        def fake_call(run, payload):
            return {
                "model": payload["model"],
                "choices": [{"finish_reason": "length", "message": {"content": broken}}],
                "usage": {"cost": 0.001},
            }, 0.01

        result = execute_v5_graph(
            graph, self.run_config(), "测试任务", call_fn=fake_call,
            limits=GraphLimits(max_retries=0, max_replacements=0, max_budget_usd=0.10),
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["completion_class"], "degraded_success")
        self.assertIn("可执行结论", result["final_answer"])


if __name__ == "__main__":
    unittest.main()
