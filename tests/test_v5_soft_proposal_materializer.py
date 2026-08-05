import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_pipeline  # noqa: E402
import v5_soft_proposal_materializer as soft  # noqa: E402
from execution_graph import (  # noqa: E402
    ExecutionGraph,
    GraphLimits,
    SelectedNode,
)


def _node() -> SelectedNode:
    return SelectedNode(
        node_id="n1",
        assigned_work=("w1",),
        professional_capabilities={"analysis": 0.9},
        functions=("analysis",),
        prompt_profile={},
        reasoning_profile={"effort": "high"},
        parameter_profile={
            "recommended_output_allowance_tokens": 100_000,
        },
        model="vendor/model",
        provider_endpoint="vendor/model@provider",
        output_contract={},
        estimated_quality=0.9,
        quality_uncertainty=0.1,
        estimated_cost=2.0,
        request_config={
            "max_tokens": 100_000,
            "reasoning": {
                "effort": "high",
                "max_tokens": 80_000,
            },
            "provider": {
                "only": ["provider"],
                "order": ["provider"],
                "allow_fallbacks": False,
            },
        },
    )


def _graph() -> ExecutionGraph:
    node = _node()
    return ExecutionGraph(
        nodes=(node,),
        edges=(),
        execution_stages=(("n1",),),
        entry_nodes=("n1",),
        final_nodes=("n1",),
        required_work=("w1",),
        estimated_quality=0.9,
        quality_floor=0.8,
        estimated_total_cost=2.0,
        metadata={
            "recovery_pool": {
                "n1": [
                    {
                        "request_config": {
                            "max_completion_tokens": 100_000,
                            "reasoning": {"token_budget": 80_000},
                        },
                        "parameter_profile": {
                            "recommended_output_allowance_tokens": 100_000,
                        },
                    }
                ]
            }
        },
    )


def _materialize_kwargs() -> dict[str, object]:
    return {
        "approved_total_calls": 4,
        "governance_calls_reserved": 3,
        "approved_recovery_calls": 0,
        "cost_anomaly_usd": 0.01,
    }


class SoftProposalMaterializerTests(unittest.TestCase):
    def test_pipeline_uses_soft_materializer(self):
        self.assertEqual(
            v5_pipeline.materialize_proposal.__module__,
            "v5_soft_proposal_materializer",
        )

    def test_structural_validation_error_is_preserved(self):
        error = soft.structural.ProposalValidationError(
            "recovery output advisory exceeds provider-native capacity"
        )
        with mock.patch.object(
            soft.structural,
            "materialize_proposal",
            side_effect=error,
        ):
            with self.assertRaises(
                soft.structural.ProposalValidationError
            ) as raised:
                soft.materialize_proposal(
                    {},
                    "task",
                    {},
                    {},
                    **_materialize_kwargs(),
                )
        self.assertIs(error, raised.exception)

    def test_deterministic_violations_reports_structural_error(self):
        error = soft.structural.ProposalValidationError(
            "recovery endpoint lacks required context capacity"
        )
        with mock.patch.object(
            soft.structural,
            "materialize_proposal",
            side_effect=error,
        ):
            violations = soft.deterministic_violations(
                {},
                "task",
                {},
                {},
                **_materialize_kwargs(),
            )
        self.assertEqual([str(error)], violations)

    def test_soft_materialization_removes_caps_before_artifact(self):
        structural_graph = _graph()
        structural_limits = GraphLimits(
            max_budget_usd=0.01,
            max_output_allowance_tokens=32_768,
        )
        structural_audit = {
            "status": "PASS",
            "risk_adjusted_reserved_cost_usd": 5.0,
        }
        with mock.patch.object(
            soft.structural,
            "materialize_proposal",
            return_value=(
                structural_graph,
                structural_limits,
                structural_audit,
            ),
        ) as structural_call:
            graph, limits, audit = soft.materialize_proposal(
                {},
                "task",
                {},
                {},
                **_materialize_kwargs(),
            )

        self.assertIsNone(
            structural_call.call_args.kwargs["cost_anomaly_usd"]
        )
        request = graph.nodes[0].request_config
        self.assertNotIn("max_tokens", request)
        self.assertNotIn("max_completion_tokens", request)
        self.assertNotIn("max_tokens", request["reasoning"])
        recovery = graph.metadata["recovery_pool"]["n1"][0]
        self.assertNotIn(
            "max_completion_tokens",
            recovery["request_config"],
        )
        self.assertNotIn(
            "token_budget",
            recovery["request_config"].get("reasoning", {}),
        )
        self.assertIsNone(limits.max_budget_usd)
        self.assertIsNone(limits.max_output_allowance_tokens)
        self.assertTrue(audit["cost_advisory_exceeded"])
        self.assertFalse(
            audit["cost_threshold_can_reject_materialization"]
        )
        self.assertFalse(audit["local_token_ceiling_enforced"])
        self.assertTrue(
            audit["request_token_fields_removed_before_artifact"]
        )


if __name__ == "__main__":
    unittest.main()
