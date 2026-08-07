from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_governance_model_plan import (  # noqa: E402
    plan_sha256,
    task_sha256,
    validate_governance_model_plan,
)

FIXTURE = ROOT / "tests" / "fixtures" / "governance-ticket.json"


def resign(value: dict) -> None:
    plan = value["governance_model_plan"]
    plan["task_sha256"] = task_sha256(value)
    plan["plan_sha256"] = plan_sha256(plan)


def ticket() -> dict:
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    plan = value["governance_model_plan"]
    # Historical qualification metadata is retained solely to prove that v9
    # treats it as compatibility/advisory data rather than an admission gate.
    plan["price_ranked_models"] = [
        dict(row) for row in plan["selected_models"] + plan["recovery_models"]
    ]
    plan["reasoning_model_required"] = True
    plan["flagship_definition"] = "legacy-fixture-only"
    plan["benchmark_source"] = "legacy-fixture-only"
    plan["company_uniqueness_scope"] = "legacy-fixture-only"
    for rank, row in enumerate(plan["price_ranked_models"], 1):
        row["price_rank"] = rank
        row["flagship_basis"] = "legacy-fixture-only"
        row["benchmark_source"] = "legacy-fixture-only"
        row["benchmark_evidence_sha256"] = f"{rank:064x}"[-64:]
        row["selection_evidence"] = "legacy-fixture-only"
    resign(value)
    return value


class DynamicNonGatingCompatibilityTests(unittest.TestCase):
    def test_legacy_qualification_metadata_does_not_block_execution(self) -> None:
        value = ticket()
        plan = validate_governance_model_plan(value)
        self.assertEqual(plan["plan_sha256"], value["governance_model_plan"]["plan_sha256"])
        self.assertEqual(plan["company_uniqueness_scope"], "legacy-fixture-only")
        self.assertEqual(plan["flagship_definition"], "legacy-fixture-only")

    def test_price_ranking_metadata_is_optional(self) -> None:
        value = ticket()
        value["governance_model_plan"].pop("price_ranked_models")
        resign(value)
        validate_governance_model_plan(value)

    def test_price_order_is_not_an_admission_gate(self) -> None:
        value = ticket()
        rows = value["governance_model_plan"]["price_ranked_models"]
        rows.reverse()
        resign(value)
        validate_governance_model_plan(value)

    def test_reasoning_flag_metadata_is_not_an_admission_gate(self) -> None:
        value = ticket()
        value["governance_model_plan"]["reasoning_model_required"] = False
        resign(value)
        validate_governance_model_plan(value)

    def test_model_name_tier_labels_are_not_an_admission_gate(self) -> None:
        value = ticket()
        plan = value["governance_model_plan"]
        plan["selected_models"][0]["model"] = "openai/example-luna-pro"
        resign(value)
        validated = validate_governance_model_plan(value)
        self.assertEqual(validated["selected_models"][0]["model"], "openai/example-luna-pro")

    def test_flagship_basis_is_advisory_only(self) -> None:
        value = ticket()
        value["governance_model_plan"]["selected_models"][0][
            "flagship_basis"
        ] = "arbitrary-advisory-label"
        resign(value)
        validate_governance_model_plan(value)

    def test_benchmark_evidence_is_not_required_for_admission(self) -> None:
        value = ticket()
        value["governance_model_plan"]["selected_models"][0].pop(
            "benchmark_evidence_sha256", None
        )
        resign(value)
        validate_governance_model_plan(value)

    def test_benchmark_source_does_not_gate_execution(self) -> None:
        value = ticket()
        value["governance_model_plan"]["selected_models"][0][
            "benchmark_source"
        ] = "untrusted-advisory-label"
        resign(value)
        validate_governance_model_plan(value)

    def test_selection_evidence_strings_do_not_gate_execution(self) -> None:
        value = ticket()
        value["governance_model_plan"]["selected_models"][0][
            "selection_evidence"
        ] = ""
        resign(value)
        validate_governance_model_plan(value)

    def test_same_company_models_are_allowed_but_exact_model_duplicates_are_not(self) -> None:
        value = ticket()
        selected = value["governance_model_plan"]["selected_models"]
        selected[1]["company"] = selected[0]["company"]
        resign(value)
        plan = validate_governance_model_plan(value)
        self.assertEqual(
            plan["selected_models"][0]["company"],
            plan["selected_models"][1]["company"],
        )


if __name__ == "__main__":
    unittest.main()
