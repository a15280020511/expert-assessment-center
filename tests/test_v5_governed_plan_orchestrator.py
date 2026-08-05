import json
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from v5_governed_plan_orchestrator import (  # noqa: E402
    GovernedPlanOrchestrationError,
    build_governed_proposal,
)
from v5_governance_model_plan import plan_sha256  # noqa: E402


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "governance-ticket.json"


def load_ticket() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def endpoint(
    model: str,
    provider: str,
    *,
    prompt: float = 1.0,
    completion: float = 3.0,
    context: int = 131_072,
    output: int = 16_000,
    rank: int = 1,
) -> dict:
    return {
        "model": model,
        "provider": provider,
        "provider_endpoint": f"{model}@{provider}",
        "official_intelligence_rank": rank,
        "context_length": context,
        "max_completion_tokens": output,
        "prompt_price_per_million": prompt,
        "completion_price_per_million": completion,
        "supported_parameters": ["max_tokens"],
        "input_modalities": ["text"],
        "output_modalities": ["text"],
    }


def catalog() -> dict:
    return {
        "endpoints": [
            endpoint("gamma/fast", "gamma-expensive", prompt=5, completion=15),
            endpoint("gamma/fast", "gamma-cheap", prompt=0.5, completion=1.5),
            endpoint("deepseek/china", "deepseek-cloud"),
            endpoint("beta/value", "beta-cloud"),
            endpoint("tau/math", "tau-cloud"),
            endpoint("rho/forecast", "rho-cloud"),
            endpoint("unused/model", "unused-cloud"),
        ]
    }


ENVELOPE = {
    "task_characters": 1200,
    "required_context_tokens": 65_536,
    "completion_capacity_advisory_tokens": 8_192,
}


class GovernedPlanOrchestratorTests(unittest.TestCase):
    def test_exact_governance_models_are_materialized(self) -> None:
        ticket = load_ticket()
        proposal, audit = build_governed_proposal(
            ticket=ticket,
            catalog=catalog(),
            task_envelope=ENVELOPE,
        )
        self.assertEqual(
            [row["model"] for row in proposal["nodes"]],
            [
                "gamma/fast",
                "deepseek/china",
                "beta/value",
                "tau/math",
            ],
        )
        self.assertEqual(proposal["nodes"][0]["provider"], "gamma-cheap")
        self.assertEqual(proposal["nodes"][-1]["node_id"], "expert-final-synthesis")
        self.assertEqual(
            proposal["nodes"][-1]["recovery"],
            [{"model": "rho/forecast", "provider": "rho-cloud"}],
        )
        self.assertEqual(audit["selection_authority"], "decision-system-governance")
        self.assertFalse(audit["model_selection_performed_locally"])
        self.assertFalse(audit["model_reranking_performed_locally"])
        self.assertFalse(audit["model_substitution_performed_locally"])
        self.assertTrue(audit["provider_resolution_performed_locally"])

    def test_catalog_permutation_does_not_change_result(self) -> None:
        ticket = load_ticket()
        baseline = build_governed_proposal(
            ticket=ticket,
            catalog=catalog(),
            task_envelope=ENVELOPE,
        )
        signatures = []
        for seed in range(20):
            rows = list(catalog()["endpoints"])
            random.Random(seed).shuffle(rows)
            signatures.append(
                json.dumps(
                    build_governed_proposal(
                        ticket=ticket,
                        catalog={"endpoints": rows},
                        task_envelope=ENVELOPE,
                    ),
                    sort_keys=True,
                    ensure_ascii=False,
                )
            )
        self.assertEqual(len(set(signatures)), 1)
        self.assertEqual(
            signatures[0],
            json.dumps(baseline, sort_keys=True, ensure_ascii=False),
        )

    def test_unavailable_planned_model_fails_without_substitution(self) -> None:
        rows = [
            row for row in catalog()["endpoints"] if row["model"] != "beta/value"
        ]
        rows.append(endpoint("alternative/model", "alternative-cloud"))
        with self.assertRaisesRegex(
            GovernedPlanOrchestrationError, "beta/value"
        ):
            build_governed_proposal(
                ticket=load_ticket(),
                catalog={"endpoints": rows},
                task_envelope=ENVELOPE,
            )

    def test_incompatible_context_fails_closed(self) -> None:
        rows = catalog()["endpoints"]
        for row in rows:
            if row["model"] == "tau/math":
                row["context_length"] = 8_192
        with self.assertRaisesRegex(GovernedPlanOrchestrationError, "tau/math"):
            build_governed_proposal(
                ticket=load_ticket(),
                catalog={"endpoints": rows},
                task_envelope=ENVELOPE,
            )

    def test_invalid_exact_provider_identity_is_ignored(self) -> None:
        rows = catalog()["endpoints"]
        rows.insert(
            0,
            {
                **endpoint("gamma/fast", "spoof", prompt=0, completion=0),
                "provider_endpoint": "other/model@spoof",
            },
        )
        proposal, _ = build_governed_proposal(
            ticket=load_ticket(),
            catalog={"endpoints": rows},
            task_envelope=ENVELOPE,
        )
        self.assertEqual(proposal["nodes"][0]["provider"], "gamma-cheap")

    def test_synthetic_endpoint_is_not_executable(self) -> None:
        rows = catalog()["endpoints"]
        for row in rows:
            if row["model"] == "deepseek/china":
                row["synthetic_fixture_only"] = True
        with self.assertRaisesRegex(GovernedPlanOrchestrationError, "deepseek/china"):
            build_governed_proposal(
                ticket=load_ticket(),
                catalog={"endpoints": rows},
                task_envelope=ENVELOPE,
            )

    def test_declared_company_must_match_model_namespace(self) -> None:
        ticket = load_ticket()
        plan = ticket["governance_model_plan"]
        plan["selected_models"][0]["company"] = "wrong-company"
        plan["plan_sha256"] = plan_sha256(plan)
        with self.assertRaisesRegex(
            GovernedPlanOrchestrationError, "company mismatch"
        ):
            build_governed_proposal(
                ticket=ticket,
                catalog=catalog(),
                task_envelope=ENVELOPE,
            )

    def test_recovery_output_capacity_is_checked(self) -> None:
        rows = catalog()["endpoints"]
        for row in rows:
            if row["model"] == "rho/forecast":
                row["max_completion_tokens"] = 1_024
        with self.assertRaisesRegex(
            GovernedPlanOrchestrationError, "recovery model lacks"
        ):
            build_governed_proposal(
                ticket=load_ticket(),
                catalog={"endpoints": rows},
                task_envelope=ENVELOPE,
            )

    def test_catalog_shape_is_required(self) -> None:
        with self.assertRaisesRegex(
            GovernedPlanOrchestrationError, "endpoint catalog is missing"
        ):
            build_governed_proposal(
                ticket=load_ticket(),
                catalog={},
                task_envelope=ENVELOPE,
            )


if __name__ == "__main__":
    unittest.main()
