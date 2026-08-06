from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))
from v5_governance_model_plan import (  # noqa: E402
    BENCHMARK_SOURCE,
    FLAGSHIP_DEFINITION,
    GovernanceModelPlanError,
    plan_sha256,
    task_sha256,
    validate_governance_model_plan,
)

FIXTURE = ROOT / "tests" / "fixtures" / "governance-ticket.json"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def recovery(slot: int, company: str, cost: float) -> dict:
    return {
        "slot": slot,
        "model": f"{company}/reasoning-pro",
        "company": company,
        "estimated_task_cost_usd": cost,
        "prompt_usd_per_million": cost / 3,
        "completion_usd_per_million": cost * 2 / 3,
    }


def enrich_row(row: dict, *, natural_top: bool = False) -> None:
    basis = (
        "company-local-natural-top-layer"
        if natural_top
        else "strict-product-tier"
    )
    model = row["model"]
    row.update(
        {
            "price_rank_usd_per_million": row["estimated_task_cost_usd"],
            "flagship_verified": True,
            "flagship_basis": basis,
            "company_flagship_method": "fixture-company-top",
            "benchmark_source": BENCHMARK_SOURCE,
            "intelligence_index": 50.0,
            "coding_index": 50.0,
            "agentic_index": 50.0,
            "balanced_score": 50.0,
            "benchmark_evidence_sha256": digest(model + "-benchmark"),
            "endpoint_inventory_sha256": digest(model + "-endpoint"),
            "qualified_provider_count": 1,
            "selection_evidence": (
                "non-search+verified-company-flagship-reasoning+"
                f"{basis}+price-order+live-exact-endpoint-qualified+"
                "authenticated-zdr-endpoint-qualified+"
                "minimum-one-zdr-provider-route"
            ),
        }
    )


def resign(value: dict) -> None:
    plan = value["governance_model_plan"]
    plan["task_sha256"] = task_sha256(value)
    plan["plan_sha256"] = plan_sha256(plan)


def ticket() -> dict:
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    plan = value["governance_model_plan"]
    primary = (
        ("gamma/reasoning-pro", "gamma"),
        ("deepseek/reasoning-pro", "deepseek"),
        ("beta/reasoning-max", "beta"),
        ("tau/reasoning-ultra", "tau"),
    )
    for row, (model_id, company) in zip(
        plan["selected_models"], primary, strict=True
    ):
        row["model"] = model_id
        row["company"] = company
    plan["recovery_models"][0]["model"] = "rho/reasoning-pro"
    plan["recovery_models"][0]["company"] = "rho"
    plan["recovery_models"].extend(
        [
            recovery(2, "epsilon", 0.06),
            recovery(3, "zeta", 0.07),
            recovery(4, "eta", 0.08),
        ]
    )
    plan["recovery_count"] = 4
    value["approved_budget"]["calls"] = 8
    value["approved_budget"]["maximum_recovery_calls"] = 4
    plan["catalog_fetch_mode"] = "live-per-task-no-cross-task-cache"
    plan["company_uniqueness_scope"] = "selected-and-recovery"
    plan["company_model_policy"] = (
        "one-highest-intelligence-verified-reasoning-flagship-"
        "per-company-then-price-rank"
    )
    plan["flagship_definition"] = FLAGSHIP_DEFINITION
    plan["reasoning_model_required"] = True
    plan["benchmark_source"] = BENCHMARK_SOURCE
    plan["price_rank_basis"] = (
        "prompt_usd_per_million + completion_usd_per_million"
    )
    plan["endpoint_qualification_performed_by_governance"] = True
    plan["model_calls"] = 0
    all_rows = plan["selected_models"] + plan["recovery_models"]
    for index, row in enumerate(all_rows):
        enrich_row(row, natural_top=index in {1, 5})
    ranked = [dict(row) for row in all_rows]
    ranked.sort(key=lambda row: row["price_rank_usd_per_million"])
    for rank, row in enumerate(ranked, 1):
        row["price_rank"] = rank
        row["slot"] = rank
        row.pop("role_id", None)
        row.pop("role_kind", None)
        row.pop("role", None)
    plan["price_ranked_models"] = ranked
    resign(value)
    return value


class UniqueCompanyPriceRankingTests(unittest.TestCase):
    def test_valid_benchmarked_reasoning_flagship_plan_passes(self) -> None:
        validate_governance_model_plan(ticket())

    def test_live_plan_requires_price_ranking(self) -> None:
        value = ticket()
        value["governance_model_plan"].pop("price_ranked_models")
        resign(value)
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
        resign(value)
        with self.assertRaisesRegex(
            GovernanceModelPlanError, "ascending price order"
        ):
            validate_governance_model_plan(value)

    def test_live_plan_requires_reasoning_models(self) -> None:
        value = ticket()
        value["governance_model_plan"]["reasoning_model_required"] = False
        resign(value)
        with self.assertRaisesRegex(
            GovernanceModelPlanError, "reasoning_model_required"
        ):
            validate_governance_model_plan(value)

    def test_luna_is_rejected_even_with_forged_evidence(self) -> None:
        value = ticket()
        plan = value["governance_model_plan"]
        old_model = plan["selected_models"][0]["model"]
        for row in plan["selected_models"] + plan["price_ranked_models"]:
            if row["model"] == old_model:
                row["model"] = "openai/gpt-5.6-luna-pro"
                row["company"] = "openai"
        resign(value)
        with self.assertRaisesRegex(
            GovernanceModelPlanError, "economy-tier model"
        ):
            validate_governance_model_plan(value)

    def test_invalid_flagship_basis_is_rejected(self) -> None:
        value = ticket()
        value["governance_model_plan"]["selected_models"][0][
            "flagship_basis"
        ] = "name-contains-pro"
        resign(value)
        with self.assertRaisesRegex(
            GovernanceModelPlanError, "flagship_basis is invalid"
        ):
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
        value["governance_model_plan"]["price_ranked_models"][0][
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
