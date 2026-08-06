from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
if str(MARKET) not in sys.path:
    sys.path.insert(0, str(MARKET))


def load_module(filename: str, name: str):
    spec = importlib.util.spec_from_file_location(name, MARKET / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


plan_module = load_module(
    "v5_governance_model_plan.py",
    "v5_governance_model_plan_unique_company_test",
)
GovernanceModelPlanError = plan_module.GovernanceModelPlanError
validate_governance_model_plan = plan_module.validate_governance_model_plan
plan_sha256 = plan_module.plan_sha256


def fixture() -> dict:
    return json.loads(
        (ROOT / "tests" / "fixtures" / "governance-ticket.json").read_text(
            encoding="utf-8"
        )
    )


def ticket() -> dict:
    value = fixture()
    plan = value["governance_model_plan"]
    # The generic fixture deliberately uses gamma/fast. This benchmark-specific
    # test needs a valid non-economy flagship baseline; the Luna case below
    # remains the explicit economy-tier negative test.
    plan["selected_models"][0]["model"] = "gamma/pro"
    plan.update(
        {
            "catalog_fetch_mode": "live-per-task-no-cross-task-cache",
            "reasoning_model_required": True,
            "flagship_definition": (
                "strict-product-tier-or-benchmarked-company-natural-top-layer"
            ),
            "benchmark_source": "artificial-analysis-via-openrouter",
            "company_model_policy": (
                "one-highest-intelligence-verified-reasoning-flagship-per-company-then-price-rank"
            ),
            "company_uniqueness_scope": "selected-and-recovery",
            "price_rank_basis": (
                "prompt_usd_per_million + completion_usd_per_million"
            ),
            "endpoint_qualification_performed_by_governance": True,
            "model_calls": 0,
        }
    )
    for index, row in enumerate(
        [*plan["selected_models"], *plan["recovery_models"]], 1
    ):
        row.update(
            {
                "flagship_verified": True,
                "flagship_basis": "strict-product-tier",
                "benchmark_source": "artificial-analysis-via-openrouter",
                "intelligence_index": 80.0 - index,
                "coding_index": 70.0 - index,
                "agentic_index": 60.0 - index,
                "balanced_score": 75.0 - index,
                "benchmark_evidence_sha256": f"{index:064x}"[-64:],
                "qualified_provider_count": 1,
                "endpoint_inventory_sha256": f"{index + 20:064x}"[-64:],
                "selection_evidence": (
                    "non-search+verified-company-flagship-reasoning+"
                    "strict-product-tier+price-order+live-exact-endpoint-qualified+"
                    "authenticated-zdr-endpoint-qualified"
                ),
            }
        )
    plan["price_ranked_models"] = []
    for price_rank, row in enumerate(
        [*plan["selected_models"], *plan["recovery_models"]], 1
    ):
        ranked = copy.deepcopy(row)
        ranked.pop("role_id", None)
        ranked.pop("role_kind", None)
        ranked.pop("role", None)
        ranked["price_rank"] = price_rank
        ranked["slot"] = price_rank
        ranked["price_rank_usd_per_million"] = float(price_rank)
        plan["price_ranked_models"].append(ranked)
    for index, row in enumerate(plan["recovery_models"], 1):
        row["slot"] = index
        row["price_rank_usd_per_million"] = float(len(plan["selected_models"]) + index)
    plan["plan_sha256"] = plan_sha256(plan)
    return value


def resign(value: dict) -> None:
    plan = value["governance_model_plan"]
    plan["plan_sha256"] = plan_sha256(plan)


class UniqueCompanyPriceRankingTests(unittest.TestCase):
    def test_valid_benchmarked_reasoning_flagship_plan_passes(self) -> None:
        value = ticket()
        self.assertEqual(validate_governance_model_plan(value), value["governance_model_plan"])

    def test_live_plan_requires_price_ranking(self) -> None:
        value = ticket()
        value["governance_model_plan"].pop("price_ranked_models")
        resign(value)
        with self.assertRaisesRegex(GovernanceModelPlanError, "price_ranked_models"):
            validate_governance_model_plan(value)

    def test_price_ranking_must_be_ascending(self) -> None:
        value = ticket()
        ranked = value["governance_model_plan"]["price_ranked_models"]
        ranked[0]["price_rank_usd_per_million"] = 100.0
        resign(value)
        with self.assertRaisesRegex(GovernanceModelPlanError, "ascending price order"):
            validate_governance_model_plan(value)

    def test_live_plan_requires_reasoning_models(self) -> None:
        value = ticket()
        value["governance_model_plan"]["reasoning_model_required"] = False
        resign(value)
        with self.assertRaisesRegex(GovernanceModelPlanError, "reasoning_model_required"):
            validate_governance_model_plan(value)

    def test_luna_is_rejected_even_with_forged_evidence(self) -> None:
        value = ticket()
        value["governance_model_plan"]["selected_models"][0]["model"] = (
            "openai/gpt-5.6-luna"
        )
        value["governance_model_plan"]["price_ranked_models"][0]["model"] = (
            "openai/gpt-5.6-luna"
        )
        resign(value)
        with self.assertRaisesRegex(GovernanceModelPlanError, "economy-tier"):
            validate_governance_model_plan(value)

    def test_invalid_flagship_basis_is_rejected(self) -> None:
        value = ticket()
        value["governance_model_plan"]["selected_models"][0]["flagship_basis"] = (
            "company-marketing-label"
        )
        resign(value)
        with self.assertRaisesRegex(GovernanceModelPlanError, "flagship_basis is invalid"):
            validate_governance_model_plan(value)

    def test_missing_benchmark_hash_is_rejected(self) -> None:
        value = ticket()
        value["governance_model_plan"]["recovery_models"][0].pop(
            "benchmark_evidence_sha256"
        )
        resign(value)
        with self.assertRaisesRegex(
            GovernanceModelPlanError, "benchmark_evidence_sha256"
        ):
            validate_governance_model_plan(value)

    def test_benchmark_source_mismatch_is_rejected(self) -> None:
        value = ticket()
        value["governance_model_plan"]["selected_models"][0][
            "benchmark_source"
        ] = "untrusted"
        resign(value)
        with self.assertRaisesRegex(
            GovernanceModelPlanError, "benchmark_source is invalid"
        ):
            validate_governance_model_plan(value)

    def test_missing_reasoning_flagship_evidence_is_rejected(self) -> None:
        value = ticket()
        value["governance_model_plan"]["selected_models"][0][
            "selection_evidence"
        ] = "authenticated-zdr-endpoint-qualified"
        resign(value)
        with self.assertRaisesRegex(
            GovernanceModelPlanError,
            "lacks verified reasoning flagship evidence",
        ):
            validate_governance_model_plan(value)


if __name__ == "__main__":
    unittest.main()
