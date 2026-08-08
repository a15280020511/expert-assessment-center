from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"


class ConstitutionPolicyTests(unittest.TestCase):
    """Assert the active dynamic, cost-effective, no-tools policy."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = json.loads(
            (MARKET / "constitutional_policy.json").read_text(encoding="utf-8")
        )

    def test_v13_cost_effectiveness_resource_closure_is_active(self) -> None:
        self.assertEqual(
            self.policy["schema_version"],
            "v5-constitutional-policy-13-cost-effectiveness-resource-closure",
        )
        self.assertEqual(self.policy["authority"], "CONSTITUTION.md")
        self.assertEqual(self.policy["only_hard_model_boundary"], "no-tools")
        matching = self.policy["dynamic_task_matching"]
        self.assertTrue(matching["all_calculable_planning_parameters_dynamic"])
        self.assertTrue(matching["all_request_resource_controls_first_class_parameters"])
        self.assertTrue(matching["runtime_parameter_lifecycle_required"])
        self.assertTrue(matching["runtime_knob_coverage_required"])
        self.assertTrue(matching["parameter_design_required"])
        self.assertTrue(matching["request_resource_parameter_design_required"])
        self.assertTrue(matching["recompute_after_final_prompt_assembly_before_send"])
        self.assertTrue(matching["continuous_spatiotemporal_replanning_required"])
        self.assertTrue(
            matching["continuous_spatiotemporal_resource_recomputation_required"]
        )
        self.assertFalse(matching["computed_but_unused_allowed"])
        self.assertFalse(matching["semantic_relatedness_can_create_hard_dependency"])
        self.assertTrue(
            matching["constitutional_invariants_must_not_be_disguised_as_dynamic"]
        )

    def test_parameter_lifecycle_and_design_classes_remain_explicit(self) -> None:
        matching = self.policy["dynamic_task_matching"]
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
        self.assertIsNone(pool["fixed_pool_size"])
        self.assertFalse(pool["fixed_top_n_allowed"])
        self.assertTrue(pool["expert_center_can_use_any_governance_supplied_candidate"])
        self.assertTrue(pool["expert_center_can_rerank_and_assign"])
        self.assertTrue(pool["tool_use_forbidden"])
        self.assertFalse(pool["tools_allowed"])

    def test_resource_parameters_and_reasoning_are_current_signal_derived(self) -> None:
        matching = self.policy["dynamic_task_matching"]
        self.assertEqual(
            matching["prompt_token_estimate"],
            "task-derived-then-final-payload-remeasured",
        )
        self.assertEqual(
            matching["completion_token_estimate"],
            "task-derived-then-current-request-and-feedback-derived",
        )
        self.assertEqual(
            matching["prompt_shape_budgeting"],
            "current-task-designed-final-payload-bound",
        )
        self.assertEqual(
            matching["resource_efficiency_balance"],
            "current-task-and-current-run-soft-objective",
        )
        self.assertEqual(
            matching["reasoning_effort"],
            "current-task-absolute-pressure-plus-current-role-demand-and-request-bound",
        )
        self.assertFalse(
            matching["single_role_reasoning_effort_unconditional_medium_allowed"]
        )
        self.assertEqual(
            matching["output_transport_allowance"],
            "current-final-payload-plus-current-run-feedback-derived",
        )
        self.assertEqual(
            matching["model_timeout_effective"],
            "current-final-payload-plus-current-run-feedback-derived-under-finite-safety-cap",
        )

    def test_cost_effectiveness_precedes_company_diversity_but_is_soft(self) -> None:
        matching = self.policy["dynamic_task_matching"]
        self.assertEqual(
            matching["company_heterogeneity_priority"],
            [
                "current-task-capability-and-capacity-risk",
                "current-task-cost-and-marginal-return",
                "maximize-distinct-company-coverage-on-higher-priority-tie",
                "stable-deterministic-tie-break",
            ],
        )
        self.assertEqual(
            matching["runtime_recovery_priority"],
            [
                "current-run-quality-or-failure-risk",
                "current-task-expected-cost-and-marginal-return",
                "company-heterogeneity-on-higher-priority-tie",
                "stable-model-identity",
            ],
        )
        optimizer = self.policy["optimizer_runtime"]
        self.assertTrue(optimizer["cost_effectiveness_soft_priority_required"])
        self.assertTrue(optimizer["token_cost_soft_optimization_required"])
        self.assertFalse(optimizer["cheapest_model_is_hard_rule"])
        self.assertEqual(
            optimizer["active_assignment_module"],
            "v5_cost_effectiveness_role_assignment",
        )
        self.assertFalse(optimizer["company_heterogeneity_is_hard_constraint"])

    def test_resource_controls_save_when_possible_without_becoming_gates(self) -> None:
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
            "fixed_reasoning_token_ratio_claim_allowed",
            "fixed_model_timeout_used_as_effective_request_timeout",
        ):
            self.assertFalse(resources[key], key)
        for key in (
            "cost_effectiveness_priority",
            "token_and_cost_are_soft_controls",
            "minimize_unnecessary_tokens_and_cost",
            "task_contract_and_quality_override_resource_savings",
            "prompt_shape_is_first_class_parameter",
            "resource_efficiency_balance_is_first_class_parameter",
            "output_transport_allowance_is_first_class_parameter",
            "effective_timeout_is_first_class_parameter",
            "final_payload_measured_before_effective_binding",
            "prompt_compaction_is_lossless_and_obligation_preserving",
            "truncation_same_model_rebind_before_cross_model_substitution",
            "dynamic_model_timeout_required",
            "model_timeout_safety_cap_required",
            "recovery_candidate_space_recomputed_each_iteration",
            "finite_execution_graph_required",
        ):
            self.assertTrue(resources[key], key)
        self.assertFalse(resources["model_timeout_safety_cap_is_business_gate"])
        self.assertFalse(resources["infinite_model_loop_allowed"])
        self.assertFalse(resources["unbounded_recursive_retry_allowed"])

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

    def test_provider_routing_is_unrestricted(self) -> None:
        routing = self.policy["provider_routing"]
        self.assertEqual(routing["mode"], "unrestricted-openrouter")
        self.assertFalse(routing["provider_allowlist_allowed"])
        self.assertFalse(routing["provider_order_allowed"])
        self.assertFalse(routing["provider_price_filter_allowed"])
        self.assertFalse(routing["exact_provider_lock_required"])
        self.assertTrue(routing["openrouter_selects_provider"])
        self.assertTrue(routing["provider_fallback_allowed"])
        self.assertFalse(routing["provider_may_change_model_identity"])

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
        self.assertEqual(
            set(dependencies["allowed"]),
            {"jsonschema", "networkx", "ortools", "optuna"},
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

    def test_integrity_invariants_remain_fixed(self) -> None:
        integrity = self.policy["security_and_integrity_invariants"]
        self.assertFalse(integrity["these_are_model_business_gates"])
        self.assertTrue(integrity["task_and_plan_hash_integrity_required"])
        self.assertTrue(integrity["candidate_transport_integrity_required"])
        self.assertTrue(integrity["finite_acyclic_dag_required"])
        self.assertTrue(integrity["finite_model_timeout_safety_cap_required"])
        self.assertTrue(integrity["continuous_replanning_must_remain_inside_finite_graph_boundary"])
        self.assertFalse(integrity["arbitrary_network_egress_allowed"])

    def test_production_promotion_audits_new_closure(self) -> None:
        promotion = self.policy["production_promotion"]
        self.assertTrue(promotion["parameter_design_coverage_required"])
        self.assertTrue(promotion["request_resource_parameter_design_required"])
        self.assertTrue(promotion["runtime_knob_coverage_required"])
        self.assertTrue(promotion["cost_effectiveness_soft_control_required"])
        self.assertTrue(promotion["continuous_spatiotemporal_replanning_required"])
        self.assertTrue(promotion["unrestricted_provider_routing_required"])
        self.assertTrue(promotion["no_tools_required"])
        self.assertFalse(promotion["automatic_merge_allowed"])


if __name__ == "__main__":
    unittest.main()
