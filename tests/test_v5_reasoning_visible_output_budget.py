import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import SelectedNode  # noqa: E402
import v5_cost_reliability_hardening as hardening  # noqa: E402


def _node(
    *,
    functions=("adversarial_reasoning",),
    effort="high",
    allowance=10_000,
    fields=None,
    explicit_sections=0,
    long_form=False,
):
    required_fields = fields or [
        "findings",
        "counterarguments",
        "failure_modes",
        "mitigations",
    ]
    return SelectedNode(
        node_id="red-team",
        assigned_work=("work-red-team",),
        professional_capabilities={"adversarial_reasoning": 0.9},
        functions=tuple(functions),
        prompt_profile={"modules": ["adversarial_review"]},
        reasoning_profile={"reasoning_enabled": True, "effort": effort},
        parameter_profile={
            "supported_parameters": ["reasoning", "max_tokens"],
            "recommended_output_allowance_tokens": allowance,
            "dynamic_parameter_decisions": {
                "reasoning_effort": effort,
            },
        },
        model="company/red-team-model",
        provider_endpoint="company/red-team-model@provider-a",
        output_contract={
            "required_fields": required_fields,
            "machine_readable_required": False,
            "task_explicit_delivery_section_count": explicit_sections,
            "task_explicit_long_form_required": long_form,
        },
        estimated_quality=0.82,
        quality_uncertainty=0.08,
        estimated_cost=0.01,
        failure_probability=0.05,
        request_config={
            "reasoning": {"effort": effort, "exclude": True},
            "provider": {
                "order": ["provider-a"],
                "only": ["provider-a"],
                "allow_fallbacks": False,
                "require_parameters": True,
            },
        },
    )


class TestReasoningVisibleOutputBudget(unittest.TestCase):
    def test_high_reasoning_red_team_reserves_visible_delivery_tokens(self):
        node = _node()
        budget = hardening.completion_token_budget(node)
        payload = hardening.hardened_build_node_payload(node, "审计任务", [])

        self.assertEqual(payload["max_tokens"], 10_000)
        self.assertEqual(
            payload["reasoning"]["max_tokens"],
            budget["reasoning_max_tokens"],
        )
        self.assertTrue(payload["reasoning"]["exclude"])
        self.assertNotIn("effort", payload["reasoning"])
        self.assertLess(
            payload["reasoning"]["max_tokens"],
            payload["max_tokens"],
        )
        self.assertGreaterEqual(
            payload["max_tokens"] - payload["reasoning"]["max_tokens"],
            budget["visible_output_reserve_tokens"],
        )
        self.assertGreaterEqual(
            budget["visible_output_reserve_tokens"],
            2_048,
        )

    def test_long_form_synthesis_gets_larger_protected_visible_reserve(self):
        node = _node(
            functions=("synthesis",),
            allowance=15_992,
            fields=[f"section_{index}" for index in range(12)],
            explicit_sections=12,
            long_form=True,
        )
        budget = hardening.completion_token_budget(node)
        payload = hardening.hardened_build_node_payload(node, "综合裁决任务", [])

        self.assertEqual(payload["max_tokens"], 15_992)
        self.assertGreaterEqual(
            budget["visible_output_reserve_tokens"],
            4_096,
        )
        self.assertEqual(
            payload["max_tokens"] - payload["reasoning"]["max_tokens"],
            budget["visible_output_reserve_tokens"],
        )

    def test_regression_reasoning_heavy_empty_body_signature_is_prevented(self):
        node = _node(allowance=10_000)
        budget = hardening.completion_token_budget(node)

        observed_completion_tokens = 8_948
        observed_reasoning_tokens = 8_872
        observed_visible_tokens = (
            observed_completion_tokens - observed_reasoning_tokens
        )

        self.assertEqual(observed_visible_tokens, 76)
        self.assertGreater(
            observed_reasoning_tokens,
            budget["reasoning_max_tokens"],
        )
        self.assertGreaterEqual(
            budget["total_completion_allowance_tokens"]
            - budget["reasoning_max_tokens"],
            budget["visible_output_reserve_tokens"],
        )
        self.assertGreater(
            budget["visible_output_reserve_tokens"],
            observed_visible_tokens,
        )

    def test_non_reasoning_node_keeps_entire_allowance_for_visible_output(self):
        node = _node(functions=("analysis",), effort="low", allowance=2_048)
        node = SelectedNode(**{
            **node.to_dict(),
            "assigned_work": tuple(node.assigned_work),
            "functions": tuple(node.functions),
            "reasoning_profile": {
                "reasoning_enabled": False,
                "effort": "low",
            },
            "request_config": {
                "provider": dict(node.request_config["provider"]),
            },
        })
        budget = hardening.completion_token_budget(node)
        payload = hardening.hardened_build_node_payload(node, "普通任务", [])

        self.assertEqual(budget["reasoning_max_tokens"], 0)
        self.assertEqual(budget["visible_output_reserve_tokens"], 2_048)
        self.assertNotIn("reasoning", payload)
        self.assertEqual(payload["max_tokens"], 2_048)


if __name__ == "__main__":
    unittest.main()
