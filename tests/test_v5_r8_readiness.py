import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import ExecutionGraph, GraphLimits, SelectedNode  # noqa: E402
import task_semantic_compiler as compiler  # noqa: E402
import v5_cutover_gate  # noqa: E402
import v5_production_hardening as hardening  # noqa: E402
import v5_r8_executor  # noqa: E402
import v5_r8_policy  # noqa: E402


def _node(
    node_id,
    *,
    model=None,
    provider=None,
    functions=("analysis",),
    machine=False,
    failure_probability=0.05,
    estimated_cost=0.01,
):
    fields = ["conclusions", "assumptions", "uncertainties", "evidence_gaps"]
    return SelectedNode(
        node_id=node_id,
        assigned_work=(f"work-{node_id}",),
        professional_capabilities={"general_analysis": 0.8},
        functions=functions,
        prompt_profile={"modules": ["structured_delivery"]},
        reasoning_profile={"reasoning_enabled": True, "effort": "high"},
        parameter_profile={
            "supported_parameters": ["max_tokens", "response_format", "structured_outputs"],
            "recommended_output_allowance_tokens": 2400,
        },
        model=model or f"vendor/{node_id}",
        provider_endpoint=f"{model or f'vendor/{node_id}'}@{provider or f'provider-{node_id}'}",
        output_contract={
            "required_fields": fields,
            "machine_readable_required": machine,
            "must_separate_fact_assumption_inference": True,
        },
        estimated_quality=0.78,
        quality_uncertainty=0.08,
        estimated_cost=estimated_cost,
        failure_probability=failure_probability,
        request_config={
            "provider": {
                "order": [provider or f"provider-{node_id}"],
                "only": [provider or f"provider-{node_id}"],
                "allow_fallbacks": False,
                "require_parameters": True,
            },
            "reasoning": {"effort": "high", "exclude": True},
        },
    )


def _good_answer(label="ok"):
    return (
        f"conclusions {label}; assumptions; uncertainties; evidence_gaps. "
        "这是完整、可交付、明确区分事实、假设、推断与不确定性的分析结果。"
    ) * 5


class TestV5R8Readiness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        hardening.install()

    def test_reasoning_is_not_uniform_high_and_json_is_delivery_boundary_only(self):
        analysis = v5_r8_policy.production_reasoning_vector(
            {"analysis": 0.58}, True, "complex"
        )
        red_team = v5_r8_policy.production_reasoning_vector(
            {"adversarial_reasoning": 0.78}, True, "complex"
        )
        self.assertLess(analysis["depth"], 0.78)
        self.assertGreaterEqual(red_team["depth"], 0.82)

        def work(work_id, operation):
            return compiler.AtomicWork(
                work_id=work_id,
                objective=work_id,
                importance=0.8,
                error_cost=0.7,
                verifiability=0.5,
                domain_requirements={"security": 0.8},
                operation_requirements={operation: 0.8},
                prompt_requirements={"structured_delivery": 0.8},
                reasoning_requirements={"reasoning_enabled": True, "depth": 0.7},
                context_requirements={
                    "system_prompt_tokens": 500,
                    "original_task_tokens": 300,
                    "visible_upstream_tokens": 0,
                    "expected_reasoning_tokens": 800,
                    "expected_output_tokens": 1200,
                    "safety_margin_tokens": 700,
                    "required_context_tokens": 3500,
                },
                output_contract={
                    "required_fields": ["conclusions"],
                    "machine_readable_required": True,
                },
                independence_requirements={},
            )

        rows = v5_r8_policy.boundary_structured_finish(
            [work("a", "analysis"), work("b", "evidence_validation")],
            "严格JSON安全分析",
            SimpleNamespace(complexity="complex", high_stakes=True),
            True,
            ["security"],
        )
        machine = [
            row for row in rows
            if row.output_contract.get("machine_readable_required")
        ]
        self.assertEqual(len(machine), 1)
        self.assertIn("synthesis", machine[0].operation_requirements)

    def test_payload_uses_dynamic_allowance_and_caps_ordinary_high_reasoning(self):
        payload = hardening.hardened_build_node_payload(
            _node("analysis"), "任务", []
        )
        self.assertEqual(payload["reasoning"]["effort"], "medium")
        self.assertEqual(payload["max_tokens"], 2400)
        self.assertLessEqual(payload["max_tokens"], 10000)

    def test_429_switches_provider_without_same_endpoint_retry(self):
        selected = _node("a", model="vendor/a", provider="p1")
        replacement = {
            **selected.to_dict(),
            "candidate_id": "candidate-b",
            "model": "vendor/b",
            "provider_endpoint": "vendor/b@p2",
            "request_config": {
                "provider": {
                    "order": ["p2"],
                    "only": ["p2"],
                    "allow_fallbacks": False,
                    "require_parameters": True,
                }
            },
            "failure_probability": 0.03,
        }
        graph = ExecutionGraph(
            nodes=(selected,),
            edges=(),
            execution_stages=(("a",),),
            entry_nodes=("a",),
            final_nodes=("a",),
            required_work=("work-a",),
            estimated_quality=0.78,
            quality_floor=0.65,
            estimated_total_cost=0.01,
            metadata={"recovery_pool": {"a": [replacement]}},
        )
        calls = []

        def fake_call(run, payload):
            calls.append(payload["model"])
            if payload["model"] == "vendor/a":
                raise RuntimeError("HTTP 429 upstream rate limit")
            return {
                "id": "response-b",
                "model": "vendor/b",
                "provider": "p2",
                "choices": [{"finish_reason": "stop", "message": {"content": _good_answer("b")}}],
                "usage": {"cost": 0.01},
            }, 0.01

        result = v5_r8_executor.resilient_execute_v5_graph(
            graph,
            SimpleNamespace(parallel_workers=1, api_key=None),
            "测试",
            call_fn=fake_call,
            limits=GraphLimits(
                max_model_calls=1,
                max_retries=1,
                max_replacements=1,
                max_provider_share=1.0,
            ),
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(calls, ["vendor/a", "vendor/b"])
        self.assertTrue(result["recovery_used"])

    def test_invalid_strict_json_is_never_accepted_as_degraded_text(self):
        node = _node("json", machine=True)
        graph = ExecutionGraph(
            nodes=(node,),
            edges=(),
            execution_stages=(("json",),),
            entry_nodes=("json",),
            final_nodes=("json",),
            required_work=("work-json",),
            estimated_quality=0.78,
            quality_floor=0.65,
            estimated_total_cost=0.01,
            metadata={},
        )

        def fake_call(run, payload):
            return {
                "id": "bad-json",
                "model": node.model,
                "provider": "provider-json",
                "choices": [{
                    "finish_reason": "length",
                    "message": {"content": "{\"conclusions\":[\"" + ("x" * 800)},
                }],
                "usage": {"cost": 0.01},
            }, 0.01

        with self.assertRaisesRegex(Exception, "coverage"):
            v5_r8_executor.resilient_execute_v5_graph(
                graph,
                SimpleNamespace(parallel_workers=1, api_key=None),
                "JSON任务",
                call_fn=fake_call,
                limits=GraphLimits(
                    max_model_calls=1,
                    max_retries=0,
                    max_replacements=0,
                    max_provider_share=1.0,
                ),
            )

    def test_risk_preflight_rejects_before_calls_and_writes_evidence(self):
        node = _node("expensive", estimated_cost=0.20)
        graph = ExecutionGraph(
            nodes=(node,),
            edges=(),
            execution_stages=(("expensive",),),
            entry_nodes=("expensive",),
            final_nodes=("expensive",),
            required_work=("work-expensive",),
            estimated_quality=0.78,
            quality_floor=0.65,
            estimated_total_cost=0.20,
            metadata={},
        )
        calls = []
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(Exception, "rejected before model calls"):
                v5_r8_executor.resilient_execute_v5_graph(
                    graph,
                    SimpleNamespace(parallel_workers=1),
                    "预算任务",
                    call_fn=lambda *_: calls.append(1),
                    output_dir=temp,
                    limits=GraphLimits(
                        max_model_calls=1,
                        max_budget_usd=0.25,
                        cost_risk_multiplier=1.35,
                        max_provider_share=1.0,
                    ),
                )
            self.assertFalse(calls)
            summary = json.loads(
                (Path(temp) / "v5-execution-summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(summary["cost_preflight"]["status"], "rejected")
            self.assertEqual(summary["actual_cost_usd"], 0.0)

    def test_cutover_and_v3_deletion_are_separate_evidence_gates(self):
        records = []
        for index in range(30):
            records.append({
                "version": "v5",
                "status": "success",
                "completion_mode": "full",
                "blind_quality_score": 0.90,
                "actual_cost_usd": 0.08,
            })
            records.append({
                "version": "v3",
                "status": "success",
                "completion_mode": "full",
                "blind_quality_score": 0.86,
                "actual_cost_usd": 0.10,
            })
        canary = v5_cutover_gate.evaluate_cutover(records, phase="canary")
        post = v5_cutover_gate.evaluate_cutover(records, phase="post_default")
        self.assertTrue(canary["production_cutover_allowed"])
        self.assertFalse(canary["v3_deletion_allowed"])
        self.assertTrue(post["v3_deletion_allowed"])


if __name__ == "__main__":
    unittest.main()
