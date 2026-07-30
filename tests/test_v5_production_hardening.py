import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import ExecutionGraph, GraphLimits, SelectedEdge, SelectedNode  # noqa: E402
import v5_cutover_readiness as cutover  # noqa: E402
import v5_executor as executor  # noqa: E402
import v5_live_benchmark as live_benchmark  # noqa: E402
import v5_live_benchmark_hardened as benchmark_hardened  # noqa: E402
import v5_production_hardening as hardening  # noqa: E402


def _node(node_id, work_id, *, functions=("analysis",), model=None, machine=False, cost=0.001):
    return SelectedNode(
        node_id=node_id,
        assigned_work=(work_id,),
        professional_capabilities={"general_analysis": 0.8},
        functions=functions,
        prompt_profile={"modules": ["structured_delivery"]},
        reasoning_profile={"reasoning_enabled": True, "effort": "medium"},
        parameter_profile={
            "supported_parameters": ["response_format", "structured_outputs", "max_tokens"],
            "recommended_output_allowance_tokens": 2_400 if "synthesis" not in functions else 5_200,
        },
        model=model or f"vendor/{node_id}",
        provider_endpoint=f"vendor/{node_id}@provider-{node_id}",
        output_contract={
            "required_fields": ["conclusions", "assumptions", "uncertainties", "evidence_gaps"],
            "machine_readable_required": machine,
            "must_separate_fact_assumption_inference": True,
        },
        estimated_quality=0.75,
        quality_uncertainty=0.08,
        estimated_cost=cost,
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
    def test_cost_estimate_includes_reasoning_and_uncertainty_reserve(self):
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
            "output_contract": {"machine_readable_required": True},
        }]
        optimistic = hardening._ORIGINAL_ESTIMATED_COST(endpoint, works)
        conservative = hardening.conservative_estimated_cost(endpoint, works)
        self.assertGreater(conservative, optimistic * 2.0)
        self.assertGreater(conservative, 0.05)

    def test_strict_json_schema_is_final_delivery_only(self):
        intermediate = _node("analysis", "work-analysis", machine=True)
        final = _node(
            "schema",
            "work-schema",
            functions=("synthesis", "delivery"),
            machine=True,
        )
        intermediate_payload = hardening.hardened_build_node_payload(intermediate, "任务", [])
        final_payload = hardening.hardened_build_node_payload(final, "任务", [])

        self.assertNotIn("response_format", intermediate_payload)
        response_format = final_payload["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        schema = response_format["json_schema"]["schema"]
        self.assertTrue(response_format["json_schema"]["strict"])
        self.assertEqual(set(schema["required"]), set(final.output_contract["required_fields"]))
        self.assertFalse(schema["additionalProperties"])
        self.assertTrue(final_payload["provider"]["require_parameters"])

    def test_dynamic_output_allowance_is_sent_as_maximum_not_target(self):
        intermediate = _node("analysis", "work-analysis", machine=True)
        final = _node(
            "final",
            "work-final",
            functions=("synthesis", "delivery"),
            machine=True,
        )
        intermediate_payload = hardening.hardened_build_node_payload(intermediate, "任务", [])
        final_payload = hardening.hardened_build_node_payload(final, "任务", [])
        intermediate_limit = intermediate_payload.get("max_tokens") or intermediate_payload.get("max_completion_tokens")
        final_limit = final_payload.get("max_tokens") or final_payload.get("max_completion_tokens")
        self.assertEqual(intermediate_limit, 2_400)
        self.assertEqual(final_limit, 5_200)
        self.assertLess(intermediate_limit, final_limit)
        self.assertLessEqual(final_limit, 10_000)

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
            self.assertFalse(audit["artificial_token_ceiling_sent"])

    def test_final_delivery_escrow_denies_optional_support_before_synthesis(self):
        nodes = (
            _node("a", "work-a", cost=0.03),
            _node("b", "work-b", cost=0.02),
            _node("c", "work-c", cost=0.01),
            _node("final", "work-synthesis", functions=("synthesis",), cost=0.04),
        )
        graph = ExecutionGraph(
            nodes=nodes,
            edges=tuple(
                SelectedEdge(source, "final", "synthesis", "validated-node-output", "declared-upstream-only")
                for source in ("a", "b", "c")
            ),
            execution_stages=(("a", "b", "c"), ("final",)),
            entry_nodes=("a", "b", "c"),
            final_nodes=("final",),
            required_work=("work-a", "work-b", "work-c", "work-synthesis"),
            estimated_quality=0.75,
            quality_floor=0.65,
            estimated_total_cost=0.10,
            metadata={"recovery_pool": {}},
        )
        calls = []
        costs = {"vendor/a": 0.04, "vendor/b": 0.02, "vendor/final": 0.03}

        def fake_call(run, payload):
            model = payload["model"]
            calls.append(model)
            return {
                "id": f"response-{model}",
                "model": model,
                "provider": payload["provider"]["order"][0],
                "choices": [{"finish_reason": "stop", "message": {"content": _answer(model)}}],
                "usage": {"cost": costs[model]},
            }, 0.01

        result = hardening.resilient_execute_v5_graph(
            graph,
            SimpleNamespace(parallel_workers=4, model_max_retries=0),
            "任务",
            call_fn=fake_call,
            limits=GraphLimits(max_model_calls=4, max_retries=0, max_replacements=0, max_budget_usd=0.10),
        )
        self.assertIn("vendor/final", calls)
        self.assertNotIn("vendor/c", calls)
        self.assertEqual(result["quality_status"], "degraded_success")
        denials = result["execution_budget"]["denials"]
        self.assertTrue(any(row.get("reason") == "final-delivery-escrow-protected" for row in denials))
        self.assertLessEqual(result["actual_cost_usd"], 0.10)

    def test_cost_preflight_rejects_before_any_paid_call(self):
        node = _node("expensive", "work-a", cost=0.30)
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

    def test_degraded_delivery_cannot_authorize_cutover(self):
        degraded = {
            "status": "success",
            "completion_mode": "degraded",
            "quality_status": "degraded_success",
            "degradation": {"used": True},
        }
        complete = {
            "status": "success",
            "completion_mode": "full",
            "quality_status": "full_success",
            "degradation": {"used": False},
        }
        answer = "可交付结果" * 100
        self.assertFalse(cutover.full_success_for_cutover(degraded, answer))
        self.assertTrue(cutover.full_success_for_cutover(complete, answer))

    def test_benchmark_allowance_preserves_lower_dynamic_node_limit(self):
        original_node = executor.build_node_payload
        original_safe = live_benchmark._safe_payload
        original_execute = live_benchmark.execute_v5_graph
        original_annotate = benchmark_hardened._annotate_v5_audit
        try:
            executor.build_node_payload = lambda node, original_task, upstream: {
                "model": "vendor/model",
                "messages": [],
                "max_tokens": 2_400,
            }
            benchmark_hardened.ALLOWANCE = 10_000
            cutover.install_benchmark_output_allowance()
            node = SimpleNamespace(parameter_profile={"supported_parameters": ["max_tokens"]})
            payload = executor.build_node_payload(node, "任务", [])
            self.assertEqual(payload["max_tokens"], 2_400)
            self.assertNotIn("max_completion_tokens", payload)
        finally:
            executor.build_node_payload = original_node
            live_benchmark._safe_payload = original_safe
            live_benchmark.execute_v5_graph = original_execute
            benchmark_hardened._annotate_v5_audit = original_annotate

    def test_robust_answer_extraction_supports_output_text(self):
        self.assertEqual(
            hardening.robust_extract_answer({"output_text": " usable answer "}),
            "usable answer",
        )


if __name__ == "__main__":
    unittest.main()
