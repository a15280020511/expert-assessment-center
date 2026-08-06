from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))
wrapper = importlib.import_module("v5_price_ranked_issue_ticket")


OLD_PLAN = "a" * 64
NEW_PLAN = "b" * 64


def accepted(retry_id: str, plan_sha256: str) -> str:
    return (
        "## EXECUTION_RETRY_ACCEPTED\n\n"
        f"- RETRY_ID: `{retry_id}`\n"
        f"- 模型计划SHA256：`{plan_sha256}`\n"
    )


class RetryBudgetPerGovernancePlanTests(unittest.TestCase):
    def tearDown(self) -> None:
        wrapper._CURRENT_MODEL_PLAN_SHA256 = ""

    def test_new_governance_plan_receives_fresh_bounded_retry_budget(self) -> None:
        state = wrapper._execution_state_by_plan(
            [
                accepted("old-1", OLD_PLAN),
                "## EXECUTION_FAILED\n",
                accepted("old-2", OLD_PLAN),
                "## EXECUTION_FAILED\n",
            ]
        )
        wrapper._CURRENT_MODEL_PLAN_SHA256 = NEW_PLAN
        reason = wrapper._current_issue_submission_reason_by_plan(
            state,
            is_retry=True,
            retry_id="new-plan-1",
        )
        self.assertEqual(reason, "")

    def test_same_governance_plan_remains_limited_to_two_retries(self) -> None:
        state = wrapper._execution_state_by_plan(
            [
                accepted("same-1", NEW_PLAN),
                "## EXECUTION_FAILED\n",
                accepted("same-2", NEW_PLAN),
                "## EXECUTION_FAILED\n",
            ]
        )
        wrapper._CURRENT_MODEL_PLAN_SHA256 = NEW_PLAN
        reason = wrapper._current_issue_submission_reason_by_plan(
            state,
            is_retry=True,
            retry_id="same-3",
        )
        self.assertIn("maximum 2 controlled retries", reason)

    def test_retry_ids_remain_unique_across_plan_versions(self) -> None:
        state = wrapper._execution_state_by_plan(
            [accepted("shared-id", OLD_PLAN), "## EXECUTION_FAILED\n"]
        )
        wrapper._CURRENT_MODEL_PLAN_SHA256 = NEW_PLAN
        reason = wrapper._current_issue_submission_reason_by_plan(
            state,
            is_retry=True,
            retry_id="shared-id",
        )
        self.assertIn("was already used", reason)

    def test_missing_current_plan_digest_keeps_legacy_issue_limit(self) -> None:
        state = wrapper._execution_state_by_plan(
            [
                accepted("old-1", OLD_PLAN),
                "## EXECUTION_FAILED\n",
                accepted("new-1", NEW_PLAN),
                "## EXECUTION_FAILED\n",
            ]
        )
        reason = wrapper._current_issue_submission_reason_by_plan(
            state,
            is_retry=True,
            retry_id="unknown-plan",
        )
        self.assertIn("maximum 2 controlled retries", reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
