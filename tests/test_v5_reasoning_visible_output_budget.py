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
        },
        estimated_quality=0.82,
        quality_uncertainty=0.08,
        estimated_cost=0.01,
        failure_probability=0.05,
        request_config={
            "reasoning": {"effort": effort, "exclude": True},
            "max_tokens": allowance,
            "provider": {
                "order": ["provider-a"],
                "only": ["provider-a"],
                "allow_fallbacks": False,
                "require_parameters": True,
            },
        },
    )


class TestReasoningVisibleOutputBudget(unittest.TestCase):
    def test_high_reasoning_request_uses_effort_not_token_budget(self):
        node = _node()
        telemetry = hardening.completion_token_budget(node)
        payload = hardening.hardened_build_node_payload(node, "审计任务", [])

        self.assertNotIn("max_tokens", payload)
        self.assertNotIn("max_completion_tokens", payload)
        self.assertEqual(payload["reasoning"]["effort"], "high")
        self.assertTrue(payload["reasoning"]["exclude"])
        self.assertNotIn("max_tokens", payload["reasoning"])
        self.assertIsNone(telemetry["reasoning_max_tokens"])
        self.assertIsNone(telemetry["visible_output_reserve_tokens"])
        self.assertFalse(telemetry["local_token_ceiling_enforced"])
        self.assertFalse(telemetry["reasoning_token_budget_enforced"])

    def test_large_advisory_is_preserved_as_telemetry_without_cap(self):
        node = _node(
            functions=("synthesis",),
            allowance=100_000,
            fields=[f"section_{index}" for index in range(12)],
        )
        telemetry = hardening.completion_token_budget(node)
        payload = hardening.hardened_build_node_payload(
            node,
            "综合裁决任务",
            [],
        )

        self.assertEqual(
            telemetry["total_completion_advisory_tokens"],
            100_000,
        )
        self.assertEqual(
            telemetry["total_completion_allowance_tokens"],
            100_000,
        )
        self.assertNotIn("max_tokens", payload)
        self.assertNotIn("max_completion_tokens", payload)

    def test_native_endpoint_capacity_only_bounds_cost_estimate(self):
        work = {
            "context_requirements": {
                "expected_output_tokens": 20_000,
                "expected_reasoning_tokens": 10_000,
            }
        }
        self.assertEqual(hardening.completion_envelope(work, 8_192), 8_192)
        self.assertGreater(hardening.completion_envelope(work, 0), 8_192)

    def test_non_reasoning_node_has_no_reasoning_or_output_cap(self):
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
                "max_tokens": 2_048,
                "provider": dict(node.request_config["provider"]),
            },
        })
        telemetry = hardening.completion_token_budget(node)
        payload = hardening.hardened_build_node_payload(node, "普通任务", [])

        self.assertFalse(telemetry["local_token_ceiling_enforced"])
        self.assertNotIn("reasoning", payload)
        self.assertNotIn("max_tokens", payload)
        self.assertNotIn("max_completion_tokens", payload)


if __name__ == "__main__":
    unittest.main()
