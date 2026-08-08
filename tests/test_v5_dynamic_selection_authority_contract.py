from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
if str(MARKET) not in sys.path:
    sys.path.insert(0, str(MARKET))

from v5_governance_model_plan import (  # noqa: E402
    DYNAMIC_SCHEMA_VERSION,
    GovernanceModelPlanError,
    plan_sha256,
    task_sha256,
    validate_governance_model_plan,
)

FIXTURE = ROOT / "tests" / "fixtures" / "governance-ticket.json"


def _ticket() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _resign(ticket: dict) -> None:
    plan = ticket["governance_model_plan"]
    plan["task_sha256"] = task_sha256(ticket)
    plan["plan_sha256"] = plan_sha256(plan)


def _dynamic_ticket() -> dict:
    ticket = _ticket()
    plan = ticket["governance_model_plan"]
    plan["schema_version"] = DYNAMIC_SCHEMA_VERSION
    plan["candidate_pool_authority"] = "decision-system-governance"
    plan["selection_authority"] = "expert-assessment-center-dynamic-ortools"
    plan["model_assignment_authority"] = (
        "expert-assessment-center-current-ticket-generated-parameter-ortools"
    )
    plan["selection_performed_by_governance"] = False
    plan["expert_center_reranking_allowed"] = True
    plan["model_substitution_allowed"] = True
    plan["provider_routing_mode"] = "unrestricted-openrouter"
    plan["provider_restrictions_applied"] = False
    _resign(ticket)
    return ticket


class DynamicSelectionAuthorityContractTests(unittest.TestCase):
    def test_dynamic_plan_accepts_governance_pool_and_expert_assignment(self) -> None:
        ticket = _dynamic_ticket()
        plan = validate_governance_model_plan(ticket)
        self.assertEqual(plan["candidate_pool_authority"], "decision-system-governance")
        self.assertTrue(plan["selection_authority"].startswith("expert-assessment-center"))
        self.assertTrue(
            plan["model_assignment_authority"].startswith("expert-assessment-center")
        )
        self.assertFalse(plan["selection_performed_by_governance"])

    def test_dynamic_plan_rejects_legacy_governance_selection_authority(self) -> None:
        ticket = _dynamic_ticket()
        ticket["governance_model_plan"]["selection_authority"] = (
            "decision-system-governance"
        )
        _resign(ticket)
        with self.assertRaisesRegex(
            GovernanceModelPlanError,
            "selection_authority must be expert-assessment-center",
        ):
            validate_governance_model_plan(ticket)

    def test_dynamic_plan_requires_governance_candidate_pool_authority(self) -> None:
        ticket = _dynamic_ticket()
        ticket["governance_model_plan"]["candidate_pool_authority"] = (
            "expert-assessment-center"
        )
        _resign(ticket)
        with self.assertRaisesRegex(
            GovernanceModelPlanError,
            "candidate_pool_authority must be decision-system-governance",
        ):
            validate_governance_model_plan(ticket)

    def test_dynamic_plan_cannot_claim_governance_selected_models(self) -> None:
        ticket = _dynamic_ticket()
        ticket["governance_model_plan"]["selection_performed_by_governance"] = True
        _resign(ticket)
        with self.assertRaisesRegex(
            GovernanceModelPlanError,
            "cannot claim Governance performed model selection",
        ):
            validate_governance_model_plan(ticket)

    def test_legacy_schema_still_requires_governance_selection_authority(self) -> None:
        ticket = _ticket()
        ticket["governance_model_plan"]["selection_authority"] = (
            "expert-assessment-center-dynamic-ortools"
        )
        _resign(ticket)
        with self.assertRaisesRegex(
            GovernanceModelPlanError,
            "selection_authority must be decision-system-governance",
        ):
            validate_governance_model_plan(ticket)


if __name__ == "__main__":
    unittest.main()
