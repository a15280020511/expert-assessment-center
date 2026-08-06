from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import ExecutionGraph, SelectedNode  # noqa: E402
from v5_soft_proposal_materializer import _soft_graph  # noqa: E402


def node(node_id: str, work: str, role: str, effort: str) -> SelectedNode:
    reasoning_enabled = effort != "none"
    request = {
        "provider": {
            "only": ["selected-provider"],
            "order": ["selected-provider"],
            "allow_fallbacks": False,
        }
    }
    if reasoning_enabled:
        request["reasoning"] = {"effort": effort, "exclude": True}
    return SelectedNode(
        node_id=node_id,
        assigned_work=(work,),
        professional_capabilities={role: 1.0},
        functions=(role,),
        prompt_profile={"role": role},
        reasoning_profile={
            "reasoning_enabled": reasoning_enabled,
            "effort": effort,
        },
        parameter_profile={
            "supported_parameters": ["selected-only"],
            "recommended_output_allowance_tokens": 4096,
        },
        model=f"selected/{node_id}",
        provider_endpoint=f"selected/{node_id}@selected-provider",
        output_contract={
            "required_fields": [f"{role}-output"],
            "final_delivery_node": role == "synthesis",
        },
        estimated_quality=0.8,
        quality_uncertainty=0.1,
        estimated_cost=0.01,
        request_config=request,
    )


class SharedGovernanceRecoveryPoolTests(unittest.TestCase):
    def test_one_synthesis_recovery_is_protected_from_lower_tiers(self) -> None:
        independent = node("independent", "work-independent", "analysis", "medium")
        review = node("review", "work-review", "review", "none")
        synthesis = node("synthesis", "work-synthesis", "synthesis", "high")
        recovery = {
            "candidate_id": "recovery:synthesis:google/gemini-2.5-pro@vertex",
            "assigned_work": ["work-synthesis"],
            "professional_capabilities": {"synthesis": 1.0},
            "functions": ["synthesis"],
            "prompt_profile": {"role": "synthesis"},
            "reasoning_profile": {"reasoning_enabled": True, "effort": "high"},
            "parameter_profile": {
                "supported_parameters": ["reasoning", "temperature"],
                "recommended_output_allowance_tokens": 6144,
            },
            "model": "google/gemini-2.5-pro",
            "provider_endpoint": "google/gemini-2.5-pro@vertex",
            "provider_slug": "vertex",
            "output_contract": {
                "required_fields": ["final"],
                "final_delivery_node": True,
            },
            "estimated_quality": 0.0,
            "quality_uncertainty": 0.0,
            "estimated_cost": 0.04,
            "failure_probability": 0.0,
            "request_config": {
                "provider": {
                    "only": ["vertex"],
                    "order": ["vertex"],
                    "allow_fallbacks": False,
                },
                "reasoning": {"effort": "high", "exclude": True},
                "max_tokens": 6144,
            },
        }
        graph = ExecutionGraph(
            nodes=(independent, review, synthesis),
            edges=(),
            execution_stages=(("independent", "review"), ("synthesis",)),
            entry_nodes=("independent", "review"),
            final_nodes=("synthesis",),
            required_work=("work-independent", "work-review", "work-synthesis"),
            estimated_quality=0.8,
            quality_floor=0.7,
            estimated_total_cost=0.03,
            metadata={
                "recovery_pool": {
                    "independent": [],
                    "review": [],
                    "synthesis": [recovery],
                }
            },
        )

        softened = _soft_graph(graph)
        pool = softened.metadata["recovery_pool"]
        policy = softened.metadata["recovery_pool_policy"]
        self.assertEqual(set(pool), {"independent", "review", "synthesis"})
        self.assertEqual(pool["independent"], [])
        self.assertEqual(pool["review"], [])
        self.assertEqual(len(pool["synthesis"]), 1)
        self.assertEqual(policy["candidate_count"], 1)
        self.assertEqual(
            policy["availability_policy"],
            "governance-priority-protected-suffix",
        )
        self.assertEqual(
            policy["candidate_owners"],
            [
                {
                    "model": "google/gemini-2.5-pro",
                    "owner_node_id": "synthesis",
                }
            ],
        )

        synthesis_recovery = pool["synthesis"][0]
        self.assertEqual(
            synthesis_recovery["model"], "google/gemini-2.5-pro"
        )
        self.assertEqual(
            synthesis_recovery["assigned_work"], ["work-synthesis"]
        )
        self.assertEqual(
            synthesis_recovery["output_contract"], synthesis.output_contract
        )
        self.assertEqual(
            synthesis_recovery["request_config"]["provider"]["only"],
            ["vertex"],
        )
        self.assertEqual(
            synthesis_recovery["request_config"]["reasoning"]["effort"],
            "high",
        )
        self.assertNotIn("max_tokens", synthesis_recovery["request_config"])
        self.assertEqual(
            synthesis_recovery["parameter_profile"]["supported_parameters"],
            ["reasoning", "temperature"],
        )
        self.assertEqual(
            synthesis_recovery["parameter_profile"][
                "recommended_output_allowance_tokens"
            ],
            4096,
        )
        self.assertTrue(
            synthesis_recovery["parameter_profile"][
                "governance_priority_protected"
            ]
        )


if __name__ == "__main__":
    unittest.main()
