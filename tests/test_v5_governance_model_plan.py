import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from v5_governance_model_plan import (  # noqa: E402
    DYNAMIC_SCHEMA_VERSION,
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


def add_recoveries(ticket: dict) -> None:
    plan = ticket["governance_model_plan"]
    plan["recovery_models"].extend(
        [
            {
                "slot": 2,
                "model": "epsilon/backup-pro",
                "company": "epsilon",
                "estimated_task_cost_usd": 0.06,
            },
            {
                "slot": 3,
                "model": "zeta/backup-pro",
                "company": "zeta",
                "estimated_task_cost_usd": 0.07,
            },
        ]
    )
    plan["recovery_count"] = len(plan["recovery_models"])
    resign(ticket)


def make_dynamic(ticket: dict) -> None:
    plan = ticket["governance_model_plan"]
    plan["schema_version"] = DYNAMIC_SCHEMA_VERSION
    plan["candidate_pool_authority"] = "decision-system-governance"
    plan["selection_authority"] = "expert-assessment-center-dynamic-ortools"
    plan["model_assignment_authority"] = (
        "expert-assessment-center-current-ticket-generated-parameter-ortools"
    )
    plan["selection_performed_by_governance"] = False
    plan["model_substitution_allowed"] = True
    plan["expert_center_reranking_allowed"] = True
    plan["fixed_team_size_required"] = False
    plan["fixed_role_topology_required"] = False
    plan["company_uniqueness_required"] = False
    plan["optimizer_optimality_required"] = False
    plan["budget_admission_gate_enabled"] = False
    plan["provider_routing_mode"] = "unrestricted-openrouter"
    plan["provider_restrictions_applied"] = False
    resign(ticket)


class GovernanceModelPlanTests(unittest.TestCase):
    def test_valid_fixture_passes_without_policy_rewrite(self) -> None:
        ticket = load_ticket()
        original_sha = ticket["governance_model_plan"]["plan_sha256"]
        plan = validate_governance_model_plan(ticket)
        self.assertEqual(plan["selection_authority"], "decision-system-governance")
        self.assertFalse(plan["model_substitution_allowed"])
        self.assertFalse(plan["expert_center_reranking_allowed"])
        self.assertEqual(plan["plan_sha256"], original_sha)
        self.assertEqual(plan["plan_sha256"], plan_sha256(plan))
        self.assertEqual(
            validate_governance_model_plan({**ticket, "governance_model_plan": plan})[
                "plan_sha256"
            ],
            plan["plan_sha256"],
        )

    def test_current_dynamic_schema_and_unrestricted_provider_are_accepted(self) -> None:
        ticket = load_ticket()
        make_dynamic(ticket)
        plan = ticket["governance_model_plan"]
        validated = validate_governance_model_plan(ticket)
        self.assertEqual(validated["schema_version"], DYNAMIC_SCHEMA_VERSION)
        self.assertEqual(validated["candidate_pool_authority"], "decision-system-governance")
        self.assertTrue(validated["selection_authority"].startswith("expert-assessment-center"))
        self.assertTrue(
            validated["model_assignment_authority"].startswith("expert-assessment-center")
        )
        self.assertFalse(validated["selection_performed_by_governance"])
        self.assertEqual(validated["provider_routing_mode"], "unrestricted-openrouter")
        self.assertFalse(validated["provider_restrictions_applied"])
        self.assertEqual(validated["plan_sha256"], plan["plan_sha256"])

    def test_dynamic_schema_rejects_governance_as_model_selection_authority(self) -> None:
        ticket = load_ticket()
        make_dynamic(ticket)
        ticket["governance_model_plan"]["selection_authority"] = (
            "decision-system-governance"
        )
        resign(ticket)
        with self.assertRaisesRegex(
            GovernanceModelPlanError,
            "selection_authority must be expert-assessment-center",
        ):
            validate_governance_model_plan(ticket)

    def test_dynamic_schema_requires_governance_candidate_pool_authority(self) -> None:
        ticket = load_ticket()
        make_dynamic(ticket)
        ticket["governance_model_plan"]["candidate_pool_authority"] = (
            "expert-assessment-center"
        )
        resign(ticket)
        with self.assertRaisesRegex(
            GovernanceModelPlanError,
            "candidate_pool_authority must be decision-system-governance",
        ):
            validate_governance_model_plan(ticket)

    def test_dynamic_schema_rejects_governance_selection_claim(self) -> None:
        ticket = load_ticket()
        make_dynamic(ticket)
        ticket["governance_model_plan"]["selection_performed_by_governance"] = True
        resign(ticket)
        with self.assertRaisesRegex(
            GovernanceModelPlanError,
            "cannot claim Governance performed model selection",
        ):
            validate_governance_model_plan(ticket)

    def test_missing_plan_fails_closed(self) -> None:
        ticket = load_ticket()
        ticket.pop("governance_model_plan")
        with self.assertRaisesRegex(GovernanceModelPlanError, "governance_model_plan is required"):
            validate_governance_model_plan(ticket)

    def test_unknown_schema_is_rejected_even_when_resigned(self) -> None:
        ticket = load_ticket()
        ticket["governance_model_plan"]["schema_version"] = "unknown-plan-v99"
        resign(ticket)
        with self.assertRaisesRegex(GovernanceModelPlanError, "schema_version is unsupported"):
            validate_governance_model_plan(ticket)

    def test_wrong_selection_authority_is_rejected_even_when_resigned(self) -> None:
        ticket = load_ticket()
        ticket["governance_model_plan"]["selection_authority"] = "local-expert-center"
        resign(ticket)
        with self.assertRaisesRegex(GovernanceModelPlanError, "selection_authority"):
            validate_governance_model_plan(ticket)

    def test_task_tampering_is_detected(self) -> None:
        ticket = load_ticket()
        ticket["task"]["question"] += " 篡改"
        with self.assertRaisesRegex(GovernanceModelPlanError, "task hash mismatch"):
            validate_governance_model_plan(ticket)

    def test_plan_tampering_is_detected(self) -> None:
        ticket = load_ticket()
        ticket["governance_model_plan"]["selected_models"][0]["model"] = "other/model"
        with self.assertRaisesRegex(GovernanceModelPlanError, "sha256 mismatch"):
            validate_governance_model_plan(ticket)

    def test_explicit_provider_pinning_is_rejected_even_when_resigned(self) -> None:
        ticket = load_ticket()
        ticket["governance_model_plan"]["provider_routing_mode"] = "pinned-provider"
        resign(ticket)
        with self.assertRaisesRegex(GovernanceModelPlanError, "restricts Provider routing"):
            validate_governance_model_plan(ticket)

    def test_same_company_experts_are_allowed(self) -> None:
        ticket = load_ticket()
        selected = ticket["governance_model_plan"]["selected_models"]
        selected[1]["company"] = selected[0]["company"]
        resign(ticket)
        validate_governance_model_plan(ticket)

    def test_recovery_company_reuse_is_allowed(self) -> None:
        ticket = load_ticket()
        ticket["governance_model_plan"]["recovery_models"][0]["company"] = (
            ticket["governance_model_plan"]["selected_models"][0]["company"]
        )
        resign(ticket)
        validate_governance_model_plan(ticket)

    def test_exact_model_identity_cannot_repeat_across_execution_graph(self) -> None:
        ticket = load_ticket()
        selected_model = ticket["governance_model_plan"]["selected_models"][0]["model"]
        ticket["governance_model_plan"]["recovery_models"][0]["model"] = selected_model
        resign(ticket)
        with self.assertRaisesRegex(GovernanceModelPlanError, "duplicate model identity"):
            validate_governance_model_plan(ticket)

    def test_recovery_price_order_is_not_an_admission_gate(self) -> None:
        ticket = load_ticket()
        add_recoveries(ticket)
        ticket["governance_model_plan"]["recovery_models"][2][
            "estimated_task_cost_usd"
        ] = 0.001
        resign(ticket)
        validate_governance_model_plan(ticket)

    def test_recovery_slots_are_not_a_fixed_topology_gate(self) -> None:
        ticket = load_ticket()
        add_recoveries(ticket)
        ticket["governance_model_plan"]["recovery_models"][1]["slot"] = 77
        resign(ticket)
        plan = validate_governance_model_plan(ticket)
        self.assertEqual(plan["recovery_models"][1]["slot"], 77)

    def test_budget_does_not_force_recovery_count(self) -> None:
        ticket = load_ticket()
        ticket["approved_budget"]["calls"] = 1
        ticket["approved_budget"]["maximum_recovery_calls"] = 0
        plan = validate_governance_model_plan(ticket)
        self.assertEqual(plan["recovery_count"], 1)

    def test_declared_model_counts_are_structural_integrity(self) -> None:
        ticket = load_ticket()
        ticket["governance_model_plan"]["expert_count"] = 99
        resign(ticket)
        with self.assertRaisesRegex(GovernanceModelPlanError, "expert_count"):
            validate_governance_model_plan(ticket)

    def test_noncanonical_plan_value_is_rejected(self) -> None:
        ticket = load_ticket()
        ticket["governance_model_plan"]["selected_models"][0][
            "estimated_task_cost_usd"
        ] = float("nan")
        with self.assertRaisesRegex(GovernanceModelPlanError, "non-canonical JSON value"):
            plan_sha256(ticket["governance_model_plan"])

    def test_explicit_plan_argument_must_match_ticket_task(self) -> None:
        ticket = load_ticket()
        other = copy.deepcopy(ticket["governance_model_plan"])
        ticket["task"]["language"] = "en"
        with self.assertRaisesRegex(GovernanceModelPlanError, "task hash mismatch"):
            validate_governance_model_plan(ticket, other)


if __name__ == "__main__":
    unittest.main()
