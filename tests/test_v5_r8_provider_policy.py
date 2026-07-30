import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import ExecutionGraph, GraphLimits, SelectedNode  # noqa: E402
import v5_r8_provider_policy as policy  # noqa: E402


def node(name, provider, *, cost=0.01, failure=0.05):
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
        estimated_cost=cost,
        failure_probability=failure,
        request_config={
            "provider": {
                "order": [provider],
                "only": [provider],
                "allow_fallbacks": False,
                "require_parameters": True,
            }
        },
    )


def graph(nodes, *, recovery=None, metadata=None):
    data = dict(metadata or {})
    if recovery is not None:
        data["recovery_pool"] = recovery
    return ExecutionGraph(
        nodes=tuple(nodes),
        edges=(),
        execution_stages=(tuple(row.node_id for row in nodes),),
        entry_nodes=tuple(row.node_id for row in nodes),
        final_nodes=tuple(row.node_id for row in nodes),
        required_work=tuple(f"work-{row.node_id}" for row in nodes),
        estimated_quality=0.8,
        quality_floor=0.6,
        estimated_total_cost=sum(row.estimated_cost for row in nodes),
        metadata=data,
    )


class TestProviderPolicy(unittest.TestCase):
    def test_concentration_without_alternative_warns_but_does_not_destroy_availability(self):
        nodes = tuple(node(name, "p1") for name in ("a", "b", "c"))
        _, report = policy.diversity_aware_preflight(
            graph(nodes), GraphLimits(max_provider_share=0.60)
        )
        self.assertEqual(report["status"], "pass")
        self.assertIn(
            "provider-concentration-above-target-no-budget-safe-alternative",
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
        adjusted, report = policy.diversity_aware_preflight(
            graph(nodes, recovery={"a": [alternative]}),
            GraphLimits(max_provider_share=0.67),
        )
        self.assertEqual(report["provider_counts"], {"p2": 1, "p1": 2})
        self.assertEqual(report["status"], "pass")
        self.assertTrue(any(
            row["reason"] == "provider-concentration-rebalance"
            for row in report["substitutions"]
        ))
        self.assertEqual(adjusted.nodes[0].provider_endpoint, "vendor/a2@p2")

    def test_expensive_provider_rebalance_is_rejected_and_original_graph_survives(self):
        nodes = tuple(node(name, "p1", cost=0.02) for name in ("a", "b", "c"))
        expensive = {
            **nodes[0].to_dict(),
            "candidate_id": "a-expensive",
            "model": "vendor/a2",
            "provider_endpoint": "vendor/a2@p2",
            "estimated_cost": 0.12,
            "request_config": {
                "provider": {
                    "order": ["p2"],
                    "only": ["p2"],
                    "allow_fallbacks": False,
                    "require_parameters": True,
                }
            },
        }
        adjusted, report = policy.diversity_aware_preflight(
            graph(nodes, recovery={"a": [expensive]}),
            GraphLimits(max_provider_share=0.60, max_budget_usd=0.10, cost_risk_multiplier=1.25),
        )
        self.assertEqual(report["status"], "pass")
        self.assertEqual(adjusted.estimated_total_cost, 0.06)
        self.assertEqual(adjusted.nodes[0].provider_endpoint, "vendor/a@p1")
        self.assertTrue(any(
            row["reason"] == "provider-rebalance-would-exceed-raw-budget"
            for row in report["rejected_substitutions"]
        ))

    def test_strict_provider_diversity_fails_closed_without_budget_safe_alternative(self):
        nodes = tuple(node(name, "p1", cost=0.02) for name in ("a", "b", "c"))
        _, report = policy.diversity_aware_preflight(
            graph(nodes, metadata={"provider_diversity_required": True}),
            GraphLimits(max_provider_share=0.60, max_budget_usd=0.10),
        )
        self.assertEqual(report["status"], "rejected")
        self.assertIn("provider-concentration-above-production-limit", report["blockers"])


if __name__ == "__main__":
    unittest.main()
