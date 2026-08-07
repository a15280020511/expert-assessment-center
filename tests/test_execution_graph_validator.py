import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import (  # noqa: E402
    ExecutionGraph,
    GraphLimits,
    SelectedEdge,
    SelectedNode,
)
from execution_graph_validator import (  # noqa: E402
    derive_execution_stages,
    validate_execution_graph,
)


def node(
    node_id,
    work,
    model,
    *,
    group=None,
    cost=0.1,
    request_config=None,
):
    return SelectedNode(
        node_id=node_id,
        assigned_work=(work,),
        professional_capabilities={"analysis": 0.9},
        functions=("analysis",),
        prompt_profile={"modules": ["scope_control"]},
        reasoning_profile={"strategy": "analysis"},
        parameter_profile={"temperature": 0.05},
        model=model,
        provider_endpoint=f"provider-{node_id}",
        output_contract={"type": "object"},
        estimated_quality=0.9,
        quality_uncertainty=0.05,
        estimated_cost=cost,
        failure_probability=0.01,
        request_config=request_config or {"messages": []},
        independence_group=group,
    )


class ExecutionGraphValidatorTests(unittest.TestCase):
    def valid_graph(self):
        nodes = (
            node(
                "analysis",
                "work-analysis",
                "vendor-a/model-a",
                group="independent-core",
            ),
            node(
                "review",
                "work-review",
                "vendor-b/model-b",
                group="independent-core",
            ),
            node("final", "work-final", "vendor-c/model-c"),
        )
        edges = (
            SelectedEdge(
                "analysis",
                "final",
                "synthesis",
                "structured_conclusion",
                "allow",
            ),
            SelectedEdge(
                "review",
                "final",
                "synthesis",
                "structured_conclusion",
                "allow",
            ),
        )
        return ExecutionGraph(
            nodes=nodes,
            edges=edges,
            execution_stages=(("analysis", "review"), ("final",)),
            entry_nodes=("analysis", "review"),
            final_nodes=("final",),
            required_work=(
                "work-analysis",
                "work-review",
                "work-final",
            ),
            estimated_quality=0.91,
            quality_floor=0.89,
            estimated_total_cost=0.3,
            metadata={"version": 5},
        )

    def test_valid_graph_passes_all_fixed_invariants(self):
        self.assertEqual(validate_execution_graph(self.valid_graph()), ())

    def test_cycle_is_rejected(self):
        graph = self.valid_graph()
        cyclic = replace(
            graph,
            edges=graph.edges
            + (
                SelectedEdge(
                    "final",
                    "analysis",
                    "correction",
                    "full_text",
                    "allow",
                ),
            ),
            execution_stages=(("review",), ("final",), ("analysis",)),
            entry_nodes=("review",),
            final_nodes=("analysis",),
        )
        codes = {issue.code for issue in validate_execution_graph(cyclic)}
        self.assertIn("cycle", codes)

    def test_tool_fields_are_rejected_recursively(self):
        graph = self.valid_graph()
        bad = replace(
            graph,
            nodes=(
                replace(
                    graph.nodes[0],
                    request_config={"nested": {"tool_choice": "auto"}},
                ),
                *graph.nodes[1:],
            ),
        )
        codes = {issue.code for issue in validate_execution_graph(bad)}
        self.assertIn("tool_field", codes)

    def test_independent_nodes_cannot_exchange_or_reuse_exact_model(self):
        graph = self.valid_graph()
        bad_nodes = (
            graph.nodes[0],
            replace(graph.nodes[1], model=graph.nodes[0].model),
            graph.nodes[2],
        )
        bad_edges = graph.edges + (
            SelectedEdge(
                "analysis",
                "review",
                "review",
                "full_text",
                "allow",
            ),
        )
        bad = replace(
            graph,
            nodes=bad_nodes,
            edges=bad_edges,
            execution_stages=(("analysis",), ("review",), ("final",)),
        )
        codes = {issue.code for issue in validate_execution_graph(bad)}
        self.assertIn("independent_same_model", codes)
        self.assertIn("independent_visibility", codes)
        self.assertIn("model_identity_reuse", codes)

    def test_same_company_different_models_are_allowed_globally(self):
        graph = self.valid_graph()
        allowed = replace(
            graph,
            nodes=(
                graph.nodes[0],
                replace(graph.nodes[1], model="vendor-a/model-b"),
                graph.nodes[2],
            ),
        )
        codes = {issue.code for issue in validate_execution_graph(allowed)}
        self.assertNotIn("model_identity_reuse", codes)
        self.assertNotIn("model_company_reuse", codes)

    def test_required_work_is_hard_but_cost_threshold_is_advisory(self):
        graph = replace(
            self.valid_graph(),
            required_work=self.valid_graph().required_work + ("missing",),
        )
        issues = validate_execution_graph(
            graph,
            GraphLimits(max_budget_usd=0.2),
        )
        codes = {issue.code for issue in issues}
        self.assertIn("work_coverage", codes)
        self.assertNotIn("budget_limit", codes)

    def test_topological_stages_are_deterministic(self):
        self.assertEqual(
            derive_execution_stages(self.valid_graph()),
            (("analysis", "review"), ("final",)),
        )


if __name__ == "__main__":
    unittest.main()
