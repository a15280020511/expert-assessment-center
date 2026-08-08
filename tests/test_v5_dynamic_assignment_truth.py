from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
if str(MARKET) not in sys.path:
    sys.path.insert(0, str(MARKET))

from v5_dynamic_assignment_truth import (  # noqa: E402
    assignment_fields,
    expert_dynamic_assignment_active,
)


class DynamicAssignmentTruthTests(unittest.TestCase):
    def test_current_full_pool_plan_is_expert_assigned_even_when_top50_false(self) -> None:
        plan = {
            "expert_candidate_pool_top50_only": False,
            "selected_from_top50_reasoning_pool_only": False,
            "selection_performed_by_governance": False,
            "expert_center_pool_selection_allowed": True,
            "expert_center_reranking_allowed": True,
            "task_adaptive_assignment_required": True,
            "model_assignment_authority": "expert-assessment-center-dynamic-ortools",
            "model_substitution_allowed": True,
        }
        self.assertTrue(expert_dynamic_assignment_active(plan))
        fields = assignment_fields(plan)
        self.assertTrue(fields["model_selection_performed_locally"])
        self.assertTrue(fields["candidate_pool_reranking_performed_locally"])
        self.assertTrue(fields["model_substitution_allowed"])
        self.assertEqual(
            "expert-assessment-center-current-ticket-generated-parameter-ortools",
            fields["selection_authority"],
        )
        self.assertFalse(fields["fixed_four_plus_four_required"])
        self.assertFalse(fields["legacy_top50_flag_controls_assignment"])

    def test_governance_selected_legacy_plan_remains_governance_authority(self) -> None:
        plan = {
            "selection_performed_by_governance": True,
            "model_assignment_authority": "decision-system-governance",
        }
        self.assertFalse(expert_dynamic_assignment_active(plan))
        fields = assignment_fields(plan)
        self.assertEqual("decision-system-governance", fields["selection_authority"])
        self.assertFalse(fields["model_selection_performed_locally"])
        self.assertFalse(fields["model_substitution_allowed"])


if __name__ == "__main__":
    unittest.main()
