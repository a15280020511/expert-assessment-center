import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import ExecutionGraph, GraphLimits, SelectedNode  # noqa: E402
from openrouter_api import OpenRouterRequestError  # noqa: E402
import v5_production_hardening as hardening  # noqa: E402


def node(name):
    return SelectedNode(
        node_id=name,
        assigned_work=(f"work-{name}",),
        professional_capabilities={"general_analysis": 0.8},
        functions=("analysis",),
        prompt_profile={"modules": ["structured_delivery"]},
        reasoning_profile={"reasoning_enabled": True, "effort": "medium"},
        parameter_profile={"supported_parameters": ["max_tokens"]},
        model=f"vendor/{name}",
        provider_endpoint=f"vendor/{name}@provider-{name}",
        output_contract={
            "required_fields": ["conclusions", "assumptions", "uncertainties", "evidence_gaps"],
            "machine_readable_required": False,
            "must_separate_fact_assumption_inference": True,
        },
        estimated_quality=0.78,
        quality_uncertainty=0.08,
        estimated_cost=0.001,
        failure_probability=0.05,
        request_config={
            "provider": {
                "order": [f"provider-{name}"],
                "only": [f"provider-{name}"],
                "allow_fallbacks": False,
                "require_parameters": True,
            }
        },
    )


def answer(label):
    return (
        f"conclusions {label}; assumptions; uncertainties; evidence_gaps. "
        "这是完整可交付并明确区分事实、假设、推断和不确定性的节点结果。"
    ) * 6


def graph(nodes, metadata=None):
    return ExecutionGraph(
        nodes=tuple(nodes),
        edges=(),
        execution_stages=(tuple(item.node_id for item in nodes),),
        entry_nodes=tuple(item.node_id for item in nodes),
        final_nodes=tuple(item.node_id for item in nodes),
        required_work=tuple(item.assigned_work[0] for item in nodes),
        estimated_quality=0.78,
        quality_floor=0.65,
        estimated_total_cost=sum(item.estimated_cost for item in nodes),
        metadata=metadata or {},
    )


class TestV5R8GateWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        hardening.install()

    def test_transient_503_gets_one_same_endpoint_retry_before_circuit(self):
        selected = node("a")
        calls = []

        def fake_call(run, payload):
            calls.append(payload["model"])
            if len(calls) == 1:
                raise OpenRouterRequestError(
                    "opaque temporary provider failure",
                    category="timeout",
                    retryable=True,
                    http_status=503,
                    request_sent=True,
                    response_received=True,
                )
            return {
                "id": "response-a",
                "model": selected.model,
                "provider": "provider-a",
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": answer("retry")},
                }],
                "usage": {"cost": 0.001},
            }, 0.01

        result = hardening.resilient_execute_v5_graph(
            graph([selected]),
            SimpleNamespace(parallel_workers=1, api_key=None),
            "测试503有限重试",
            call_fn=fake_call,
            limits=GraphLimits(
                max_model_calls=2,
                max_retries=1,
                max_replacements=0,
                max_provider_share=1.0,
            ),
        )
        self.assertEqual(calls, [selected.model, selected.model])
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["recovery_used"])
        self.assertEqual(result["node_results"][0]["status"], "success_retried")

    def test_optional_work_is_excluded_from_configurable_coverage(self):
        a, b = node("a"), node("b")

        def fake_call(run, payload):
            if payload["model"] == a.model:
                return {
                    "id": "response-a",
                    "model": a.model,
                    "provider": "provider-a",
                    "choices": [{"finish_reason": "stop", "message": {"content": answer("a")}}],
                    "usage": {"cost": 0.001},
                }, 0.01
            raise RuntimeError("empty optional node")

        result = hardening.resilient_execute_v5_graph(
            graph([a, b], {"optional_work_ids": ["work-b"]}),
            SimpleNamespace(parallel_workers=2, api_key=None),
            "测试可选工作",
            call_fn=fake_call,
            limits=GraphLimits(
                max_model_calls=2,
                max_retries=0,
                max_replacements=0,
                min_required_work_coverage=1.0,
                max_provider_share=1.0,
            ),
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["work_coverage"]["coverage_ratio"], 1.0)
        self.assertEqual(result["delivery_policy"]["optional_work_ids"], ["work-b"])

    def test_non_degradable_work_and_degraded_disable_are_hard_gates(self):
        a, b = node("a"), node("b")

        def fake_call(run, payload):
            if payload["model"] == a.model:
                return {
                    "id": "response-a",
                    "model": a.model,
                    "provider": "provider-a",
                    "choices": [{"finish_reason": "stop", "message": {"content": answer("a")}}],
                    "usage": {"cost": 0.001},
                }, 0.01
            raise RuntimeError("required node failed")

        with self.assertRaisesRegex(Exception, "production delivery policy"):
            hardening.resilient_execute_v5_graph(
                graph([a, b], {"non_degradable_work_ids": ["work-b"]}),
                SimpleNamespace(parallel_workers=2, api_key=None),
                "测试不可降级工作",
                call_fn=fake_call,
                limits=GraphLimits(
                    max_model_calls=2,
                    max_retries=0,
                    max_replacements=0,
                    min_required_work_coverage=0.5,
                    allow_degraded_success=True,
                    max_provider_share=1.0,
                ),
            )

        with self.assertRaisesRegex(Exception, "production delivery policy"):
            hardening.resilient_execute_v5_graph(
                graph([a, b]),
                SimpleNamespace(parallel_workers=2, api_key=None),
                "测试禁用降级",
                call_fn=fake_call,
                limits=GraphLimits(
                    max_model_calls=2,
                    max_retries=0,
                    max_replacements=0,
                    min_required_work_coverage=0.5,
                    allow_degraded_success=False,
                    max_provider_share=1.0,
                ),
            )

    def test_minimum_successful_content_nodes_is_enforced(self):
        a, b, c = node("a"), node("b"), node("c")

        def fake_call(run, payload):
            if payload["model"] == a.model:
                return {
                    "id": "response-a",
                    "model": a.model,
                    "provider": "provider-a",
                    "choices": [{"finish_reason": "stop", "message": {"content": answer("a")}}],
                    "usage": {"cost": 0.001},
                }, 0.01
            raise RuntimeError("node failed")

        with self.assertRaisesRegex(Exception, "insufficient-successful-content-nodes"):
            hardening.resilient_execute_v5_graph(
                graph([a, b, c]),
                SimpleNamespace(parallel_workers=3, api_key=None),
                "测试最少成功节点",
                call_fn=fake_call,
                limits=GraphLimits(
                    max_model_calls=3,
                    max_retries=0,
                    max_replacements=0,
                    min_required_work_coverage=1.0 / 3.0,
                    min_successful_content_nodes=2,
                    max_provider_share=1.0,
                ),
            )


if __name__ == "__main__":
    unittest.main()
