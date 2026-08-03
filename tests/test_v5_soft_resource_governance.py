import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import ExecutionGraph, SelectedNode  # noqa: E402
from v5_runtime import RetryPolicy, RuntimeConfig  # noqa: E402
from v5_soft_resource_governance import (  # noqa: E402
    SOFT_RESOURCE_INSTRUCTION,
    SoftResourceBudgetController,
    _without_local_token_caps,
    build_runtime,
)


def node() -> SelectedNode:
    return SelectedNode(
        node_id="n1",
        assigned_work=("w1",),
        professional_capabilities={},
        functions=("synthesis",),
        prompt_profile={},
        reasoning_profile={"effort": "high"},
        parameter_profile={},
        model="company/model",
        provider_endpoint="company/model@provider",
        output_contract={},
        estimated_quality=0.9,
        quality_uncertainty=0.1,
        estimated_cost=1.0,
        failure_probability=0.01,
        request_config={},
    )


def graph() -> ExecutionGraph:
    selected = node()
    return ExecutionGraph(
        nodes=(selected,),
        edges=(),
        execution_stages=((selected.node_id,),),
        entry_nodes=(selected.node_id,),
        final_nodes=(selected.node_id,),
        required_work=("w1",),
        estimated_quality=0.9,
        quality_floor=0.8,
        estimated_total_cost=1.0,
        metadata={},
    )


class SoftResourceGovernanceTests(unittest.TestCase):
    def test_constitution_and_machine_policy_require_soft_governance(self):
        constitution = (ROOT / "CONSTITUTION.md").read_text(encoding="utf-8")
        policy = json.loads(
            (ROOT / "open-model-market" / "constitutional_policy.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("Token 与费用实行软治理", constitution)
        self.assertFalse(
            policy["resource_governance"]["local_token_ceiling_allowed"]
        )
        self.assertFalse(
            policy["resource_governance"]["estimated_cost_can_reject_or_stop"]
        )

    def test_payload_removes_all_local_token_caps_and_adds_prompt_policy(self):
        payload = {
            "max_tokens": 4096,
            "max_completion_tokens": 8192,
            "reasoning": {"max_tokens": 2048, "exclude": True},
            "messages": [{"role": "system", "content": "base"}],
        }
        softened = _without_local_token_caps(payload, node())
        self.assertNotIn("max_tokens", softened)
        self.assertNotIn("max_completion_tokens", softened)
        self.assertNotIn("max_tokens", softened["reasoning"])
        self.assertEqual(softened["reasoning"]["effort"], "high")
        self.assertIn(
            SOFT_RESOURCE_INSTRUCTION,
            softened["messages"][0]["content"],
        )

    def test_cost_threshold_is_advisory_and_never_denies_or_invalidates(self):
        config = RuntimeConfig(
            total_call_limit=2,
            recovery_call_limit=1,
            cost_anomaly_usd=0.01,
        )
        budget = SoftResourceBudgetController(config, graph())
        allowed, reason = budget.reserve("initial", 1.0, "n1")
        self.assertTrue(allowed)
        self.assertEqual(reason, "")
        self.assertFalse(budget.reconcile(1.0))
        snapshot = budget.snapshot()
        self.assertFalse(snapshot["cost_limit_enforced"])
        self.assertTrue(snapshot["cost_advisory_exceeded"])
        self.assertEqual(snapshot["denials"], [])

    def test_preflight_does_not_reject_high_estimated_cost(self):
        runtime = build_runtime(
            RuntimeConfig(
                total_call_limit=2,
                recovery_call_limit=1,
                cost_anomaly_usd=0.01,
            ),
            retry_policy=RetryPolicy(
                retry_same_endpoint_categories=(),
                maximum_same_endpoint_retries_per_node=0,
            ),
        )
        preflight = runtime.execution_engine._preflight(graph())
        self.assertEqual(preflight["status"], "pass")
        self.assertNotIn(
            "preflight-risk-adjusted-cost-above-anomaly-limit",
            preflight["blockers"],
        )
        self.assertFalse(preflight["cost_limit_enforced"])
        self.assertFalse(preflight["token_limit_enforced_by_runtime"])


if __name__ == "__main__":
    unittest.main()
