import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import ExecutionGraph, GraphLimits, SelectedEdge, SelectedNode  # noqa: E402
import v5_production_hardening as hardening  # noqa: E402
import v5_truncation_budget_policy as truncation  # noqa: E402
from v5_planning_runtime import PlannerPolicy  # noqa: E402


def _node(node_id, work_id, *, functions=("analysis",), model=None):
    resolved_model = model or f"company-{node_id}/{node_id}"
    return SelectedNode(
        node_id=node_id,
        assigned_work=(work_id,),
        professional_capabilities={"general_analysis": 0.8},
        functions=functions,
        prompt_profile={"modules": ["structured_delivery"]},
        reasoning_profile={"reasoning_enabled": True, "effort": "medium"},
        parameter_profile={"supported_parameters": ["response_format", "structured_outputs"]},
        model=resolved_model,
        provider_endpoint=f"{resolved_model}@provider-{node_id}",
        output_contract={
            "required_fields": ["conclusions", "assumptions", "uncertainties", "evidence_gaps"],
            "machine_readable_required": False,
            "must_separate_fact_assumption_inference": True,
        },
        estimated_quality=0.75,
        quality_uncertainty=0.08,
        estimated_cost=0.001,
        failure_probability=0.05,
        request_config={
            "provider": {
                "order": [f"provider-{node_id}"],
                "only": [f"provider-{node_id}"],
                "allow_fallbacks": False,
                "require_parameters": True,
            }
        },
    )


def _answer(label):
    text = (
        f"conclusions {label} assumptions uncertainties evidence_gaps。"
        "这是一个完整、可交付并明确区分事实、假设、推断和不确定性的节点结果。"
    )
    return text * 5


class TestV5ProductionHardening(unittest.TestCase):
    def test_cost_estimate_includes_reasoning_and_p95_reserves_without_pricing_full_allowance(self):
        endpoint = {
            "prompt_price_per_million": 2.0,
            "completion_price_per_million": 10.0,
            "max_completion_tokens": 10000,
            "reliability": 0.96,
        }
        works = [{
            "context_requirements": {
                "system_prompt_tokens": 500,
                "original_task_tokens": 500,
                "visible_upstream_tokens": 0,
                "expected_output_tokens": 2500,
                "expected_reasoning_tokens": 1600,
            },
            "reasoning_requirements": {"reasoning_enabled": True},
            "output_contract": {
                "required_fields": [],
                "machine_readable_required": True,
            },
        }]
        optimistic = hardening._ORIGINAL_ESTIMATED_COST(endpoint, works)
        conservative = PlannerPolicy._p95_cost(endpoint, works, 1.0)
        usage = truncation.estimated_completion_usage(works[0], 10000)
        allowance = truncation.completion_envelope(works[0], 10000)
        reliability_reserve = 1.0 / 0.96
        expected = round(
            ((1000 * 2.0) + (usage * 10.0))
            / 1_000_000
            * reliability_reserve,
            8,
        )
        full_allowance_cost = (
            ((1000 * 2.0) + (allowance * 10.0))
            / 1_000_000
            * reliability_reserve
        )

        self.assertEqual(usage, math.ceil((2500 + 1600) * 1.22))
        self.assertEqual(allowance, 6250)
        self.assertLess(usage, allowance)
        self.assertAlmostEqual(conservative, expected, places=8)
        self.assertGreater(conservative, optimistic)
        self.assertLess(conservative, full_allowance_cost)
        self.assertGreater(conservative, 0.05)

    def test_dynamic_output_allowance_may_exceed_ten_thousand(self):
        node = _node("long", "work-long", functions=("synthesis",))
        node = SelectedNode(**{
            **node.to_dict(),
            "assigned_work": tuple(node.assigned_work),
            "functions": tuple(node.functions),
            "parameter_profile": {
                "supported_parameters": ["max_tokens"],
                "recommended_output_allowance_tokens": 15992,
            },
        })
        payload = hardening.hardened_build_node_payload(node, "复杂任务", [])
        self.assertEqual(payload["max_tokens"], 15992)
        self.assertGreater(payload["max_tokens"], 10000)
        self.assertLessEqual(payload["max_tokens"], 32768)

    def test_strict_json_schema_requires_every_declared_field(self):
        node = _node("schema", "work-schema")
        node = SelectedNode(**{
            **node.to_dict(),
            "assigned_work": tuple(node.assigned_work),
            "functions": tuple(node.functions),
            "output_contract": {
                **dict(node.output_contract),
                "machine_readable_required": True,
            },
        })
        payload = hardening.hardened_build_node_payload(node, "任务", [])
        response_format = payload["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        schema = response_format["json_schema"]["schema"]
        self.assertTrue(response_format["json_schema"]["strict"])
        self.assertEqual(set(schema["required"]), set(node.output_contract["required_fields"]))
        self.assertFalse(schema["additionalProperties"])
        self.assertTrue(payload["provider"]["require_parameters"])

    def test_partial_failure_continues_and_produces_deterministic_degraded_answer(self):
        nodes = (
            _node("a", "work-a"),
            _node("b", "work-b"),
            _node("c", "work-c"),
            _node("final", "work-synthesis", functions=("synthesis",)),
        )
        graph = ExecutionGraph(
            nodes=nodes,
            edges=(
                SelectedEdge("a", "c", "dependency", "validated-node-output", "declared-upstream-only"),
                SelectedEdge("b", "c", "dependency", "validated-node-output", "declared-upstream-only"),
                SelectedEdge("c", "final", "synthesis", "validated-node-output", "declared-upstream-only"),
            ),
            execution_stages=(("a", "b"), ("c",), ("final",)),
            entry_nodes=("a", "b"),
            final_nodes=("final",),
            required_work=("work-a", "work-b", "work-c", "work-synthesis"),
            estimated_quality=0.75,
            quality_floor=0.65,
            estimated_total_cost=0.004,
            metadata={"recovery_pool": {}},
        )
        calls = []

        def fake_call(run, payload):
            model = payload["model"]
            calls.append(model)
            if model.endswith("/b") or model.endswith("/final"):
                raise RuntimeError("simulated endpoint failure")
            return {
                "id": f"response-{model}",
                "model": model,
                "provider": payload["provider"]["order"][0],
                "choices": [{"finish_reason": "stop", "message": {"content": _answer(model)}}],
                "usage": {"cost": 0.001},
            }, 0.01

        run = SimpleNamespace(parallel_workers=4, api_key=None, model_timeout_seconds=30, model_max_retries=0)
        with tempfile.TemporaryDirectory() as temp:
            result = hardening.resilient_execute_v5_graph(
                graph,
                run,
                "测试任务",
                call_fn=fake_call,
                output_dir=temp,
                limits=GraphLimits(max_model_calls=4, max_retries=0, max_replacements=0),
            )
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["completion_mode"], "degraded")
            self.assertEqual(result["quality_status"], "degraded_success")
            self.assertTrue(result["degradation"]["used"])
            self.assertEqual(result["degradation"]["extra_model_calls"], 0)
            self.assertAlmostEqual(result["work_coverage"]["coverage_ratio"], 2 / 3, places=5)
            self.assertIn("work-b", result["work_coverage"]["missing_work_ids"])
            self.assertIn("V5降级合成结果", result["final_answer"])
            self.assertEqual(len(calls), 4)
            self.assertTrue(all(stage["continued_after_failure"] for stage in result["execution_stages"] if stage["failed_node_ids"]))
            summary = json.loads((Path(temp) / "v5-execution-summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["completion_mode"], "degraded")
            audit = json.loads((Path(temp) / "v5-request-audit.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["status"], "PASS")
            self.assertTrue(audit["degraded_synthesis_is_deterministic"])

    def test_cost_preflight_rejects_before_any_paid_call(self):
        node = _node("expensive", "work-a")
        node = SelectedNode(**{
            **node.to_dict(),
            "assigned_work": tuple(node.assigned_work),
            "functions": tuple(node.functions),
            "estimated_cost": 0.30,
        })
        graph = ExecutionGraph(
            nodes=(node,),
            edges=(),
            execution_stages=(("expensive",),),
            entry_nodes=("expensive",),
            final_nodes=("expensive",),
            required_work=("work-a",),
            estimated_quality=0.7,
            quality_floor=0.6,
            estimated_total_cost=0.30,
            metadata={},
        )
        calls = []

        def should_not_call(run, payload):
            calls.append(payload)
            raise AssertionError("paid call must not happen")

        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(Exception, "rejected before model calls"):
                hardening.resilient_execute_v5_graph(
                    graph,
                    SimpleNamespace(parallel_workers=1),
                    "任务",
                    call_fn=should_not_call,
                    output_dir=temp,
                    limits=GraphLimits(max_model_calls=1, max_retries=0, max_replacements=0, max_budget_usd=0.25),
                )
            self.assertFalse(calls)
            summary = json.loads((Path(temp) / "v5-execution-summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["cost_preflight"]["status"], "rejected")
            self.assertEqual(summary["actual_cost_usd"], 0.0)

    def test_robust_answer_extraction_supports_output_text(self):
        self.assertEqual(
            hardening.robust_extract_answer({"output_text": " usable answer "}),
            "usable answer",
        )


if __name__ == "__main__":
    unittest.main()
