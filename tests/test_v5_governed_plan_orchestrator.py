import copy
import json
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from v5_dynamic_pipeline import (  # noqa: E402
    _dynamic_assignment_fields,
    _expert_assignment_active,
)
from v5_governed_plan_orchestrator import (  # noqa: E402
    build_governed_proposal,
)
from v5_governance_model_plan import (  # noqa: E402
    GovernanceModelPlanError,
    plan_sha256,
)
from v5_production_expert_policy import (  # noqa: E402
    EvidenceCompleteExecutionEngine,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "governance-ticket.json"


def load_ticket() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def resign(ticket: dict) -> None:
    plan = ticket["governance_model_plan"]
    plan["plan_sha256"] = plan_sha256(plan)


class GovernedPlanOrchestratorTests(unittest.TestCase):
    def test_current_plan_materializes_exact_declared_roles_without_inventing_edges(self) -> None:
        ticket = load_ticket()
        proposal, audit = build_governed_proposal(
            ticket=ticket,
            catalog={},
            task_envelope={},
        )
        self.assertEqual(
            [row["model"] for row in proposal["nodes"]],
            [row["model"] for row in ticket["governance_model_plan"]["selected_models"]],
        )
        self.assertEqual(
            [row["role_kind"] for row in proposal["nodes"]],
            [row["role_kind"] for row in ticket["governance_model_plan"]["selected_models"]],
        )
        self.assertEqual(len(proposal["nodes"]), 4)
        self.assertEqual(proposal["edges"], [])
        self.assertEqual(
            sorted(proposal["final_nodes"]),
            sorted(row["node_id"] for row in proposal["nodes"]),
        )
        self.assertEqual(
            proposal["recovery_models"],
            ticket["governance_model_plan"]["recovery_models"],
        )
        self.assertTrue(audit["networkx_used_for_dag_validation"])
        self.assertFalse(audit["fixed_team_size_used"])
        self.assertFalse(audit["fixed_four_plus_four_used"])
        self.assertFalse(audit["fixed_role_topology_used"])
        self.assertFalse(audit["fixed_role_grammar_used"])
        self.assertFalse(audit["role_dependencies_recomputed_from_role_kind"])
        self.assertFalse(audit["company_uniqueness_constraint_used"])
        self.assertFalse(audit["provider_endpoint_resolution_performed"])
        self.assertEqual(audit["provider_routing_mode"], "unrestricted-openrouter")
        self.assertFalse(audit["provider_restrictions_applied"])

    def test_dynamic_assignment_telemetry_uses_current_authority_not_top50(self) -> None:
        plan = {
            "selected_from_top50_reasoning_pool_only": False,
            "candidate_pool_authority": "decision-system-governance",
            "model_assignment_authority": "expert-assessment-center-dynamic-ortools",
            "selected_models": [{"model": "vendor/model-a"}],
            "optimizer": "ortools-cp-sat",
            "optimizer_audit": {
                "optimizer": "ortools-cp-sat",
                "solver_status": "OPTIMAL",
                "optimality_proven": True,
                "fallback_used": False,
            },
        }
        self.assertTrue(_expert_assignment_active(plan))
        fields = _dynamic_assignment_fields(plan)
        self.assertEqual(
            "expert-assessment-center-dynamic-ortools",
            fields["model_assignment_authority"],
        )
        self.assertEqual(
            "expert-assessment-center-dynamic-ortools",
            fields["selection_authority"],
        )
        self.assertTrue(fields["expert_center_pool_assignment_performed"])
        self.assertTrue(fields["model_selection_performed_locally"])
        self.assertTrue(fields["optimizer_present"])
        self.assertTrue(fields["optimizer_used"])
        self.assertEqual("ortools-cp-sat", fields["optimizer"])
        self.assertTrue(fields["optimizer_optimality_proven"])

    def test_successful_company_duplicates_exclude_failed_attempt_companies(self) -> None:
        result = {
            "node_results": [
                {
                    "node_id": "N1",
                    "selected_model": "nvidia/nemotron-3-ultra-550b-a55b:free",
                    "resolved_model": "inclusionai/ling-2.6-flash",
                    "status": "success_recovered",
                    "attempts": [
                        {
                            "attempt_kind": "initial",
                            "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
                            "status": "call_failed",
                        },
                        {
                            "attempt_kind": "replacement",
                            "model": "inclusionai/ling-2.6-flash",
                            "response_model": "inclusionai/ling-2.6-flash",
                            "status": "passed",
                        },
                    ],
                },
                {
                    "node_id": "N2",
                    "selected_model": "nvidia/nemotron-3-super-120b-a12b:free",
                    "resolved_model": "inclusionai/ling-2.6-flash",
                    "status": "success_recovered",
                    "attempts": [
                        {
                            "attempt_kind": "initial",
                            "model": "nvidia/nemotron-3-super-120b-a12b:free",
                            "status": "call_failed",
                        },
                        {
                            "attempt_kind": "replacement",
                            "model": "inclusionai/ling-2.6-flash",
                            "response_model": "inclusionai/ling-2.6-flash",
                            "status": "passed",
                        },
                    ],
                },
            ]
        }
        audit = EvidenceCompleteExecutionEngine._actual_company_audit(result)
        called_duplicates = audit["duplicate_called_companies_across_nodes"]
        successful_duplicates = audit["duplicate_successful_companies"]
        self.assertEqual("PASS", audit["status"])
        self.assertIn("nvidia", called_duplicates)
        self.assertIn("inclusionai", called_duplicates)
        self.assertNotIn("nvidia", successful_duplicates)
        self.assertEqual(["N1", "N2"], successful_duplicates["inclusionai"])
        self.assertEqual(
            "strict-successful-node-models-only",
            audit["successful_company_duplicates_source"],
        )

    def test_catalog_is_not_a_provider_or_model_admission_gate(self) -> None:
        ticket = load_ticket()
        baseline = build_governed_proposal(
            ticket=ticket,
            catalog={},
            task_envelope={},
        )
        rows = [{"model": "unrelated/model", "provider": "fixture"}]
        for seed in range(10):
            shuffled = list(rows)
            random.Random(seed).shuffle(shuffled)
            candidate = build_governed_proposal(
                ticket=ticket,
                catalog={"endpoints": shuffled},
                task_envelope={"required_context_tokens": 999999999},
            )
            self.assertEqual(candidate, baseline)

    def test_single_expert_without_role_metadata_remains_single_dynamic_sink(self) -> None:
        ticket = load_ticket()
        plan = ticket["governance_model_plan"]
        plan["selected_models"] = [copy.deepcopy(plan["selected_models"][0])]
        plan["selected_models"][0].pop("role_kind", None)
        plan["selected_models"][0].pop("role_id", None)
        plan["expert_count"] = 1
        plan["recovery_models"] = []
        plan["recovery_count"] = 0
        resign(ticket)
        proposal, _ = build_governed_proposal(
            ticket=ticket,
            catalog={},
            task_envelope={},
        )
        self.assertEqual(len(proposal["nodes"]), 1)
        self.assertEqual(proposal["nodes"][0]["role_kind"], "dynamic:task-role")
        self.assertEqual(proposal["edges"], [])
        self.assertEqual(proposal["final_nodes"], [proposal["nodes"][0]["node_id"]])

    def test_two_experts_without_declared_dependency_remain_parallel_sinks(self) -> None:
        ticket = load_ticket()
        plan = ticket["governance_model_plan"]
        plan["selected_models"] = [
            copy.deepcopy(plan["selected_models"][0]),
            copy.deepcopy(plan["selected_models"][1]),
        ]
        for row in plan["selected_models"]:
            row.pop("role_kind", None)
            row.pop("role_id", None)
        plan["expert_count"] = 2
        plan["recovery_models"] = []
        plan["recovery_count"] = 0
        resign(ticket)
        proposal, _ = build_governed_proposal(
            ticket=ticket,
            catalog={},
            task_envelope={},
        )
        self.assertEqual(
            [row["role_kind"] for row in proposal["nodes"]],
            ["dynamic:task-role", "dynamic:task-role"],
        )
        self.assertEqual(proposal["edges"], [])
        self.assertEqual(
            sorted(proposal["final_nodes"]),
            sorted(row["node_id"] for row in proposal["nodes"]),
        )

    def test_same_company_models_are_not_rejected_by_orchestrator(self) -> None:
        ticket = load_ticket()
        plan = ticket["governance_model_plan"]
        plan["selected_models"][1]["company"] = plan["selected_models"][0]["company"]
        resign(ticket)
        proposal, audit = build_governed_proposal(
            ticket=ticket,
            catalog={},
            task_envelope={},
        )
        self.assertEqual(len(proposal["nodes"]), len(plan["selected_models"]))
        self.assertFalse(audit["company_uniqueness_constraint_used"])

    def test_tampered_plan_hash_fails_closed_before_dag_materialization(self) -> None:
        ticket = load_ticket()
        ticket["governance_model_plan"]["selected_models"][0]["model"] = "other/model"
        with self.assertRaisesRegex(GovernanceModelPlanError, "sha256 mismatch"):
            build_governed_proposal(
                ticket=ticket,
                catalog={},
                task_envelope={},
            )


if __name__ == "__main__":
    unittest.main()
