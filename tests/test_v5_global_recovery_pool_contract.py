import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_soft_proposal_materializer as materializer  # noqa: E402
from execution_graph import ExecutionGraph, SelectedNode  # noqa: E402
from v5_constitutional_runtime import ConstitutionalExecutionEngine  # noqa: E402
from v5_runtime import RuntimeConfig  # noqa: E402
from v5_soft_resource_governance import (  # noqa: E402
    SoftResourceBudgetController,
    SoftResourceExecutionEngine,
)


def _node(node_id: str, model: str, functions: tuple[str, ...]) -> SelectedNode:
    provider = model.split("/", 1)[0]
    return SelectedNode(
        node_id=node_id,
        assigned_work=(f"work-{node_id}",),
        professional_capabilities={value: 1.0 for value in functions},
        functions=functions,
        prompt_profile={},
        reasoning_profile={"effort": "medium"},
        parameter_profile={},
        model=model,
        provider_endpoint=f"{model}@{provider}",
        output_contract={},
        estimated_quality=0.0,
        quality_uncertainty=0.0,
        estimated_cost=0.01,
        request_config={
            "provider": {
                "only": [provider],
                "order": [provider],
                "allow_fallbacks": False,
            }
        },
    )


def _graph() -> ExecutionGraph:
    nodes = (
        _node("independent-1", "vendor-a/main", ("independent_analysis",)),
        _node("independent-2", "vendor-b/main", ("independent_analysis",)),
        _node("review", "vendor-c/main", ("cross_review",)),
        _node("final", "vendor-d/main", ("final_synthesis",)),
    )
    return ExecutionGraph(
        nodes=nodes,
        edges=(),
        execution_stages=(
            ("independent-1", "independent-2"),
            ("review",),
            ("final",),
        ),
        entry_nodes=("independent-1", "independent-2"),
        final_nodes=("final",),
        required_work=tuple(
            work for node in nodes for work in node.assigned_work
        ),
        estimated_quality=0.0,
        quality_floor=0.0,
        estimated_total_cost=0.04,
        metadata={},
    )


def _recovery(model: str) -> dict[str, object]:
    provider = model.split("/", 1)[0]
    return {
        "model": model,
        "provider_endpoint": f"{model}@{provider}",
        "estimated_cost": 0.01,
        "request_config": {
            "provider": {
                "only": [provider],
                "order": [provider],
                "allow_fallbacks": False,
            }
        },
    }


def _original_recovery_assignments() -> dict[str, list[dict[str, object]]]:
    # Mapping insertion order is intentionally opposite to governance priority.
    return {
        "independent-1": [_recovery("moonshotai/kimi-k3")],
        "independent-2": [_recovery("anthropic/claude-opus-5")],
        "review": [_recovery("nvidia/nemotron-3-ultra")],
        "final": [_recovery("z-ai/glm-5.2")],
    }


class GlobalRecoveryPoolContractTests(unittest.TestCase):
    def test_materializer_recovers_governance_round_robin_order(self):
        graph = _graph()
        # The governed orchestrator assigned the signed sequence as:
        # final -> review -> independent-1 -> independent-2.
        ordered = materializer._unique_recovery_candidates(
            graph,
            _original_recovery_assignments(),
        )
        self.assertEqual(
            [
                "z-ai/glm-5.2",
                "nvidia/nemotron-3-ultra",
                "moonshotai/kimi-k3",
                "anthropic/claude-opus-5",
            ],
            [row["model"] for row in ordered],
        )
        self.assertEqual(
            ["final", "review", "independent-1", "independent-2"],
            [row["governance_recovery_owner_node_id"] for row in ordered],
        )

    def test_priority_protection_prevents_lower_tier_starvation(self):
        graph = _graph()
        shared = materializer._shared_recovery_pool(
            graph,
            _original_recovery_assignments(),
        )
        models = {
            node_id: [str(row["model"]) for row in rows]
            for node_id, rows in shared.items()
        }
        self.assertEqual(
            [
                "z-ai/glm-5.2",
                "nvidia/nemotron-3-ultra",
                "moonshotai/kimi-k3",
                "anthropic/claude-opus-5",
            ],
            models["final"],
        )
        self.assertEqual(
            [
                "nvidia/nemotron-3-ultra",
                "moonshotai/kimi-k3",
                "anthropic/claude-opus-5",
            ],
            models["review"],
        )
        self.assertEqual(
            ["moonshotai/kimi-k3", "anthropic/claude-opus-5"],
            models["independent-1"],
        )
        self.assertEqual(
            ["anthropic/claude-opus-5"],
            models["independent-2"],
        )
        self.assertNotIn("z-ai/glm-5.2", models["independent-1"])
        self.assertNotIn("nvidia/nemotron-3-ultra", models["independent-1"])

    def test_replacement_identity_and_company_are_consumed_once(self):
        graph = _graph()
        budget = SoftResourceBudgetController(
            RuntimeConfig(
                total_call_limit=8,
                recovery_call_limit=4,
                cost_anomaly_usd=None,
            ),
            graph,
        )
        first, _ = budget.reserve_replacement_identity(
            "moonshotai/kimi-k3",
            "moonshotai/kimi-k3@morph",
            "independent-1",
        )
        duplicate_identity, identity_reason = budget.reserve_replacement_identity(
            "moonshotai/kimi-k3",
            "moonshotai/kimi-k3@morph",
            "review",
        )
        duplicate_company, company_reason = budget.reserve_replacement_identity(
            "moonshotai/kimi-k2",
            "moonshotai/kimi-k2@moonshotai",
            "review",
        )
        other, _ = budget.reserve_replacement_identity(
            "z-ai/glm-5.2",
            "z-ai/glm-5.2@decart/fp4",
            "review",
        )
        self.assertTrue(first)
        self.assertFalse(duplicate_identity)
        self.assertEqual("recovery-candidate-already-consumed", identity_reason)
        self.assertFalse(duplicate_company)
        self.assertEqual("recovery-company-already-consumed", company_reason)
        self.assertTrue(other)
        snapshot = budget.snapshot()["global_recovery_identity_guard"]
        self.assertEqual(2, len(snapshot["reserved_identities"]))
        self.assertFalse(snapshot["duplicate_calls_allowed"])

    def test_engine_skips_duplicate_before_parent_call(self):
        graph = _graph()
        budget = SoftResourceBudgetController(
            RuntimeConfig(
                total_call_limit=8,
                recovery_call_limit=4,
                cost_anomaly_usd=None,
            ),
            graph,
        )
        engine = object.__new__(SoftResourceExecutionEngine)
        selected = graph.nodes[0]
        replacement = _node(
            selected.node_id,
            "moonshotai/kimi-k3",
            selected.functions,
        )
        with mock.patch.object(
            ConstitutionalExecutionEngine,
            "_recorded_call",
            return_value="called",
        ) as parent:
            first = engine._recorded_call(
                selected,
                [],
                "task",
                [],
                None,
                lambda *_: ({}, 0.0),
                budget,
                replacement,
                "replacement",
            )
            second = engine._recorded_call(
                selected,
                [],
                "task",
                [],
                None,
                lambda *_: ({}, 0.0),
                budget,
                replacement,
                "replacement",
            )
        self.assertEqual("called", first)
        self.assertIsNone(second)
        self.assertEqual(1, parent.call_count)


if __name__ == "__main__":
    unittest.main()
