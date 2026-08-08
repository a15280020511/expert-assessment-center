from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads(
    (ROOT / "open-model-market" / "constitutional_policy.json").read_text(
        encoding="utf-8"
    )
)


class Article15MachinePolicyTests(unittest.TestCase):
    def test_company_heterogeneity_is_soft_and_ordered_after_capability(self) -> None:
        matching = POLICY["dynamic_task_matching"]
        self.assertTrue(matching["company_heterogeneity_soft_objective_required"])
        self.assertFalse(matching["company_diversity_is_execution_gate"])
        self.assertFalse(matching["fixed_company_count_allowed"])
        self.assertEqual(
            [
                "current-task-capability-and-capacity-risk",
                "maximize-distinct-company-coverage",
                "current-task-cost-and-marginal-return",
                "stable-deterministic-tie-break",
            ],
            matching["company_heterogeneity_priority"],
        )
        self.assertTrue(matching["standby_claim_preserves_reranked_priority"])
        self.assertFalse(
            matching["company_diversity_overrides_higher_priority_candidate"]
        )

    def test_active_optimizer_is_production_runtime_role_assignment(self) -> None:
        optimizer = POLICY["optimizer_runtime"]
        self.assertEqual(
            "v5_runtime_role_assignment",
            optimizer["active_assignment_module"],
        )
        self.assertTrue(optimizer["company_heterogeneity_soft_objective"])
        self.assertFalse(optimizer["company_heterogeneity_is_hard_constraint"])
        self.assertFalse(optimizer["company_uniqueness_constraint"])
        self.assertEqual(
            [
                "capability-and-capacity-risk",
                "distinct-company-coverage",
                "cost-and-marginal-return",
                "stable-tie-break",
            ],
            optimizer["company_heterogeneity_lexicographic_order"],
        )

    def test_run387_quality_truthfulness_is_machine_policy(self) -> None:
        quality = POLICY["quality_governance"]
        self.assertTrue(quality["task_explicit_semantic_obligations_required"])
        self.assertEqual(
            "final-delivery-only",
            quality["task_explicit_semantic_obligation_scope"],
        )
        self.assertFalse(
            quality["heading_presence_alone_satisfies_semantic_obligation"]
        )
        self.assertTrue(quality["explicit_arithmetic_consistency_required"])
        self.assertTrue(quality["explicit_linear_threshold_consistency_required"])
        self.assertFalse(quality["hidden_chain_of_thought_required_for_math_validation"])
        self.assertTrue(
            quality["derived_quantities_from_authoritative_input_are_allowed"]
        )
        self.assertTrue(
            quality["derived_quantities_from_authoritative_input_must_be_preserved"]
        )
        self.assertTrue(quality["unsupported_external_quantities_fail_closed"])

    def test_truncation_repairs_same_model_before_cross_model_substitution(self) -> None:
        resources = POLICY["resource_governance"]
        self.assertTrue(resources["truncation_can_recompute_transport_allowance"])
        self.assertTrue(
            resources["truncation_same_model_rebind_before_cross_model_substitution"]
        )


if __name__ == "__main__":
    unittest.main()
