import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import ExecutionGraph, GraphLimits, SelectedNode  # noqa: E402
from execution_graph_validator import validate_execution_graph  # noqa: E402
import v5_r8_provider_policy as policy  # noqa: E402


def node(
    name,
    provider,
    *,
    cost=0.01,
    failure=0.05,
    model=None,
    independence_group=None,
):
    model = model or f"vendor/{name}"
    return SelectedNode(
        node_id=name,
        assigned_work=(f"work-{name}",),
        professional_capabilities={"general_analysis": 0.8},
        functions=("analysis",),
        prompt_profile={"modules": []},
        reasoning_profile={"reasoning_enabled": True, "effort": "medium"},
        parameter_profile={"supported_parameters": ["max_tokens"]},
        model=model,
        provider_endpoint=f"{model}@{provider}",
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
        independence_group=independence_group,
    )


def candidate(selected, *, model, provider, cost=None, failure=None):
    value = selected.to_dict()
    value.update({
        "candidate_id": f"{selected.node_id}-{provider}-{model}",
        "model": model,
        "provider_endpoint": f"{model}@{provider}",
        "estimated_cost": selected.estimated_cost if cost is None else cost,
        "failure_probability": selected.failure_probability if failure is None else failure,
        "request_config": {
            "provider": {
                "order": [provider],
                "only": [provider],
                "allow_fallbacks": False,
                "require_parameters": True,
            }
        },
    })
    return value


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
        adjusted, report = policy.diversity_aware_preflight(
            graph(nodes), GraphLimits(max_provider_share=0.60)
        )
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["post_substitution_validation"]["status"], "PASS")
        self.assertFalse(validate_execution_graph(adjusted, GraphLimits(max_provider_share=0.60)))
        self.assertIn(
            "provider-concentration-above-target-no-budget-safe-alternative",
            report["warnings"],
        )

    def test_recovery_pool_rebalances_provider_before_execution(self):
        nodes = tuple(node(name, "p1") for name in ("a", "b", "c"))
        alternative = candidate(nodes[0], model="vendor/a2", provider="p2")
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
        expensive = candidate(nodes[0], model="vendor/a2", provider="p2", cost=0.12)
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

    def test_provider_rebalance_cannot_duplicate_model_inside_independence_group(self):
        nodes = (
            node("independent-a", "p1", model="vendor/model-a", independence_group="work-x"),
            node("independent-b", "p1", model="vendor/model-b", independence_group="work-x"),
            node("ordinary", "p1", model="vendor/model-c"),
        )
        # This is the exact failure shape observed in Stage-D: changing Provider would
        # make both independent replicas use model-a. It must be rejected even though
        # it is cheap, reliable, and helps Provider concentration.
        unsafe = candidate(
            nodes[1],
            model="vendor/model-a",
            provider="p2",
            cost=0.005,
            failure=0.01,
        )
        adjusted, report = policy.diversity_aware_preflight(
            graph(nodes, recovery={"independent-b": [unsafe]}),
            GraphLimits(max_provider_share=0.60),
        )
        self.assertEqual(report["status"], "pass")
        self.assertEqual(adjusted.nodes[1].model, "vendor/model-b")
        self.assertEqual(adjusted.nodes[1].provider_endpoint, "vendor/model-b@p1")
        self.assertEqual(report["post_substitution_validation"]["status"], "PASS")
        self.assertTrue(any(
            row["reason"] == "provider-rebalance-would-break-independent-model-diversity"
            for row in report["rejected_substitutions"]
        ))
        self.assertFalse(any(
            issue.code == "independent_same_model"
            for issue in validate_execution_graph(adjusted, GraphLimits(max_provider_share=0.60))
        ))

    def test_reliability_replacement_also_preserves_independent_model_diversity(self):
        nodes = (
            node("a", "p1", model="vendor/model-a", independence_group="work-x"),
            node(
                "b",
                "p1",
                model="vendor/model-b",
                independence_group="work-x",
                failure=0.50,
            ),
        )
        unsafe = candidate(
            nodes[1],
            model="vendor/model-a",
            provider="p2",
            failure=0.01,
        )
        _, report = policy.diversity_aware_preflight(
            graph(nodes, recovery={"b": [unsafe]}),
            GraphLimits(max_node_failure_probability=0.20),
        )
        self.assertEqual(report["status"], "rejected")
        self.assertIn("required-node-risk-above-threshold:b", report["blockers"])
        self.assertTrue(any(
            row["reason"] == "reliability-replacement-would-break-independent-model-diversity"
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
