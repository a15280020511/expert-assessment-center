from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
if str(MARKET) not in sys.path:
    sys.path.insert(0, str(MARKET))

from v5_cost_effectiveness_role_assignment import solve_runtime_roles  # noqa: E402


def _metrics(candidates, capability_scores, economy_scores=None):
    economy_scores = economy_scores or {
        str(row["model"]): 1 for row in candidates
    }
    result = {}
    for row in candidates:
        model = str(row["model"])
        capability = int(capability_scores[model])
        economy = int(economy_scores[model])
        result[model] = {
            "objective_score": capability + economy,
            "base_objective_score": capability + economy,
            "estimated_task_cost_usd": float(economy),
            "compatible": True,
            "capacity_shortfall": 0.0,
            "capacity_shortfall_penalty": 0,
            "marginal_cost_per_quality": float(economy),
            "weights": {
                "intelligence": 1,
                "weekly_popularity": 0,
                "capacity_headroom": 0,
                "task_cost": 1,
                "marginal_return": 0,
            },
            "weight_strengths": {},
            "role_tokens": {},
            "ranks": {
                "intelligence": capability,
                "weekly_popularity": 1,
                "capacity_headroom": 1,
                "task_cost": economy,
                "marginal_return": 1,
            },
        }
    return result


class InitialCompanyHeterogeneityTests(unittest.TestCase):
    def _solve(
        self,
        candidates,
        capability_scores,
        *,
        economy_scores=None,
        roles=2,
        recovery=0,
    ):
        role_plan = [
            {
                "role_id": f"role-{index}",
                "role_kind": "analysis",
                "role": f"role {index}",
            }
            for index in range(roles)
        ]
        metrics = _metrics(candidates, capability_scores, economy_scores)
        with (
            patch(
                "v5_cost_effectiveness_role_assignment.build_runtime_role_metrics",
                return_value=metrics,
            ),
            patch(
                "v5_cost_effectiveness_role_assignment.build_runtime_recovery_metrics",
                return_value=(metrics, {"role_id": "recovery-reference"}),
            ),
        ):
            return solve_runtime_roles(candidates, {}, role_plan, recovery)

    def test_equal_capability_and_economy_prefer_distinct_companies(self) -> None:
        candidates = [
            {"model": "aion-labs/model-a"},
            {"model": "aion-labs/model-b"},
            {"model": "anthropic/model-c"},
        ]
        capability = {row["model"]: 1 for row in candidates}
        selected, backups, audit = self._solve(candidates, capability)
        self.assertEqual([], backups)
        companies = {row["model"].split("/", 1)[0] for row in selected}
        self.assertEqual({"aion-labs", "anthropic"}, companies)
        self.assertEqual(2, audit["primary_distinct_company_count"])
        self.assertEqual(1.0, audit["company_heterogeneity_ratio"])
        self.assertFalse(audit["company_diversity_is_execution_gate"])

    def test_better_capability_and_reliability_beats_everything_else(self) -> None:
        candidates = [
            {"model": "aion-labs/model-a"},
            {"model": "aion-labs/model-b"},
            {"model": "anthropic/model-c"},
        ]
        capability = {
            "aion-labs/model-a": 1,
            "aion-labs/model-b": 1,
            "anthropic/model-c": 2,
        }
        selected, _, audit = self._solve(candidates, capability)
        companies = sorted(row["model"].split("/", 1)[0] for row in selected)
        self.assertEqual(["aion-labs", "aion-labs"], companies)
        self.assertEqual(
            "current-task-capability-and-capacity-risk",
            audit["objective_priority"][0],
        )

    def test_cheaper_same_company_beats_expensive_diversity_when_capability_ties(self) -> None:
        candidates = [
            {"model": "aion-labs/model-a"},
            {"model": "aion-labs/model-b"},
            {"model": "anthropic/model-c"},
        ]
        capability = {row["model"]: 1 for row in candidates}
        economy = {
            "aion-labs/model-a": 1,
            "aion-labs/model-b": 1,
            "anthropic/model-c": 20,
        }
        selected, _, audit = self._solve(
            candidates,
            capability,
            economy_scores=economy,
        )
        companies = {row["model"].split("/", 1)[0] for row in selected}
        self.assertEqual({"aion-labs"}, companies)
        self.assertEqual(
            [
                "current-task-capability-and-capacity-risk",
                "current-task-cost-and-marginal-return",
                "maximize-distinct-company-coverage-on-higher-priority-tie",
                "stable-deterministic-tie-break",
            ],
            audit["objective_priority"],
        )
        self.assertTrue(audit["cost_effectiveness_priority"])
        self.assertFalse(audit["cost_is_business_gate"])

    def test_recovery_slot_uses_diversity_only_on_cost_tie(self) -> None:
        candidates = [
            {"model": "openai/model-a"},
            {"model": "openai/model-b"},
            {"model": "deepseek/model-c"},
        ]
        capability = {row["model"]: 1 for row in candidates}
        selected, backups, audit = self._solve(
            candidates,
            capability,
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
