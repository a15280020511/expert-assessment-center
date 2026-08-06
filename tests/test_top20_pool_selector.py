from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import unittest


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_selector():
    path = (
        Path(__file__).resolve().parents[1]
        / "open-model-market"
        / "v5_top20_pool_selector.py"
    )
    return _load_module(path, "v5_top50_pool_optimizer_test")


def _load_validator():
    path = (
        Path(__file__).resolve().parents[1]
        / "open-model-market"
        / "v5_governance_model_plan.py"
    )
    return _load_module(path, "v5_governance_model_plan_top50_test")


def _canonical(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(value) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _company(index: int) -> str:
    return "company1" if index in {1, 2, 3} else f"company{index}"


def _candidate(index: int) -> dict:
    company = _company(index)
    price = float(index) / 10
    return {
        "slot": index,
        "candidate_price_rank": index,
        "model": f"{company}/model-{index}",
        "company": company,
        "estimated_task_cost_usd": price,
        "price_rank_usd_per_million": price,
        "prompt_usd_per_million": price / 3,
        "completion_usd_per_million": price * 2 / 3,
        "official_intelligence_rank": max(1, 80 - index * 2),
        "qualified_provider_count": 1 + index % 5,
        "endpoint_inventory_sha256": f"{index:064x}"[-64:],
        "selection_evidence": (
            "openrouter-top-weekly-reasoning+live-exact-endpoint-qualified+"
            "authenticated-zdr-endpoint-qualified"
        ),
        "popularity_rank": index,
        "reasoning_rank_verified": True,
        "reasoning_supported": True,
        "ranking_basis": "openrouter-most-popular-last-week-token-volume",
        "source_pool_schema_version": (
            "governance-openrouter-top50-reasoning-pool-v1"
        ),
        "source_pool": "openrouter-most-popular-last-week-token-volume",
        "expert_center_selectable": True,
    }


def _packet() -> dict:
    task = {"question": "fixture"}
    raw = [
        {
            "popularity_rank": index,
            "source_rank": index,
            "model": f"{_company(index)}/model-{index}",
            "company": _company(index),
            "reasoning_supported": True,
        }
        for index in range(1, 51)
    ]
    eligible = [_candidate(index) for index in range(1, 31)]
    distinct_companies = len({row["company"] for row in eligible})
    legacy_raw = raw[:20]
    plan = {
        "schema_version": "governance-expert-model-plan-v1",
        "selection_authority": "decision-system-governance",
        "model_substitution_allowed": False,
        "expert_center_reranking_allowed": False,
        "task_sha256": _sha(task),
        "top50_reasoning_pool_schema_version": (
            "governance-openrouter-top50-reasoning-pool-v1"
        ),
        "top50_reasoning_pool_source": (
            "openrouter-most-popular-last-week-token-volume"
        ),
        "top50_reasoning_pool_size": 50,
        "top50_reasoning_models": raw,
        "top50_reasoning_pool_sha256": _sha(raw),
        "top20_reasoning_pool_schema_version": (
            "governance-openrouter-top20-reasoning-pool-v1"
        ),
        "top20_reasoning_pool_source": (
            "openrouter-most-popular-last-week-token-volume"
        ),
        "top20_reasoning_pool_size": 20,
        "top20_reasoning_models": legacy_raw,
        "top20_reasoning_pool_sha256": _sha(legacy_raw),
        "expert_selectable_candidates": eligible,
        "expert_selectable_candidates_sha256": _sha(eligible),
        "expert_selectable_candidate_count": len(eligible),
        "expert_selectable_distinct_company_count": distinct_companies,
        "candidate_pool_authority": "decision-system-governance",
        "model_assignment_authority": "expert-assessment-center",
        "expert_center_pool_selection_allowed": True,
        "old_flagship_filter_applied_to_top50_pool": False,
        "old_flagship_filter_applied_to_top20_pool": False,
        "endpoint_qualification_performed_by_governance": True,
        "model_calls": 0,
        "selected_models": [],
        "recovery_models": [],
        "price_ranked_models": [],
        "expert_count": 4,
        "recovery_count": 4,
    }
    plan["plan_sha256"] = _sha(plan)
    return {
        "task": task,
        "approved_budget": {"calls": 8, "maximum_recovery_calls": 4},
        "governance_model_plan": plan,
    }


class Top50PoolOptimizerTests(unittest.TestCase):
    def test_ortools_assigns_four_roles_and_retains_all_fifty(self) -> None:
        module = _load_selector()
        packet, receipt = module.materialize_top20_selection(_packet())
        plan = packet["governance_model_plan"]

        self.assertTrue(plan["expert_center_pool_selection_completed"])
        self.assertTrue(plan["selected_from_top50_reasoning_pool_only"])
        self.assertTrue(plan["optimizer_used"])
        self.assertEqual(plan["optimizer_library"], "ortools")
        self.assertEqual(plan["optimizer_algorithm"], "cp-sat")
        self.assertEqual(plan["expert_count"], 4)
        self.assertEqual(plan["recovery_count"], 4)
        self.assertEqual(len(plan["selected_models"]), 4)
        self.assertEqual(len(plan["recovery_models"]), 4)
        self.assertEqual(
            len({row["company"] for row in plan["selected_models"]}), 4
        )
        self.assertEqual(
            len({row["company"] for row in plan["recovery_models"]}), 4
        )
        self.assertEqual(
            [row["role_id"] for row in plan["selected_models"]],
            ["evidence", "options", "review", "synthesis"],
        )
        self.assertEqual(plan["recovery_inventory_count"], 4)
        self.assertEqual(plan["total_qualified_recovery_inventory_count"], 26)
        self.assertEqual(plan["extended_recovery_model_count"], 22)
        self.assertEqual(len(plan["extended_recovery_models"]), 22)
        self.assertEqual(plan["expert_center_top50_inventory_count"], 50)
        self.assertEqual(len(plan["expert_center_top50_inventory"]), 50)
        self.assertEqual(receipt["top50_inventory_count"], 50)
        self.assertEqual(receipt["total_qualified_recovery_inventory_count"], 26)
        self.assertEqual(receipt["model_calls"], 0)
        self.assertEqual(plan["plan_sha256"], module._plan_digest(plan))

    def test_all_qualified_nonactive_models_are_ordered_recovery_inventory(self) -> None:
        module = _load_selector()
        packet, _ = module.materialize_top50_selection(_packet())
        plan = packet["governance_model_plan"]
        selected_models = {row["model"] for row in plan["selected_models"]}
        warm = list(plan["recovery_models"])
        extended = list(plan["extended_recovery_models"])
        all_recoveries = [*warm, *extended]
        eligible_models = {
            row["model"] for row in plan["expert_selectable_candidates"]
        }
        self.assertEqual(
            {row["model"] for row in all_recoveries},
            eligible_models - selected_models,
        )
        ordered = sorted(all_recoveries, key=lambda row: row["recovery_priority"])
        self.assertEqual(
            [row["recovery_priority"] for row in ordered],
            list(range(1, len(ordered) + 1)),
        )
        self.assertTrue(all(row["warm_recovery"] for row in warm))
        self.assertTrue(all(not row["warm_recovery"] for row in extended))

    def test_materialized_plan_passes_top50_execution_contract(self) -> None:
        selector = _load_selector()
        validator = _load_validator()
        packet, _ = selector.materialize_top50_selection(_packet())
        validated = validator.validate_governance_model_plan(packet)
        self.assertTrue(validated["selected_from_top50_reasoning_pool_only"])
        self.assertEqual(
            validated["optimizer_audit"]["optimizer"], "ortools-cp-sat"
        )

    def test_optimizer_rejects_fewer_than_four_distinct_companies(self) -> None:
        module = _load_selector()
        packet = _packet()
        plan = packet["governance_model_plan"]
        eligible = plan["expert_selectable_candidates"]
        for index, row in enumerate(eligible):
            row["company"] = f"company{index % 3}"
        plan["expert_selectable_distinct_company_count"] = 3
        plan["expert_selectable_candidates_sha256"] = _sha(eligible)
        material = dict(plan)
        material.pop("plan_sha256", None)
        plan["plan_sha256"] = _sha(material)

        with self.assertRaisesRegex(
            module.Top50PoolOptimizationError,
            "fewer than four distinct-company",
        ):
            module.materialize_top50_selection(packet)


if __name__ == "__main__":
    unittest.main()
