from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_governance_model_plan import validate_governance_model_plan  # noqa: E402
from v5_top50_pool_optimizer import materialize_top50_selection  # noqa: E402


PRINCIPLES = [
    "concrete-problem-concrete-analysis",
    "dynamic-adaptation",
    "small-effort-large-return",
]


def _sha(value):
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _plan_sha(plan: dict) -> str:
    material = dict(plan)
    material.pop("plan_sha256", None)
    return _sha(material)


def _packet(
    *,
    complex_task: bool = False,
    candidate_count: int = 50,
    same_company: bool = False,
) -> dict:
    if complex_task:
        task = {
            "question": "X" * 12000,
            "requirements": [f"requirement-{index}" for index in range(12)],
            "required_outputs": [f"field-{index}" for index in range(8)],
            "language": "zh-CN",
        }
        evidence = [{"text": "E" * 1200} for _ in range(8)]
        acceptance = [f"accept-{index}" for index in range(8)]
    else:
        task = {
            "question": "比较A和B并给出建议。",
            "requirements": ["给出最终建议"],
            "language": "zh-CN",
        }
        evidence = []
        acceptance = ["包含最终建议"]

    candidates = []
    for rank in range(1, candidate_count + 1):
        company = "shared-company" if same_company else f"company{rank}"
        model = f"vendor{rank}/reasoner-{rank}"
        candidates.append(
            {
                "slot": rank,
                "candidate_price_rank": rank,
                "model": model,
                "company": company,
                "price_rank_usd_per_million": float(rank),
                "prompt_usd_per_million": float(rank) / 3,
                "completion_usd_per_million": float(rank) * 2 / 3,
                "request_usd": 0.0,
                "official_intelligence_rank": max(1, candidate_count + 1 - rank),
                "popularity_rank": rank,
                "context_length": 262144,
                "max_completion_tokens": 32768,
                "required_context_tokens": 8192,
                "reasoning_rank_verified": True,
                "reasoning_supported": True,
                "expert_center_selectable": True,
                "provider_routing_mode": "unrestricted-openrouter",
                "provider_restrictions_applied": False,
            }
        )

    plan = {
        "schema_version": "governance-expert-model-plan-v1",
        "selection_authority": "decision-system-governance",
        "candidate_pool_authority": "decision-system-governance",
        "model_assignment_authority": "expert-assessment-center-dynamic-ortools",
        "model_substitution_allowed": True,
        "expert_center_reranking_allowed": True,
        "task_sha256": _sha(task),
        "expert_candidate_pool": candidates,
        "expert_candidate_pool_size": candidate_count,
        "provider_routing_mode": "unrestricted-openrouter",
        "provider_restrictions_applied": False,
        "fixed_team_size_required": False,
        "fixed_role_topology_required": False,
        "company_deduplication_required": False,
        "top50_only_required": False,
        "free_first_required": False,
        "canary_required_before_execution": False,
        "model_calls": 0,
    }
    plan["plan_sha256"] = _plan_sha(plan)
    return {
        "task_id": "test-dynamic-optimizer",
        "route": "expert-team",
        "task": task,
        "evidence": evidence,
        "execution_acceptance": acceptance,
        # These are compatibility/advisory inputs only. Dynamic composition must
        # not be rejected or forced to match them.
        "approved_budget": {"calls": 8, "maximum_recovery_calls": 4},
        "governance_model_plan": plan,
    }


class DynamicPoolOptimizerTests(unittest.TestCase):
    def test_team_is_dynamic_and_full_candidate_inventory_is_retained(self) -> None:
        packet, receipt = materialize_top50_selection(_packet())
        plan = packet["governance_model_plan"]
        selected = plan["selected_models"]
        recovery = plan["recovery_models"]
        standby = plan["expert_center_ordered_standby"]

        self.assertGreaterEqual(len(selected), 1)
        self.assertGreaterEqual(len(recovery), 0)
        self.assertEqual(len(selected) + len(recovery) + len(standby), 50)
        self.assertEqual(plan["expert_count"], len(selected))
        self.assertEqual(plan["recovery_count"], len(recovery))
        self.assertFalse(plan["fixed_team_size_required"])
        self.assertFalse(plan["company_deduplication_required"])
        self.assertFalse(plan["optimizer_audit"]["fixed_four_plus_four_used"])
        self.assertFalse(plan["optimizer_audit"]["top50_membership_constraint_used"])
        self.assertFalse(plan["optimizer_audit"]["budget_constraint_used"])
        self.assertEqual(plan["selection_principles"], PRINCIPLES)
        self.assertEqual(plan["provider_routing_mode"], "unrestricted-openrouter")
        self.assertFalse(plan["provider_restrictions_applied"])
        self.assertEqual(receipt["primary_expert_count"], len(selected))
        self.assertEqual(receipt["recovery_count"], len(recovery))

    def test_same_company_candidates_are_allowed(self) -> None:
        packet, _ = materialize_top50_selection(_packet(same_company=True))
        plan = packet["governance_model_plan"]
        active = [*plan["selected_models"], *plan["recovery_models"]]
        self.assertGreaterEqual(len(active), 1)
        self.assertEqual({row["company"] for row in active}, {"shared-company"})
        self.assertFalse(plan["optimizer_audit"]["company_uniqueness_constraint_used"])

    def test_candidate_pool_is_not_capped_at_fifty(self) -> None:
        packet, _ = materialize_top50_selection(_packet(candidate_count=80))
        plan = packet["governance_model_plan"]
        self.assertEqual(
            len(plan["selected_models"])
            + len(plan["recovery_models"])
            + len(plan["expert_center_ordered_standby"]),
            80,
        )
        self.assertFalse(plan["selected_from_top50_reasoning_pool_only"])

    def test_budget_numbers_do_not_force_or_reject_team_shape(self) -> None:
        packet = _packet()
        packet["approved_budget"] = {"calls": 1, "maximum_recovery_calls": 0}
        materialized, _ = materialize_top50_selection(packet)
        plan = materialized["governance_model_plan"]
        self.assertGreaterEqual(plan["expert_count"], 1)
        self.assertFalse(plan["optimizer_audit"]["budget_constraint_used"])

    def test_complex_task_can_expand_dynamic_team(self) -> None:
        simple, _ = materialize_top50_selection(_packet())
        complex_, _ = materialize_top50_selection(_packet(complex_task=True))
        simple_plan = simple["governance_model_plan"]
        complex_plan = complex_["governance_model_plan"]
        simple_audit = simple_plan["optimizer_audit"]
        complex_audit = complex_plan["optimizer_audit"]

        self.assertGreater(
            complex_audit["task_demand_profile"]["pressure"]["overall"],
            simple_audit["task_demand_profile"]["pressure"]["overall"],
        )
        self.assertGreaterEqual(complex_plan["expert_count"], simple_plan["expert_count"])
        self.assertGreaterEqual(
            complex_plan["expert_count"] + complex_plan["recovery_count"],
            simple_plan["expert_count"] + simple_plan["recovery_count"],
        )
        self.assertEqual(len(complex_audit["role_plan"]), complex_plan["expert_count"])

    def test_role_weights_are_task_adaptive(self) -> None:
        simple, _ = materialize_top50_selection(_packet())
        complex_, _ = materialize_top50_selection(_packet(complex_task=True))
        simple_selected = simple["governance_model_plan"]["selected_models"]
        complex_selected = complex_["governance_model_plan"]["selected_models"]
        simple_synthesis = next(row for row in simple_selected if row["role_id"] == "synthesis")
        complex_synthesis = next(row for row in complex_selected if row["role_id"] == "synthesis")

        self.assertNotEqual(
            simple_synthesis["task_adaptive_weights"],
            complex_synthesis["task_adaptive_weights"],
        )

    def test_deterministic_assignment_for_same_task_and_pool(self) -> None:
        first, _ = materialize_top50_selection(_packet())
        second, _ = materialize_top50_selection(_packet())
        first_plan = first["governance_model_plan"]
        second_plan = second["governance_model_plan"]
        self.assertEqual(
            [row["model"] for row in first_plan["selected_models"]],
            [row["model"] for row in second_plan["selected_models"]],
        )
        self.assertEqual(
            [row["model"] for row in first_plan["recovery_models"]],
            [row["model"] for row in second_plan["recovery_models"]],
        )

    def test_recovery_order_is_dynamic_when_recovery_exists(self) -> None:
        packet, _ = materialize_top50_selection(_packet(complex_task=True))
        rows = packet["governance_model_plan"]["recovery_models"]
        self.assertEqual(
            [row["warm_recovery_priority"] for row in rows],
            list(range(1, len(rows) + 1)),
        )

    def test_plan_hash_survives_governance_validation(self) -> None:
        packet, receipt = materialize_top50_selection(_packet())
        plan = packet["governance_model_plan"]
        self.assertEqual(plan["plan_sha256"], _plan_sha(plan))
        self.assertEqual(receipt["selection_basis_sha256"], _sha({
            key: value
            for key, value in plan.items()
            if key not in {"plan_sha256", "expert_center_selection_receipt"}
        }))

        validated = validate_governance_model_plan(packet)
        self.assertEqual(validated["plan_sha256"], plan["plan_sha256"])
        self.assertEqual(validated["expert_count"], len(plan["selected_models"]))
        self.assertEqual(validated["recovery_count"], len(plan["recovery_models"]))


if __name__ == "__main__":
    unittest.main()
