import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import (  # noqa: E402
    ExecutionGraph,
    GraphLimits,
    SelectedEdge,
    SelectedNode,
)
from v5_executor import V5ExecutionError  # noqa: E402
from v5_production_hardening import (  # noqa: E402
    OUTPUT_ALLOWANCE_TOKENS,
    _candidate_cost_envelope,
    _sanitize_endpoint_payloads,
    build_node_payload,
    execute_v5_graph,
)


class TestV5ProductionHardening(unittest.TestCase):
    @staticmethod
    def node(
        node_id,
        work_id,
        model,
        functions=("analysis",),
        machine=False,
    ):
        required = ["conclusions", "assumptions"]
        request_config = {
            "provider": {
                "order": [f"provider-{model}"],
                "only": [f"provider-{model}"],
                "allow_fallbacks": False,
                "require_parameters": True,
            },
            "max_tokens": OUTPUT_ALLOWANCE_TOKENS,
        }
        if machine:
            request_config["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "v5_node_delivery",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            field: {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 1,
                            }
                            for field in required
                        },
                        "required": required,
                        "additionalProperties": False,
                    },
                },
            }
        return SelectedNode(
            node_id=node_id,
            assigned_work=(work_id,),
            professional_capabilities={"general_analysis": 0.9},
            functions=tuple(functions),
            prompt_profile={"modules": ["structured_delivery"]},
            reasoning_profile={"reasoning_enabled": True, "effort": "low"},
            parameter_profile={"supported_parameters": ["structured_outputs"]},
            model=model,
            provider_endpoint=f"{model}@provider-{model}",
            output_contract={
                "required_fields": required,
                "machine_readable_required": machine,
                "must_separate_fact_assumption_inference": True,
            },
            estimated_quality=0.9,
            quality_uncertainty=0.05,
            estimated_cost=0.001,
            failure_probability=0.02,
            request_config=request_config,
        )

    @classmethod
    def graph(cls, estimated_total_cost=0.003):
        a = cls.node("a", "work-a", "model-a")
        b = cls.node("b", "work-b", "model-b")
        c = cls.node("c", "work-c", "model-c", ("synthesis",))
        return ExecutionGraph(
            nodes=(a, b, c),
            edges=(
                SelectedEdge(
                    source="a",
                    target="c",
                    relation_type="dependency",
                    payload_type="validated-node-output",
                    visibility_policy="declared-upstream-only",
                ),
                SelectedEdge(
                    source="b",
                    target="c",
                    relation_type="dependency",
                    payload_type="validated-node-output",
                    visibility_policy="declared-upstream-only",
                ),
            ),
            execution_stages=(("a", "b"), ("c",)),
            entry_nodes=("a", "b"),
            final_nodes=("c",),
            required_work=("work-a", "work-b", "work-c"),
            estimated_quality=0.9,
            quality_floor=0.8,
            estimated_total_cost=estimated_total_cost,
            metadata={
                "node_coverage_keys": {
                    "a": ["work-a#0"],
                    "b": ["work-b#0"],
                    "c": ["work-c#0"],
                },
                "work_policy": {
                    "work-a": {
                        "critical": True,
                        "synthesis": False,
                    },
                    "work-b": {
                        "critical": False,
                        "synthesis": False,
                    },
                    "work-c": {
                        "critical": False,
                        "synthesis": True,
                    },
                },
                "minimum_usable_work_coverage": 0.5,
                "recovery_pool": {},
            },
        )

    @staticmethod
    def run():
        return SimpleNamespace(
            api_key=None,
            parallel_workers=3,
            model_timeout_seconds=30,
            model_max_retries=0,
        )

    @staticmethod
    def answer_for(model):
        if model == "model-c":
            return (
                "conclusions assumptions。"
                "综合现有上游结果形成可执行结论，明确关键约束、主要风险、验证条件和回滚条件。"
                "对缺失输入单独标记，不将未验证事项伪装为事实。"
            ) * 8
        return (
            "conclusions assumptions。"
            "这是一个完整节点结果，包含结论、假设、边界、不确定性、风险与行动建议。"
        ) * 6

    @classmethod
    def fake_call(cls, run, payload):
        model = payload["model"]
        if model == "model-b":
            raise RuntimeError("simulated optional endpoint failure")
        return {
            "id": f"resp-{model}",
            "model": model,
            "provider": payload["provider"]["order"][0],
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": cls.answer_for(model)},
                }
            ],
            "usage": {"cost": 0.001},
        }, 0.01

    def test_optional_node_failure_degrades_instead_of_killing_graph(self):
        with tempfile.TemporaryDirectory() as temp:
            result = execute_v5_graph(
                self.graph(),
                self.run(),
                "测试任务",
                call_fn=self.fake_call,
                output_dir=temp,
                limits=GraphLimits(
                    max_model_calls=3,
                    max_retries=0,
                    max_replacements=0,
                    max_budget_usd=0.01,
                ),
            )
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["completion_mode"], "degraded")
            self.assertTrue(result["degraded"])
            self.assertFalse(result["deterministic_fallback_used"])
            self.assertEqual(
                result["degradation_reasons"]["failed_node_ids"],
                ["b"],
            )
            self.assertGreater(len(result["final_answer"]), 320)

    def test_failed_synthesis_uses_deterministic_successful_node_fallback(self):
        def fail_b_and_c(run, payload):
            if payload["model"] in {"model-b", "model-c"}:
                raise RuntimeError("simulated failure")
            return self.fake_call(run, payload)

        with tempfile.TemporaryDirectory() as temp:
            result = execute_v5_graph(
                self.graph(),
                self.run(),
                "测试任务",
                call_fn=fail_b_and_c,
                output_dir=temp,
                limits=GraphLimits(
                    max_model_calls=3,
                    max_retries=0,
                    max_replacements=0,
                    max_budget_usd=0.01,
                ),
            )
            self.assertEqual(result["status"], "success")
            self.assertTrue(result["deterministic_fallback_used"])
            self.assertIn("V5降级合成结果", result["final_answer"])
            self.assertIn("work-b", result["final_answer"])
            self.assertIn("work-c", result["final_answer"])

    def test_worst_case_budget_rejects_before_any_call(self):
        calls = {"count": 0}

        def should_not_call(run, payload):
            calls["count"] += 1
            raise AssertionError("must not call")

        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(V5ExecutionError):
                execute_v5_graph(
                    self.graph(estimated_total_cost=0.50),
                    self.run(),
                    "测试任务",
                    call_fn=should_not_call,
                    output_dir=temp,
                    limits=GraphLimits(
                        max_model_calls=3,
                        max_retries=0,
                        max_replacements=0,
                        max_budget_usd=0.25,
                    ),
                )
        self.assertEqual(calls["count"], 0)

    def test_payload_has_real_10k_allowance_and_strict_schema(self):
        node = self.node(
            "json-node",
            "json-work",
            "json-model",
            machine=True,
        )
        payload = build_node_payload(node, "测试任务", [])
        self.assertEqual(payload["max_tokens"], 10_000)
        self.assertEqual(
            payload["response_format"]["type"],
            "json_schema",
        )
        schema = payload["response_format"]["json_schema"]
        self.assertTrue(schema["strict"])
        self.assertFalse(
            schema["schema"]["additionalProperties"]
        )

    def test_cost_envelope_counts_reasoning_and_worst_case_allowance(self):
        candidate = {
            "assigned_work": ["work-a"],
        }
        works = {
            "work-a": {
                "context_requirements": {
                    "system_prompt_tokens": 500,
                    "original_task_tokens": 500,
                    "visible_upstream_tokens": 500,
                    "expected_output_tokens": 1_000,
                    "expected_reasoning_tokens": 2_000,
                }
            }
        }
        endpoint = {
            "prompt_price_per_million": 2.0,
            "completion_price_per_million": 10.0,
        }
        envelope = _candidate_cost_envelope(
            candidate,
            endpoint,
            works,
        )
        self.assertEqual(
            envelope["expected_completion_and_reasoning_tokens"],
            3_000,
        )
        self.assertEqual(
            envelope["max_completion_tokens_sent"],
            10_000,
        )
        self.assertGreater(
            envelope["worst_case_cost_usd"],
            envelope["p95_cost_usd"],
        )
        self.assertGreater(
            envelope["p95_cost_usd"],
            envelope["expected_cost_usd"],
        )

    def test_missing_reliability_is_not_promoted_to_097(self):
        payloads = {
            "model/a": {
                "data": {
                    "endpoints": [
                        {
                            "tag": "provider-a",
                            "pricing": {
                                "prompt": 0.000001,
                                "completion": 0.000002,
                            },
                        }
                    ]
                }
            }
        }
        sanitized = _sanitize_endpoint_payloads(payloads)
        endpoint = sanitized["model/a"]["data"]["endpoints"][0]
        self.assertEqual(endpoint["uptime"], 0.0)
        self.assertEqual(
            endpoint["v5_reliability_evidence"],
            "missing-rejected",
        )


if __name__ == "__main__":
    unittest.main()
