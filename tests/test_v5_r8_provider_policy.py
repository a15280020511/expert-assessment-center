import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import ExecutionGraph, GraphLimits, SelectedNode  # noqa: E402
import v5_r8_provider_policy as policy  # noqa: E402


def node(name, provider):
    return SelectedNode(
        node_id=name,
        assigned_work=(f"work-{name}",),
        professional_capabilities={"general_analysis": 0.8},
        functions=("analysis",),
        prompt_profile={"modules": []},
        reasoning_profile={"reasoning_enabled": True, "effort": "medium"},
        parameter_profile={"supported_parameters": ["max_tokens"]},
        model=f"vendor/{name}",
        provider_endpoint=f"vendor/{name}@{provider}",
        output_contract={
            "required_fields": ["conclusions"],
            "machine_readable_required": False,
        },
        estimated_quality=0.8,
        quality_uncertainty=0.1,
        estimated_cost=0.01,
        failure_probability=0.05,
        request_config={
            "provider": {
                "order": [provider],
                "only": [provider],
                "allow_fallbacks": False,
                "require_parameters": True,
            }
        },
    )


class TestProviderPolicy(unittest.TestCase):
    def test_concentration_without_alternative_warns_but_does_not_destroy_availability(self):
        nodes = tuple(node(name, "p1") for name in ("a", "b", "c"))
        graph = ExecutionGraph(
            nodes=nodes,
            edges=(),
            execution_stages=(("a", "b", "c"),),
            entry_nodes=("a", "b", "c"),
            final_nodes=("a", "b", "c"),
            required_work=tuple(f"work-{name}" for name in ("a", "b", "c")),
            estimated_quality=0.8,
            quality_floor=0.6,
            estimated_total_cost=0.03,
            metadata={},
        )
        _, report = policy.diversity_aware_preflight(
            graph, GraphLimits(max_provider_share=0.60)
        )
        self.assertEqual(report["status"], "pass")
        self.assertIn(
            "provider-concentration-above-target-no-safe-alternative",
            report["warnings"],
        )

    def test_recovery_pool_rebalances_provider_before_execution(self):
        nodes = tuple(node(name, "p1") for name in ("a", "b", "c"))
        alternative = {
            **nodes[0].to_dict(),
            "candidate_id": "a-p2",
            "model": "vendor/a2",
            "provider_endpoint": "vendor/a2@p2",
            "request_config": {
                "provider": {
                    "order": ["p2"],
                    "only": ["p2"],
                    "allow_fallbacks": False,
                    "require_parameters": True,
                }
            },
        }
        graph = ExecutionGraph(
            nodes=nodes,
            edges=(),
            execution_stages=(("a", "b", "c"),),
            entry_nodes=("a", "b", "c"),
            final_nodes=("a", "b", "c"),
            required_work=tuple(f"work-{name}" for name in ("a", "b", "c")),
            estimated_quality=0.8,
            quality_floor=0.6,
            estimated_total_cost=0.03,
            metadata={"recovery_pool": {"a": [alternative]}},
        )
        adjusted, report = policy.diversity_aware_preflight(
            graph, GraphLimits(max_provider_share=0.67)
        )
        self.assertEqual(report["provider_counts"], {"p2": 1, "p1": 2})
        self.assertEqual(report["status"], "pass")
        self.assertTrue(any(
            row["reason"] == "provider-concentration-rebalance"
            for row in report["substitutions"]
        ))
        self.assertEqual(adjusted.nodes[0].provider_endpoint, "vendor/a2@p2")


if __name__ == "__main__":
    unittest.main()
