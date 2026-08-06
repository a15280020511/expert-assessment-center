from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_governance_model_plan import validate_governance_model_plan
from v5_top50_plan_validation import validate_top50_contract
from v5_top50_pool_optimizer import Top50PoolOptimizationError, materialize_top50_selection


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


def _packet(*, complex_task: bool = False) -> dict:
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

    raw = []
    candidates = []
    for rank in range(1, 51):
        company = f"company{rank}"
        model = f"{company}/reasoner-{rank}"
        raw.append(
            {
                "popularity_rank": rank,
                "source_rank": rank,
                "model": model,
                "company": company,
                "reasoning_supported": True,
                "pool_source": "openrouter-most-popular-last-week-token-volume",
                "popularity_period": "week",
                "provider_routing_mode": "unrestricted-openrouter",
            }
        )
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
                "official_intelligence_rank": 51 - rank,
                "popularity_rank": rank,
                "context_length": 262144,
                "max_completion_tokens": 32768,
                "required_context_tokens": 8192,
                "reasoning_rank_verified": True,
                "reasoning_supported": True,
                "selection_evidence": (
                    "openrouter-top-weekly-reasoning+"
                    "model-metadata-qualified+"
                    "unrestricted-openrouter-provider-routing"
                ),
                "expert_center_selectable": True,
                "provider_routing_mode": "unrestricted-openrouter",
                "provider_restrictions_applied": False,
            }
        )
    plan = {
        "schema_version": "governance-expert-model-plan-v1",
        "selection_authority": "decision-system-governance",
        "model_substitution_allowed": False,
        "expert_center_reranking_allowed": False,
        "task_sha256": _sha(task),
        "required_context_tokens": 8192,
        "top50_reasoning_pool_schema_version": "governance-openrouter-top50-reasoning-pool-v2-open-provider",
        "top50_reasoning_pool_source": "openrouter-most-popular-last-week-token-volume",
        "top50_reasoning_pool_period": "week",
        "top50_reasoning_pool_size": 50,
        "top50_reasoning_models": raw,
        "top50_expert_selectable_candidates": candidates,
        "top50_expert_selectable_distinct_company_count": 50,
        "top50_candidate_pool_authority": "decision-system-governance",
        "top50_model_assignment_authority": "expert-assessment-center-ortools",
        "expert_center_top50_pool_selection_allowed": True,
        "top50_task_adaptive_assignment_required": True,
        "top50_model_assignment_principles": list(PRINCIPLES),
        "top50_assignment_recomputed_from_current_task": True,
        "top50_cross_task_history_allowed": False,
        "top50_semantic_keyword_routing_allowed": False,
        "top50_domain_hardcoding_allowed": False,
        "top50_provider_metric_allowed_in_assignment": False,
        "top50_provider_routing_mode": "unrestricted-openrouter",
        "top50_provider_restrictions_applied": False,
        "top50_provider_endpoint_qualification_required": False,
        "top50_zdr_provider_qualification_required": False,
        "top50_old_flagship_filter_applied": False,
        "top50_model_calls": 0,
    }
    plan["top50_reasoning_pool_sha256"] = _sha(raw)
    plan["top50_expert_selectable_candidates_sha256"] = _sha(candidates)
    plan["plan_sha256"] = _plan_sha(plan)
    return {
        "task_id": "test-top50-optimizer",
        "route": "expert-team",
        "task": task,
        "evidence": evidence,
        "execution_acceptance": acceptance,
        "approved_budget": {"calls": 8, "maximum_recovery_calls": 4},
        "governance_model_plan": plan,
    }


class Top50PoolOptimizerTests(unittest.TestCase):
    def test_four_active_four_recovery_and_all_fifty_retained(self) -> None:
        packet, receipt = materialize_top50_selection(_packet())
        plan = packet["governance_model_plan"]
        self.assertEqual(len(plan["selected_models"]), 4)
        self.assertEqual(len(plan["recovery_models"]), 4)
        self.assertEqual(len(plan["expert_center_top50_inventory"]), 50)
        companies = {
            row["company"]
            for row in [*plan["selected_models"], *plan["recovery_models"]]
        }
        self.assertEqual(len(companies), 8)
        self.assertTrue(plan["optimizer_audit"]["optimality_proven"])
        self.assertFalse(
            plan["optimizer_audit"]["constraints"]["provider_resilience_used"]
        )
        self.assertTrue(
            plan["optimizer_audit"]["constraints"]["provider_routing_unrestricted"]
        )
        self.assertTrue(
            plan["optimizer_audit"]["constraints"]["four_primary_calls_reserved"]
        )
        self.assertTrue(
            plan["optimizer_audit"]["constraints"]["four_warm_recovery_calls_reserved"]
        )
        self.assertTrue(
            plan["optimizer_audit"]["constraints"]["dynamic_role_weights_used"]
        )
        self.assertTrue(plan["optimizer_audit"]["constraints"]["marginal_return_used"])
        self.assertTrue(plan["task_adaptive_scoring_completed"])
        self.assertEqual(plan["selection_principles"], PRINCIPLES)
        self.assertEqual(plan["top50_model_assignment_principles"], PRINCIPLES)
        self.assertEqual(plan["provider_routing_mode"], "unrestricted-openrouter")
        self.assertFalse(plan["provider_restrictions_applied"])
        self.assertEqual(receipt["optimizer_audit"]["optimizer"], "ortools-cp-sat")
        self.assertEqual(receipt["approved_recovery_calls"], 4)
        self.assertEqual(
            receipt["warm_recovery_order_basis"],
            "same-task-adaptive-recovery-objective",
        )
        validate_top50_contract(
            plan,
            plan["selected_models"],
            plan["recovery_models"],
        )

    def test_materialized_plan_passes_full_governance_validation(self) -> None:
        packet, _ = materialize_top50_selection(_packet())
        validated = validate_governance_model_plan(packet)
        self.assertEqual(validated["plan_sha256"], packet["governance_model_plan"]["plan_sha256"])
        self.assertEqual(validated["expert_count"], 4)
        self.assertEqual(validated["recovery_count"], 4)

    def test_deterministic_assignment(self) -> None:
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
        self.assertEqual(
            first_plan["optimizer_audit"]["objective_value"],
            second_plan["optimizer_audit"]["objective_value"],
        )

    def test_complex_task_changes_dynamic_weights(self) -> None:
        simple, _ = materialize_top50_selection(_packet())
        complex_, _ = materialize_top50_selection(_packet(complex_task=True))
        simple_audit = simple["governance_model_plan"]["optimizer_audit"]
        complex_audit = complex_["governance_model_plan"]["optimizer_audit"]
        self.assertGreater(
            complex_audit["task_demand_profile"]["pressure"]["overall"],
            simple_audit["task_demand_profile"]["pressure"]["overall"],
        )
        simple_synthesis = next(
            row
            for row in simple_audit["role_assignments"]
            if row["role_id"] == "synthesis"
        )
        complex_synthesis = next(
            row
            for row in complex_audit["role_assignments"]
            if row["role_id"] == "synthesis"
        )
        self.assertLess(
            complex_synthesis["weights"]["task_cost"],
            simple_synthesis["weights"]["task_cost"],
        )
        self.assertGreater(
            complex_synthesis["weights"]["intelligence"],
            simple_synthesis["weights"]["intelligence"],
        )

    def test_warm_recovery_priority_uses_same_dynamic_objective(self) -> None:
        packet, _ = materialize_top50_selection(_packet())
        plan = packet["governance_model_plan"]
        rows = plan["recovery_models"]
        scores = [row["task_adaptive_objective_score"] for row in rows]
        self.assertEqual(scores, sorted(scores))
        self.assertEqual(
            [row["warm_recovery_priority"] for row in rows],
            [1, 2, 3, 4],
        )
        warm_inventory = [
            row
            for row in plan["expert_center_top50_inventory"]
            if row["standby_state"] == "warm-recovery"
        ]
        self.assertEqual(
            sorted(row["warm_recovery_priority"] for row in warm_inventory),
            [1, 2, 3, 4],
        )

    def test_less_than_four_recovery_budget_is_rejected_before_solving(self) -> None:
        packet = _packet()
        packet["approved_budget"]["maximum_recovery_calls"] = 1
        with self.assertRaisesRegex(Top50PoolOptimizationError, "must equal four"):
            materialize_top50_selection(packet)

    def test_less_than_eight_total_calls_is_rejected_before_solving(self) -> None:
        packet = _packet()
        packet["approved_budget"]["calls"] = 7
        with self.assertRaisesRegex(Top50PoolOptimizationError, "between 8 and 16"):
            materialize_top50_selection(packet)


if __name__ == "__main__":
    unittest.main()
