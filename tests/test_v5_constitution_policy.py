from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"


class ConstitutionPolicyTests(unittest.TestCase):
    """Assert the active fully dynamic no-tools policy."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = json.loads(
            (MARKET / "constitutional_policy.json").read_text(encoding="utf-8")
        )

    def test_v12_parameter_design_closure_policy_is_active(self) -> None:
        self.assertEqual(
            self.policy["schema_version"],
            "v5-constitutional-policy-12-parameter-design-closure",
        )
        self.assertEqual(self.policy["authority"], "CONSTITUTION.md")
        self.assertEqual(self.policy["only_hard_model_boundary"], "no-tools")
        matching = self.policy["dynamic_task_matching"]
        self.assertTrue(matching["runtime_parameter_lifecycle_required"])
        self.assertTrue(matching["runtime_knob_coverage_required"])
        self.assertTrue(matching["parameter_design_required"])
        self.assertTrue(matching["parameter_design_before_resolution_required"])
        self.assertFalse(matching["computed_but_unused_allowed"])
        self.assertFalse(matching["semantic_relatedness_can_create_hard_dependency"])
        self.assertTrue(
            matching["constitutional_invariants_must_not_be_disguised_as_dynamic"]
        )
        self.assertEqual(
            matching["parameter_lifecycle"],
            [
                "task-model",
                "discover-decisions",
                "design-parameters",
                "instantiate-parameters",
                "resolve",
                "bind",
                "consume",
                "observe",
                "recompute-from-current-run-feedback",
            ],
        )
        self.assertEqual(
            matching["parameter_design_dimensions"],
            [
                "value_type",
                "domain",
                "resolver",
                "dependencies",
                "consumer_binding",
                "recompute_trigger",
            ],
        )
        self.assertEqual(
            set(matching["parameter_design_allowed_classes"]),
            {
                "constitutional_invariant",
                "infrastructure_invariant",
                "current_task_derived",
                "current_run_feedback_derived",
            },
        )

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

    def test_governance_source_is_full_reasoning_popularity_sequence(self) -> None:
        pool = self.policy["candidate_pool"]
        self.assertEqual(pool["authority"], "decision-system-governance")
        self.assertEqual(
            pool["source"], "openrouter-live-reasoning-most-popular-catalog"
        )
        self.assertEqual(pool["source_definition"]["sort"], "most-popular")
        self.assertEqual(
            pool["source_definition"]["supported_parameters"], "reasoning"
        )
        self.assertEqual(pool["source_definition"]["output_modalities"], "text")
        self.assertIsNone(pool["fixed_pool_size"])
        self.assertFalse(pool["fixed_top_n_allowed"])
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
        self.assertTrue(pool["tool_use_forbidden"])
        self.assertFalse(pool["tools_allowed"])

    def test_all_calculable_team_and_execution_parameters_are_dynamic(self) -> None:
        matching = self.policy["dynamic_task_matching"]
        self.assertTrue(matching["required"])
        self.assertTrue(matching["all_calculable_planning_parameters_dynamic"])
        self.assertTrue(matching["current_task_only"])
        self.assertFalse(matching["cross_task_history_allowed"])
        for key in (
            "task_volume",
            "evidence_volume",
            "constraint_pressure",
            "delivery_pressure",
            "prompt_token_estimate",
            "completion_token_estimate",
            "protocol_reserve",
            "dependency_fan_in",
            "team_size",
            "recovery_size",
            "roles",
            "role_topology",
            "model_assignment",
            "role_weights",
            "solver_time",
        ):
            self.assertEqual(matching[key], "task-derived", key)
        self.assertEqual(matching["solver_seed"], "task-derived-reproducible")
        self.assertEqual(matching["reasoning_effort"], "task-derived-and-request-bound")
        self.assertEqual(matching["output_transport_allowance"], "current-request-derived")
        self.assertEqual(
            matching["model_timeout_effective"],
            "current-request-derived-under-finite-safety-cap",
        )
        self.assertEqual(matching["company_mix"], "unconstrained")
        self.assertFalse(matching["fixed_team_size_allowed"])
        self.assertFalse(matching["fixed_four_plus_four_allowed"])
        self.assertFalse(matching["keyword_routing_required"])
        self.assertFalse(matching["domain_hardcoding_required"])
        self.assertTrue(matching["model_substitution_allowed"])

    def test_optimizer_has_no_business_eligibility_constraints(self) -> None:
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
            "capacity_constraint",
            "free_route_penalty_required",
            "company_overlap_penalty_required",
        ):
            self.assertFalse(optimizer[key], key)
        self.assertTrue(optimizer["capacity_metadata_is_advisory"])
        self.assertTrue(optimizer["cost_metadata_is_advisory"])

    def test_provider_routing_is_unrestricted_but_model_recovery_stays_expert_owned(self) -> None:
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
        self.assertFalse(routing["provider_may_change_model_identity"])
        self.assertEqual(
            routing["model_substitution_authority"],
            "expert-assessment-center-dynamic-recovery",
        )

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
            "fixed_model_timeout_used_as_effective_request_timeout",
        ):
            self.assertFalse(resources[key], key)
        self.assertTrue(resources["dynamic_transport_allowance_allowed"])
        self.assertFalse(resources["dynamic_transport_allowance_is_task_admission_gate"])
        self.assertFalse(resources["dynamic_transport_allowance_can_invalidate_valid_output"])
        self.assertTrue(resources["truncation_can_recompute_transport_allowance"])
        self.assertTrue(resources["dynamic_model_timeout_required"])
        self.assertTrue(resources["model_timeout_safety_cap_required"])
        self.assertFalse(resources["model_timeout_safety_cap_is_business_gate"])
        self.assertTrue(resources["timeout_feedback_can_recompute_effective_timeout"])
        self.assertTrue(resources["team_and_recovery_counts_come_from_current_execution_graph"])
        self.assertTrue(resources["finite_execution_graph_required"])
        self.assertFalse(resources["infinite_model_loop_allowed"])
        self.assertFalse(resources["unbounded_recursive_retry_allowed"])

    def test_quality_gate_uses_observable_contract_not_hidden_fixed_heuristics(self) -> None:
        quality = self.policy["quality_governance"]
        self.assertEqual(
            quality["gate_source"],
            "observable-current-contract-and-evidence-signals",
        )
        self.assertFalse(quality["fixed_answer_length_gate_used"])
        self.assertFalse(quality["fixed_business_quality_weight_coefficients_used"])
        self.assertFalse(quality["fixed_numeric_quality_threshold_gate_used"])
        self.assertEqual(quality["quality_score_role"], "telemetry-only")
        self.assertTrue(quality["final_evidence_validation_fail_closed"])

    def test_runtime_dependency_allowlist_matches_constitution(self) -> None:
        dependencies = self.policy["runtime_dependencies"]
        self.assertTrue(dependencies["must_match_constitution"])
        self.assertEqual(
            set(dependencies["allowed"]),
            {"jsonschema", "networkx", "ortools", "optuna"},
        )
        self.assertFalse(
            dependencies["additional_runtime_dependencies_allowed_without_constitution_change"]
        )
        self.assertFalse(dependencies["heavy_agent_orchestration_framework_allowed"])

    def test_expert_tools_remain_hard_forbidden(self) -> None:
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
        self.assertTrue(tools["request_tool_fields_forbidden"])
        self.assertTrue(tools["response_tool_evidence_forbidden"])

    def test_integrity_checks_are_not_misclassified_as_model_business_gates(self) -> None:
        integrity = self.policy["security_and_integrity_invariants"]
        self.assertFalse(integrity["these_are_model_business_gates"])
        self.assertTrue(integrity["authentication_required"])
        self.assertTrue(integrity["secret_protection_required"])
        self.assertTrue(integrity["repository_isolation_preserved"])
        self.assertTrue(integrity["task_and_plan_hash_integrity_required"])
        self.assertTrue(integrity["candidate_transport_integrity_required"])
        self.assertTrue(integrity["finite_acyclic_dag_required"])
        self.assertTrue(integrity["semantic_relatedness_is_not_dependency"])
        self.assertTrue(integrity["finite_model_timeout_safety_cap_required"])
        self.assertFalse(integrity["arbitrary_network_egress_allowed"])
        self.assertEqual(integrity["model_plane_hosts"], ["openrouter.ai"])
        self.assertEqual(integrity["control_plane_hosts"], ["api.github.com"])

    def test_production_model_policy_requires_parameter_design_runtime_coverage_and_no_tools(self) -> None:
        promotion = self.policy["production_promotion"]
        self.assertFalse(promotion["zero_cost_ci_required"])
        self.assertFalse(promotion["zero_cost_free_canary_required"])
        self.assertFalse(promotion["explicit_paid_acceptance_required"])
        self.assertFalse(promotion["signed_weekly_top50_pool_required"])
        self.assertFalse(promotion["ortools_optimality_proof_required"])
        self.assertFalse(promotion["task_adaptive_value_scoring_required"])
        self.assertTrue(promotion["parameter_design_coverage_required"])
        self.assertTrue(promotion["runtime_knob_coverage_required"])
        self.assertTrue(promotion["unrestricted_provider_routing_required"])
        self.assertTrue(promotion["no_tools_required"])
        self.assertFalse(promotion["automatic_merge_allowed"])
        self.assertFalse(promotion["automatic_production_ref_move_allowed"])


if __name__ == "__main__":
    unittest.main()
