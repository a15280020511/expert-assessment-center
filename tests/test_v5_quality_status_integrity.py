import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_executor  # noqa: E402
import v5_production_hardening  # noqa: E402
import v5_quality_status_integrity as quality_integrity  # noqa: E402
import v5_token_cost_policy as token_cost  # noqa: E402
import v5_truncation_budget_policy as truncation  # noqa: E402
from execution_graph import ExecutionGraph, GraphLimits, SelectedNode  # noqa: E402
from v5_execution_auditor_integrity import _node_quality  # noqa: E402


CANARY_FIELDS = [
    "conclusions",
    "assumptions",
    "uncertainties",
    "variables",
    "formulas",
    "calculations",
    "sensitivity",
    "scenarios",
    "triggers",
    "failure_modes",
    "options",
    "criteria",
    "tradeoffs",
    "ranking",
    "final_recommendation",
]


def canary_work():
    return {
        "work_id": "wifi-decision",
        "context_requirements": {
            "expected_output_tokens": 2801,
            "expected_reasoning_tokens": 1594,
        },
        "reasoning_requirements": {
            "reasoning_enabled": True,
            "depth": 0.84,
            "verification": 0.90,
        },
        "output_contract": {
            "required_fields": CANARY_FIELDS,
            "machine_readable_required": False,
        },
    }


def single_node_graph():
    node = SelectedNode(
        node_id="node-wifi",
        assigned_work=("wifi-decision",),
        professional_capabilities={"quantitative_reasoning": 0.9},
        functions=("decision_comparison", "quantitative_modeling"),
        prompt_profile={"modules": ["quantitative_rigor", "decision_comparison"]},
        reasoning_profile={"reasoning_enabled": True, "effort": "high", "depth": 0.84},
        parameter_profile={
            "supported_parameters": ["reasoning", "max_tokens"],
            "recommended_output_allowance_tokens": 9508,
        },
        model="openai/test-model",
        provider_endpoint="openai/test-model@openai/flex",
        output_contract={
            "required_fields": CANARY_FIELDS,
            "machine_readable_required": False,
        },
        estimated_quality=0.8,
        quality_uncertainty=0.1,
        estimated_cost=0.01,
        failure_probability=0.01,
        request_config={
            "provider": {
                "order": ["openai/flex"],
                "only": ["openai/flex"],
                "allow_fallbacks": False,
                "require_parameters": True,
            },
            "reasoning": {"effort": "high", "exclude": True},
        },
    )
    return ExecutionGraph(
        nodes=(node,),
        edges=(),
        execution_stages=((node.node_id,),),
        entry_nodes=(node.node_id,),
        final_nodes=(node.node_id,),
        required_work=("wifi-decision",),
        estimated_quality=0.8,
        quality_floor=0.6,
        estimated_total_cost=0.01,
        metadata={"version": 5, "recovery_pool": {node.node_id: []}},
    )


class V5QualityStatusIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        v5_production_hardening.install()

    def test_canary_allowance_exceeds_observed_truncation_point(self):
        allowance = truncation.completion_envelope(canary_work(), 10_000)
        self.assertEqual(allowance, 9508)
        self.assertGreater(allowance, 6675)
        self.assertLessEqual(allowance, 10_000)

    def test_canary_p95_usage_includes_reasoning_pressure(self):
        usage = truncation.estimated_completion_usage(canary_work(), 10_000)
        allowance = truncation.completion_envelope(canary_work(), 10_000)
        self.assertEqual(usage, 8292)
        self.assertGreaterEqual(usage, 7588)
        self.assertLessEqual(usage, allowance)
        self.assertIs(token_cost.estimated_completion_usage, truncation.estimated_completion_usage)

    def test_degraded_node_cannot_be_full_success(self):
        raw = {
            "status": "success",
            "completion_mode": "full",
            "quality_status": "full_success",
            "node_results": [
                {
                    "node_id": "node-wifi",
                    "status": "success_degraded",
                    "quality_score": 0.48,
                    "attempts": [
                        {
                            "attempt_index": 1,
                            "status": "quality_gate_failed",
                            "gate_reasons": ["truncated-output", "quality-score<0.662"],
                            "quality_score": 0.48,
                        }
                    ],
                }
            ],
            "degradation": {"used": False, "extra_model_calls": 0},
        }
        fixed = quality_integrity.enforce_result_integrity(raw)
        self.assertEqual(fixed["completion_mode"], "degraded")
        self.assertEqual(fixed["quality_status"], "degraded_success")
        self.assertEqual(fixed["quality_integrity"]["status"], "DEGRADED")
        self.assertFalse(fixed["quality_integrity"]["full_success_allowed"])
        self.assertTrue(fixed["degradation"]["used"])

    def test_strict_recovered_node_may_remain_full_success(self):
        raw = {
            "status": "success",
            "completion_mode": "full",
            "quality_status": "full_success",
            "node_results": [
                {
                    "node_id": "node-wifi",
                    "status": "success_recovered",
                    "quality_score": 0.82,
                    "attempts": [],
                }
            ],
        }
        fixed = quality_integrity.enforce_result_integrity(raw)
        self.assertEqual(fixed["completion_mode"], "full")
        self.assertEqual(fixed["quality_status"], "full_success")
        self.assertEqual(fixed["quality_integrity"]["status"], "PASS")
        self.assertTrue(fixed["quality_integrity"]["full_success_allowed"])

    def test_real_executor_marks_truncated_usable_answer_degraded(self):
        graph = single_node_graph()
        answer = (
            "conclusions assumptions uncertainties variables formulas calculations "
            "sensitivity scenarios triggers failure_modes options criteria tradeoffs "
            "ranking final_recommendation。"
        ) * 12

        def fake_call(run, payload):
            self.assertEqual(payload.get("max_tokens"), 9508)
            return {
                "id": "response-truncated",
                "model": payload["model"],
                "provider": "openai/flex",
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": answer},
                    }
                ],
                "usage": {
                    "prompt_tokens": 900,
                    "completion_tokens": 6675,
                    "reasoning_tokens": 3624,
                    "cost": 0.01,
                },
            }, 0.01

        run = SimpleNamespace(
            task="wifi task",
            parallel_workers=1,
            api_key=None,
            model_timeout_seconds=30,
            model_max_retries=0,
        )
        with tempfile.TemporaryDirectory() as temp:
            result = v5_executor.execute_v5_graph(
                graph,
                run,
                "wifi task",
                call_fn=fake_call,
                output_dir=temp,
                limits=GraphLimits(
                    max_nodes=4,
                    max_model_calls=4,
                    max_retries=0,
                    max_replacements=0,
                    max_budget_usd=0.20,
                ),
            )
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["completion_mode"], "degraded")
            self.assertEqual(result["quality_status"], "degraded_success")
            self.assertEqual(result["quality_integrity"]["status"], "DEGRADED")
            self.assertEqual(result["node_results"][0]["status"], "success_degraded")

            audit = json.loads(
                (Path(temp) / "v5-request-audit.json").read_text(encoding="utf-8")
            )
            self.assertTrue(audit["dynamic_output_allowance_sent"])
            self.assertTrue(audit["bounded_output_allowance_sent"])
            self.assertFalse(audit["artificial_token_ceiling_sent"])
            self.assertEqual(audit["quality_integrity_status"], "DEGRADED")

            node_quality = _node_quality(Path(temp))
            self.assertEqual(len(node_quality["degraded_nodes"]), 1)
            self.assertFalse(node_quality["all_nodes_strict"])


if __name__ == "__main__":
    unittest.main()
