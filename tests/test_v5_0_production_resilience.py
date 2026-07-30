import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_executor as executor  # noqa: E402
import v5_production_resilience as resilience  # noqa: E402
from execution_graph import (  # noqa: E402
    ExecutionGraph,
    GraphLimits,
    SelectedEdge,
    SelectedNode,
)


class TestV5ProductionResilience(unittest.TestCase):
    @staticmethod
    def node(node_id, model, work, functions, *, machine=True, cost=0.001):
        fields = ["conclusions", "assumptions", "uncertainties", "evidence_gaps"]
        return SelectedNode(
            node_id=node_id,
            assigned_work=(work,),
            professional_capabilities={"general_analysis": 0.9},
            functions=tuple(functions),
            prompt_profile={"modules": ["structured_delivery"]},
            reasoning_profile={"reasoning_enabled": True, "effort": "low"},
            parameter_profile={
                "supported_parameters": ["reasoning", "structured_outputs", "max_tokens"],
                "parameters": {"response_format": {"type": "json_object"}} if machine else {},
            },
            model=model,
            provider_endpoint=f"{model}@provider",
            output_contract={
                "required_fields": fields,
                "machine_readable_required": machine,
                "must_separate_fact_assumption_inference": True,
            },
            estimated_quality=0.82,
            quality_uncertainty=0.10,
            estimated_cost=cost,
            failure_probability=0.03,
            request_config={
                "provider": {
                    "order": ["provider"],
                    "only": ["provider"],
                    "allow_fallbacks": False,
                    "require_parameters": True,
                },
                **({"response_format": {"type": "json_object"}} if machine else {}),
            },
        )

    @classmethod
    def graph(cls, *, costs=(0.001, 0.001, 0.001)):
        failed = cls.node("support-fail", "fail/model", "risk", ("analysis",), cost=costs[0])
        passed = cls.node("support-ok", "ok/model", "evidence", ("analysis",), cost=costs[1])
        final = cls.node("final", "final/model", "delivery", ("synthesis", "delivery"), cost=costs[2])
        return ExecutionGraph(
            nodes=(failed, passed, final),
            edges=(
                SelectedEdge("support-fail", "final", "synthesis", "validated-node-output", "declared-upstream-only"),
                SelectedEdge("support-ok", "final", "synthesis", "validated-node-output", "declared-upstream-only"),
            ),
            execution_stages=(("support-fail", "support-ok"), ("final",)),
            entry_nodes=("support-fail", "support-ok"),
            final_nodes=("final",),
            required_work=("risk", "evidence", "delivery"),
            estimated_quality=0.82,
            quality_floor=0.75,
            estimated_total_cost=sum(costs),
            metadata={"recovery_pool": {}},
        )

    @staticmethod
    def run_config():
        return SimpleNamespace(parallel_workers=4, model_max_retries=0, model_timeout_seconds=30, api_key="test")

    @staticmethod
    def final_json():
        value = "明确结论、依据、边界、风险和执行动作。" * 15
        return json.dumps({
            "conclusions": value,
            "assumptions": value,
            "uncertainties": value,
            "evidence_gaps": value,
        }, ensure_ascii=False)

    def patched(self):
        return (
            patch.object(executor, "build_node_payload", resilience.build_node_payload),
            patch.object(executor, "quality_gate", resilience.quality_gate),
            patch.object(executor, "_reserved_attempt", resilience._reserved_attempt),
        )

    def test_failed_support_node_does_not_prevent_final_delivery(self):
        calls = []

        def fake_call(run, payload):
            calls.append(payload["model"])
            if payload["model"] == "fail/model":
                raise RuntimeError("HTTP 429 upstream rate limited")
            answer = self.final_json() if payload["model"] == "final/model" else "有效分析正文。" * 80
            return {
                "id": "response",
                "model": payload["model"],
                "provider": "provider",
                "choices": [{"finish_reason": "stop", "message": {"content": answer}}],
                "usage": {"cost": 0.001},
            }, 0.01

        p1, p2, p3 = self.patched()
        with p1, p2, p3, tempfile.TemporaryDirectory() as temp:
            result = resilience.execute_v5_graph(
                self.graph(), self.run_config(), "测试任务", call_fn=fake_call, output_dir=temp,
                limits=GraphLimits(max_retries=0, max_replacements=0),
            )
            self.assertEqual(result["status"], "success")
            self.assertTrue(result["degraded"])
            self.assertIn("support-fail", result["failed_node_ids"])
            self.assertIn("final/model", calls)
            self.assertTrue(result["execution_stages"][0]["continued_after_failure"])
            self.assertGreater(len(result["final_answer"]), 320)

    def test_intermediate_json_is_deferred_and_dynamic_caps_are_bounded(self):
        support, _, final = self.graph().nodes
        p1, _, _ = self.patched()
        with p1:
            support_payload = resilience.build_node_payload(support, "任务", [])
            final_payload = resilience.build_node_payload(final, "任务", [])
        self.assertNotIn("response_format", support_payload)
        self.assertIn("response_format", final_payload)
        support_cap = support_payload.get("max_tokens") or support_payload.get("max_completion_tokens")
        final_cap = final_payload.get("max_tokens") or final_payload.get("max_completion_tokens")
        self.assertGreaterEqual(support_cap, 1024)
        self.assertLess(support_cap, final_cap)
        self.assertLessEqual(final_cap, 10000)

    def test_conservative_preflight_stops_before_any_paid_call(self):
        calls = []

        def fake_call(run, payload):
            calls.append(payload)
            raise AssertionError("preflight should prevent calls")

        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(executor.V5ExecutionError, "no model call was made"):
                resilience.execute_v5_graph(
                    self.graph(costs=(0.05, 0.05, 0.05)), self.run_config(), "测试任务",
                    call_fn=fake_call, output_dir=temp,
                    limits=GraphLimits(max_retries=0, max_replacements=0, max_budget_usd=0.10),
                )
            self.assertFalse(calls)
            summary = json.loads((Path(temp) / "v5-execution-summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["stop_reason"], "preflight-reservation-exceeds-budget")
            self.assertEqual(summary["execution_budget"]["calls_reserved"], 0)

    def test_candidate_cost_is_hardened_before_cp_sat(self):
        candidate = {
            "candidate_id": "candidate-1",
            "interpretation_id": "interpretation-1",
            "coverage_keys": ["work-1#0"],
            "assigned_work": ["work-1"],
            "copy_indices": [0],
            "professional_capabilities": {"general_analysis": 0.9},
            "functions": ["analysis"],
            "prompt_profile": {},
            "reasoning_profile": {},
            "parameter_profile": {
                "parameters": {"response_format": {"type": "json_object"}},
                "supported_parameters": ["structured_outputs", "max_tokens"],
            },
            "model": "vendor/model",
            "provider_endpoint": "vendor/model@provider",
            "provider_slug": "provider",
            "output_contract": {
                "required_fields": ["conclusions"],
                "machine_readable_required": True,
            },
            "estimated_quality": 0.8,
            "quality_uncertainty": 0.1,
            "estimated_cost": 0.01,
            "failure_probability": 0.1,
            "request_config": {
                "provider": {"order": ["provider"], "only": ["provider"], "allow_fallbacks": False},
                "response_format": {"type": "json_object"},
            },
            "independence_groups": [],
        }
        hardened = resilience._harden_candidates({"candidates": [candidate]})
        row = hardened["candidates"][0]
        self.assertGreater(row["estimated_cost"], 0.01)
        self.assertFalse(row["output_contract"]["machine_readable_required"])
        self.assertNotIn("response_format", row["request_config"])
        self.assertEqual(row["output_contract"]["cost_estimate_policy"], "conservative-p95-reservation")


if __name__ == "__main__":
    unittest.main()
