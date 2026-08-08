import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import model_market  # noqa: E402
import v5_pipeline  # noqa: E402
from execution_graph import ExecutionGraph, SelectedNode  # noqa: E402
from v5_gpt_expert_selector import _schema, parse_proposal  # noqa: E402
from v5_runtime import RetryPolicy, RuntimeConfig  # noqa: E402
from v5_soft_resource_governance import (  # noqa: E402
    SOFT_RESOURCE_INSTRUCTION,
    SoftResourceBudgetController,
    _without_local_token_caps,
    build_runtime,
)
from v5_task_envelope import build_task_envelope  # noqa: E402


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
        self.assertIn("Token、费用、余额与调用数实行软治理", constitution)
        resource = policy["resource_governance"]
        self.assertFalse(resource["local_token_ceiling_allowed"])
        self.assertFalse(resource["estimated_cost_can_reject_execution"])
        self.assertFalse(resource["cost_threshold_can_reject_execution"])
        self.assertFalse(resource["actual_cost_can_invalidate_valid_output"])
        self.assertTrue(resource["dynamic_transport_allowance_allowed"])
        self.assertFalse(resource["dynamic_transport_allowance_is_task_admission_gate"])
        self.assertFalse(resource["dynamic_transport_allowance_can_invalidate_valid_output"])
        self.assertTrue(resource["finite_execution_graph_required"])
        self.assertFalse(resource["unbounded_recursive_retry_allowed"])

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
        self.assertIn(SOFT_RESOURCE_INSTRUCTION, softened["messages"][0]["content"])

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

    def test_pipeline_does_not_consume_or_exhaust_cost_advisory(self):
        governance_cost, advisory = v5_pipeline._remaining_cost(
            0.01,
            {"actual_cost_usd": 3.5},
        )
        self.assertEqual(governance_cost, 3.5)
        self.assertEqual(advisory, 0.01)

    def test_pipeline_reports_cost_advisory_without_invalidating_result(self):
        result = {
            "actual_cost_usd": 3.0,
            "execution_budget": {"calls_reserved": 1},
        }
        args = SimpleNamespace(cost_anomaly_usd=0.01)
        governance_models = {
            "gpt": {"resolved_model": "openai/gpt", "provider": "openai"},
            "claude": {
                "resolved_model": "anthropic/claude",
                "provider": "anthropic",
            },
        }
        v5_pipeline._finalize_result(
            result,
            args=args,
            total_calls=4,
            governance_models=governance_models,
            governance_ledger={"actual_governance_calls": 3},
            governance_cost=2.0,
        )
        self.assertEqual(result["actual_cost_usd"], 5.0)
        self.assertTrue(result["resource_governance"]["cost_advisory_exceeded"])
        self.assertFalse(
            result["resource_governance"]["cost_threshold_can_invalidate_result"]
        )

    def test_large_completion_advisory_is_not_rejected_by_config(self):
        run = model_market.build_run_config(
            SimpleNamespace(
                task="closed-world task",
                max_completion_tokens=1_000_000,
                maximum_recovery_calls=0,
                catalog_file=None,
                output_dir="v5-artifacts",
                dry_run=True,
                require_live_catalog=False,
                maximum_total_calls=4,
            )
        )
        self.assertEqual(run.max_completion_tokens, 1_000_000)

    def test_completion_advisory_does_not_change_required_context(self):
        low = build_task_envelope(
            "closed-world task",
            minimum_context_length=16_384,
            maximum_completion_tokens=1_000,
        )
        high = build_task_envelope(
            "closed-world task",
            minimum_context_length=16_384,
            maximum_completion_tokens=1_000_000,
        )
        self.assertEqual(low["required_context_tokens"], high["required_context_tokens"])
        self.assertNotEqual(
            low["completion_capacity_advisory_tokens"],
            high["completion_capacity_advisory_tokens"],
        )
        self.assertFalse(high["completion_advisory_affects_eligibility"])
        self.assertFalse(high["local_token_ceiling_enforced"])

    def test_gpt_plan_schema_and_parser_have_no_local_maximum(self):
        token_schema = (
            _schema("soft_resource_fixture")["json_schema"]["schema"]
            ["properties"]["nodes"]["items"]["properties"]["max_output_tokens"]
        )
        self.assertNotIn("maximum", token_schema)
        proposal = {
            "work_items": [
                {
                    "work_id": "w1",
                    "objective": "complete the task",
                    "dependencies": [],
                    "required_outputs": ["answer"],
                }
            ],
            "nodes": [
                {
                    "node_id": "n1",
                    "work_ids": ["w1"],
                    "role": "expert",
                    "functions": ["synthesis"],
                    "model": "company/model",
                    "provider": "provider",
                    "reasoning_effort": "high",
                    "max_output_tokens": 100_000,
                    "recovery": [],
                }
            ],
            "edges": [],
            "final_nodes": ["n1"],
        }
        parsed = parse_proposal(json.dumps(proposal))
        self.assertEqual(parsed["nodes"][0]["max_output_tokens"], 100_000)


if __name__ == "__main__":
    unittest.main()
