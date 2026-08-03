from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import SelectedNode  # noqa: E402
from v5_runtime import (  # noqa: E402
    BudgetController,
    ExecutionEngine,
    FailureCategory,
    OutputPolicy,
    PromptPolicy,
    RecoveryPolicy,
    RetryPolicy,
    RuntimeConfig,
)


class _Quality:
    def evaluate(self, _node, _response, answer):
        return (bool(answer.strip()), 0.95, [])


def selected_node() -> SelectedNode:
    return SelectedNode(
        node_id="node-visible-recovery",
        assigned_work=("work-a",),
        professional_capabilities={"analysis": 0.8},
        functions=("quantitative_modeling",),
        prompt_profile={"modules": ["structured_delivery"]},
        reasoning_profile={"reasoning_enabled": True, "effort": "high"},
        parameter_profile={
            "supported_parameters": ["reasoning", "max_tokens"],
            "recommended_output_allowance_tokens": 4096,
            "model_company": "qwen",
            "dynamic_parameter_decisions": {"reasoning_effort": "high"},
        },
        model="qwen/test",
        provider_endpoint="qwen/test@provider-a",
        output_contract={
            "required_fields": ["conclusions"],
            "exact_markdown_headings": ["conclusions"],
            "machine_readable_required": False,
        },
        estimated_quality=0.8,
        quality_uncertainty=0.1,
        estimated_cost=0.01,
        failure_probability=0.05,
        request_config={
            "reasoning": {"effort": "high", "exclude": True},
            "provider": {
                "order": ["provider-a"],
                "only": ["provider-a"],
                "allow_fallbacks": False,
                "require_parameters": True,
            },
        },
    )


def recovery_row() -> dict:
    node = selected_node()
    return {
        **node.to_dict(),
        "candidate_id": "recovery-google",
        "model": "google/test",
        "provider_endpoint": "google/test@provider-b",
        "parameter_profile": {
            **dict(node.parameter_profile),
            "model_company": "google",
        },
        "request_config": {
            "reasoning": {"effort": "high", "exclude": True},
            "provider": {
                "order": ["provider-b"],
                "only": ["provider-b"],
                "allow_fallbacks": False,
                "require_parameters": True,
            },
        },
    }


class V5ReasoningSaturationRecoveryTests(unittest.TestCase):
    def test_replacement_for_reasoning_saturated_empty_output_is_visible_only(self) -> None:
        node = selected_node()
        config = RuntimeConfig(2, 1, 0.35)
        graph = SimpleNamespace(nodes=[node], final_nodes=[])
        budget = BudgetController(config, graph)
        requests: list[dict] = []

        def call_fn(_run, payload):
            requests.append(dict(payload))
            if len(requests) == 1:
                self.assertEqual("high", payload["reasoning"]["effort"])
                self.assertNotIn("max_tokens", payload["reasoning"])
                return ({
                    "id": "empty-reasoning",
                    "model": "qwen/test",
                    "provider": "provider-a",
                    "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
                    "usage": {
                        "completion_tokens": 1_100,
                        "completion_tokens_details": {
                            "reasoning_tokens": 1_100,
                        },
                        "cost": 0.001,
                    },
                }, 0.1)
            self.assertNotIn("reasoning", payload)
            return ({
                "id": "visible-answer",
                "model": "google/test",
                "provider": "provider-b",
                "choices": [{
                    "message": {"content": "## conclusions\n\n结论：保持安全隔离。"},
                    "finish_reason": "stop",
                }],
                "usage": {
                    "completion_tokens": 64,
                    "completion_tokens_details": {"reasoning_tokens": 0},
                    "cost": 0.001,
                },
            }, 0.1)

        engine = ExecutionEngine(
            config,
            prompt_policy=PromptPolicy(),
            retry_policy=RetryPolicy(
                retry_same_endpoint_categories=(
                    FailureCategory.PROVIDER_RATE_LIMITED,
                    FailureCategory.PROVIDER_TIMEOUT,
                )
            ),
            recovery_policy=RecoveryPolicy(),
            quality_policy=_Quality(),
            output_policy=OutputPolicy(),
        )
        result = engine.execute_node(
            node,
            "仅依据题面给出结论。",
            [],
            SimpleNamespace(),
            call_fn,
            [recovery_row()],
            budget,
        )
        self.assertEqual("success_recovered", result.status)
        self.assertEqual(2, len(result.attempts))
        self.assertIn("reasoning", requests[0])
        self.assertNotIn("max_tokens", requests[0]["reasoning"])
        self.assertNotIn("reasoning", requests[1])
        self.assertIn(
            "reasoning-saturated-empty-output",
            result.attempts[0].gate_reasons,
        )
        adaptation = result.attempts[1].answer_transformations[-1]
        self.assertEqual("recovery-request-adaptation", adaptation["type"])
        self.assertTrue(adaptation["reasoning_removed"])
        self.assertFalse(adaptation["substantive_prompt_changed"])

    def test_ordinary_empty_output_does_not_disable_reasoning_without_evidence(self) -> None:
        usage = {
            "completion_tokens": 10,
            "completion_tokens_details": {"reasoning_tokens": 0},
        }
        evidence = ExecutionEngine._reasoning_saturation_evidence(
            usage,
            {"reasoning": {"effort": "high"}},
        )
        self.assertFalse(evidence["reasoning_saturated_empty_output"])
        self.assertEqual(0, evidence["requested_reasoning_max_tokens"])


if __name__ == "__main__":
    unittest.main()
