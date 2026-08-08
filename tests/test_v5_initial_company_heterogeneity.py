from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
if str(MARKET) not in sys.path:
    sys.path.insert(0, str(MARKET))

from v5_dynamic_role_assignment import solve_dynamic_roles  # noqa: E402


def _metrics(candidates, scores):
    return {
        str(row["model"]): {
            "objective_score": int(scores[str(row["model"])]),
            "estimated_task_cost_usd": 0.01,
            "compatible": True,
        }
        for row in candidates
    }


class InitialCompanyHeterogeneityTests(unittest.TestCase):
    def _solve(self, candidates, scores, *, roles=2, recovery=0):
        role_plan = [
            {
                "role_id": f"role-{index}",
                "role_kind": "analysis",
                "role": f"role {index}",
            }
            for index in range(roles)
        ]
        metrics = _metrics(candidates, scores)
        with (
            patch(
                "v5_dynamic_role_assignment.build_dynamic_role_metrics",
                return_value=metrics,
            ),
            patch(
                "v5_dynamic_role_assignment.build_dynamic_recovery_metrics",
                return_value=(metrics, {"role_id": "recovery-reference"}),
            ),
        ):
            return solve_dynamic_roles(candidates, {}, role_plan, recovery)

    def test_equal_task_scores_prefer_distinct_companies(self) -> None:
        candidates = [
            {"model": "aion-labs/model-a"},
            {"model": "aion-labs/model-b"},
            {"model": "anthropic/model-c"},
        ]
        scores = {row["model"]: 0 for row in candidates}
        selected, backups, audit = self._solve(candidates, scores)
        self.assertEqual([], backups)
        companies = {row["model"].split("/", 1)[0] for row in selected}
        self.assertEqual({"aion-labs", "anthropic"}, companies)
        self.assertEqual(2, audit["primary_distinct_company_count"])
        self.assertEqual(1.0, audit["company_heterogeneity_ratio"])
        self.assertFalse(audit["company_diversity_is_execution_gate"])

    def test_better_current_task_objective_beats_diversity(self) -> None:
        candidates = [
            {"model": "aion-labs/model-a"},
            {"model": "aion-labs/model-b"},
            {"model": "anthropic/model-c"},
        ]
        scores = {
            "aion-labs/model-a": 0,
            "aion-labs/model-b": 0,
            "anthropic/model-c": 1,
        }
        selected, _, audit = self._solve(candidates, scores)
        companies = [row["model"].split("/", 1)[0] for row in selected]
        self.assertEqual(["aion-labs", "aion-labs"], sorted(companies))
        self.assertEqual(1, audit["primary_distinct_company_count"])
        self.assertEqual(
            "current-task-role-quality-risk-objective",
            audit["objective_priority"][0],
        )

    def test_recovery_slot_also_prefers_new_company_on_soft_tie(self) -> None:
        candidates = [
            {"model": "openai/model-a"},
            {"model": "openai/model-b"},
            {"model": "deepseek/model-c"},
        ]
        scores = {row["model"]: 0 for row in candidates}
        selected, backups, audit = self._solve(
            candidates,
            scores,
            roles=1,
            recovery=1,
        )
        sequence = [selected[0]["model"], backups[0]["model"]]
        companies = {model.split("/", 1)[0] for model in sequence}
        self.assertEqual({"openai", "deepseek"}, companies)
        self.assertEqual(2, audit["assigned_distinct_company_count"])
        self.assertEqual(0, audit["same_company_position_reuse_count"])


if __name__ == "__main__":
    unittest.main()
