import copy
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from v5_governance_model_plan import (  # noqa: E402
    GovernanceModelPlanError,
    plan_sha256,
    task_sha256,
    validate_governance_model_plan,
)


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "governance-ticket.json"


def load_ticket() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def resign(ticket: dict) -> None:
    plan = ticket["governance_model_plan"]
    plan["task_sha256"] = task_sha256(ticket)
    plan["plan_sha256"] = plan_sha256(plan)


class GovernanceModelPlanTests(unittest.TestCase):
    def test_valid_fixture_passes(self) -> None:
        ticket = load_ticket()
        plan = validate_governance_model_plan(ticket)
        self.assertEqual(plan["selection_authority"], "decision-system-governance")
        self.assertFalse(plan["model_substitution_allowed"])
        self.assertFalse(plan["expert_center_reranking_allowed"])

    def test_missing_plan_fails_closed(self) -> None:
        ticket = load_ticket()
        ticket.pop("governance_model_plan")
        with self.assertRaisesRegex(
            GovernanceModelPlanError, "local selection is disabled"
        ):
            validate_governance_model_plan(ticket)

    def test_task_tampering_is_detected(self) -> None:
        ticket = load_ticket()
        ticket["task"]["question"] += " 篡改"
        with self.assertRaisesRegex(GovernanceModelPlanError, "task hash mismatch"):
            validate_governance_model_plan(ticket)

    def test_plan_tampering_is_detected(self) -> None:
        ticket = load_ticket()
        ticket["governance_model_plan"]["selected_models"][0]["model"] = (
            "other/model"
        )
        with self.assertRaisesRegex(GovernanceModelPlanError, "digest mismatch"):
            validate_governance_model_plan(ticket)

    def test_duplicate_company_is_rejected_after_valid_resign(self) -> None:
        ticket = load_ticket()
        selected = ticket["governance_model_plan"]["selected_models"]
        selected[1]["company"] = selected[0]["company"]
        resign(ticket)
        with self.assertRaisesRegex(GovernanceModelPlanError, "duplicate or reused"):
            validate_governance_model_plan(ticket)

    def test_role_order_is_rejected_after_valid_resign(self) -> None:
        ticket = load_ticket()
        selected = ticket["governance_model_plan"]["selected_models"]
        selected[1], selected[2] = selected[2], selected[1]
        for index, row in enumerate(selected, 1):
            row["slot"] = index
        resign(ticket)
        with self.assertRaisesRegex(GovernanceModelPlanError, "final two"):
            validate_governance_model_plan(ticket)

    def test_recovery_count_must_equal_ticket_reserve(self) -> None:
        ticket = load_ticket()
        ticket["approved_budget"]["maximum_recovery_calls"] = 2
        resign(ticket)
        with self.assertRaisesRegex(GovernanceModelPlanError, "recovery model count"):
            validate_governance_model_plan(ticket)

    def test_boolean_and_nonfinite_costs_are_rejected(self) -> None:
        for value in (True, float("nan"), float("inf"), -1):
            with self.subTest(value=value):
                ticket = load_ticket()
                ticket["governance_model_plan"]["selected_models"][0][
                    "estimated_task_cost_usd"
                ] = value
                if isinstance(value, float) and not math.isfinite(value):
                    with self.assertRaisesRegex(
                        GovernanceModelPlanError, "non-canonical JSON value"
                    ):
                        validate_governance_model_plan(ticket)
                    continue
                resign(ticket)
                with self.assertRaises(GovernanceModelPlanError):
                    validate_governance_model_plan(ticket)

    def test_explicit_plan_argument_must_match_ticket_task(self) -> None:
        ticket = load_ticket()
        other = copy.deepcopy(ticket["governance_model_plan"])
        ticket["task"]["language"] = "en"
        with self.assertRaisesRegex(GovernanceModelPlanError, "task hash mismatch"):
            validate_governance_model_plan(ticket, other)


if __name__ == "__main__":
    unittest.main()
