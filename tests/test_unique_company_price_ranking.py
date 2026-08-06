from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))
from v5_governance_model_plan import (  # noqa: E402
    GovernanceModelPlanError,
    plan_sha256,
    task_sha256,
    validate_governance_model_plan,
)

FIXTURE = ROOT / "tests" / "fixtures" / "governance-ticket.json"


def ticket() -> dict:
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    plan = value["governance_model_plan"]
    plan["catalog_fetch_mode"] = "live-per-task-no-cross-task-cache"
    plan["company_uniqueness_scope"] = "selected-and-recovery"
    rows = []
    for row in plan["selected_models"] + plan["recovery_models"]:
        rows.append(
            {
                "model": row["model"],
                "company": row["company"],
                "estimated_task_cost_usd": row["estimated_task_cost_usd"],
                "price_rank_usd_per_million": row["estimated_task_cost_usd"],
            }
        )
    rows.sort(key=lambda row: row["price_rank_usd_per_million"])
    for rank, row in enumerate(rows, 1):
        row["price_rank"] = rank
        row["slot"] = rank
    plan["price_ranked_models"] = rows
    plan["task_sha256"] = task_sha256(value)
    plan["plan_sha256"] = plan_sha256(plan)
    return value


class UniqueCompanyPriceRankingTests(unittest.TestCase):
    def test_live_plan_requires_price_ranking(self) -> None:
        value = ticket()
        value["governance_model_plan"].pop("price_ranked_models")
        value["governance_model_plan"]["plan_sha256"] = plan_sha256(
            value["governance_model_plan"]
        )
        with self.assertRaisesRegex(
            GovernanceModelPlanError, "price_ranked_models"
        ):
            validate_governance_model_plan(value)

    def test_price_ranking_must_be_ascending(self) -> None:
        value = ticket()
        rows = value["governance_model_plan"]["price_ranked_models"]
        rows[0], rows[1] = rows[1], rows[0]
        for rank, row in enumerate(rows, 1):
            row["price_rank"] = rank
        value["governance_model_plan"]["plan_sha256"] = plan_sha256(
            value["governance_model_plan"]
        )
        with self.assertRaisesRegex(
            GovernanceModelPlanError, "ascending price order"
        ):
            validate_governance_model_plan(value)


if __name__ == "__main__":
    unittest.main()
