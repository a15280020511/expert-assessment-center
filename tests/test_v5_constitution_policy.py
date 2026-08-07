from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"


class ConstitutionPolicyTests(unittest.TestCase):
    """Assert the active v9 policy rather than retired v8 admission gates."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = json.loads(
            (MARKET / "constitutional_policy.json").read_text(encoding="utf-8")
        )

    def test_v9_open_dynamic_policy_is_active(self) -> None:
        self.assertEqual(
            self.policy["schema_version"],
            "v5-constitutional-policy-9-open-dynamic-experts",
        )
        self.assertEqual(self.policy["authority"], "CONSTITUTION.md")

    def test_execution_has_no_free_first_or_exact_sha_qualification_gate(self) -> None:
        admission = self.policy["execution_admission"]
        for key in (
            "free_first_required",
            "zero_call_qualification_required",
            "free_canary_required",
            "full_ci_pass_required_before_task_execution",
            "exact_sha_evidence_required",
            "paid_execution_requires_prior_qualification",
        ):
            self.assertFalse(admission[key], key)
        self.assertEqual(
            admission["qualification_and_canary_role"],
            "advisory-telemetry-only",
        )

    def test_governance_supplies_candidates_without_fixed_pool_eligibility(self) -> None:
        pool = self.policy["candidate_pool"]
        self.assertEqual(pool["authority"], "decision-system-governance")
        self.assertIsNone(pool["fixed_pool_size"])
        for key in (
            "top50_only",
            "top20_only",
            "weekly_rank_required",
            "reasoning_rank_required",
            "flagship_filter_required",
            "price_filter_required",
            "company_diversity_required",
            "signed_pool_membership_required",
            "provider_endpoint_qualification_required",
            "zdr_provider_qualification_required",
        ):
            self.assertFalse(pool[key], key)
        self.assertTrue(pool["expert_center_can_use_any_governance_supplied_candidate"])
        self.assertTrue(pool["expert_center_can_rerank_and_assign"])

    def test_team_roles_and_recovery_are_task_derived(self) -> None:
        matching = self.policy["dynamic_task_matching"]
        self.assertTrue(matching["required"])
        self.assertTrue(matching["current_task_only"])
        self.assertFalse(matching["cross_task_history_allowed"])
        self.assertEqual(matching["team_size"], "task-derived")
        self.assertEqual(matching["recovery_size"], "task-derived")
        self.assertEqual(matching["roles"], "task-derived")
        self.assertEqual(matching["role_topology"], "task-derived")
        self.assertEqual(matching["model_assignment"], "task-derived")
        self.assertEqual(matching["company_mix"], "unconstrained")
        self.assertFalse(matching["fixed_team_size_allowed"])
        self.assertFalse(matching["fixed_four_plus_four_allowed"])
        self.assertFalse(matching["keyword_routing_required"])
        self.assertFalse(matching["domain_hardcoding_required"])
        self.assertTrue(matching["model_substitution_allowed"])
        self.assertEqual(
            matching["principles"],
            [
                "concrete_problem_concrete_analysis",
                "dynamic_adaptation",
                "small_effort_large_return",
            ],
        )

    def test_optimizer_is_optional_and_has_nonblocking_fallbacks(self) -> None:
        optimizer = self.policy["optimizer_runtime"]
        self.assertEqual(optimizer["engine"], "ortools-cp-sat")
        self.assertFalse(optimizer["optimizer_required"])
        self.assertFalse(optimizer["optimality_required"])
        self.assertTrue(optimizer["feasible_solution_allowed"])
        self.assertTrue(optimizer["heuristic_fallback_allowed"])
        for key in (
            "company_uniqueness_constraint",
            "top50_membership_constraint",
            "fixed_role_slot_constraint",
            "fixed_recovery_slot_constraint",
            "approved_budget_constraint",
            "provider_constraint",
        ):
            self.assertFalse(optimizer[key], key)
        self.assertTrue(optimizer["capacity_metadata_is_advisory"])
        self.assertTrue(optimizer["cost_metadata_is_advisory"])

    def test_provider_routing_is_unrestricted(self) -> None:
        routing = self.policy["provider_routing"]
        self.assertEqual(routing["mode"], "unrestricted-openrouter")
        self.assertFalse(routing["provider_allowlist_allowed"])
        self.assertFalse(routing["provider_order_allowed"])
        self.assertFalse(routing["provider_ignore_list_allowed"])
        self.assertFalse(routing["provider_price_filter_allowed"])
        self.assertFalse(routing["provider_zdr_filter_required"])
        self.assertFalse(routing["provider_data_collection_filter_required"])
        self.assertFalse(routing["exact_provider_lock_required"])
        self.assertTrue(routing["openrouter_selects_provider"])
        self.assertTrue(routing["provider_fallback_allowed"])

    def test_resource_controls_are_soft_but_execution_must_be_finite(self) -> None:
        resources = self.policy["resource_governance"]
        for key in (
            "fixed_total_call_ceiling",
            "fixed_initial_call_ceiling",
            "fixed_recovery_call_ceiling",
            "fixed_team_size_ceiling",
            "cost_threshold_can_reject_execution",
            "estimated_cost_can_reject_execution",
            "actual_cost_can_invalidate_valid_output",
            "local_token_ceiling_allowed",
        ):
            self.assertFalse(resources[key], key)
        self.assertTrue(resources["team_and_recovery_counts_come_from_current_execution_graph"])
        self.assertTrue(resources["finite_execution_graph_required"])
        self.assertFalse(resources["infinite_model_loop_allowed"])
        self.assertFalse(resources["unbounded_recursive_retry_allowed"])

    def test_expert_tools_and_arbitrary_network_egress_remain_prohibited(self) -> None:
        tools = self.policy["tool_policy"]
        for key in (
            "expert_external_tools_allowed",
            "expert_web_browsing_allowed",
            "expert_plugin_or_mcp_allowed",
            "expert_code_execution_allowed",
            "expert_database_lookup_allowed",
            "expert_external_api_allowed",
        ):
            self.assertFalse(tools[key], key)

        security = self.policy["security_boundaries"]
        self.assertTrue(security["authentication_required"])
        self.assertTrue(security["secret_protection_required"])
        self.assertTrue(security["repository_isolation_preserved"])
        self.assertFalse(security["arbitrary_network_egress_allowed"])
        self.assertEqual(security["model_plane_hosts"], ["openrouter.ai"])
        self.assertEqual(security["control_plane_hosts"], ["api.github.com"])
        self.assertFalse(security["unsafe_infinite_execution_allowed"])

    def test_production_requires_unrestricted_provider_routing_only_as_model_policy_gate(self) -> None:
        promotion = self.policy["production_promotion"]
        self.assertFalse(promotion["zero_cost_ci_required"])
        self.assertFalse(promotion["zero_cost_free_canary_required"])
        self.assertFalse(promotion["explicit_paid_acceptance_required"])
        self.assertFalse(promotion["signed_weekly_top50_pool_required"])
        self.assertFalse(promotion["ortools_optimality_proof_required"])
        self.assertFalse(promotion["task_adaptive_value_scoring_required"])
        self.assertTrue(promotion["unrestricted_provider_routing_required"])
        self.assertFalse(promotion["automatic_merge_allowed"])
        self.assertFalse(promotion["automatic_production_ref_move_allowed"])


if __name__ == "__main__":
    unittest.main()
