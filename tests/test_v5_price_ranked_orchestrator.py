from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from v5_price_ranked_orchestrator import (  # noqa: E402
    PriceRankedOrchestrationError,
    build_price_ranked_proposal,
    rank_endpoints,
)


def endpoint(
    model: str,
    provider: str,
    company: str,
    *,
    rank: int,
    prompt: float,
    completion: float,
    max_completion_tokens: int = 8192,
    synthetic: bool = False,
):
    return {
        "model": model,
        "provider": provider,
        "company": company,
        "provider_endpoint": f"{model}@{provider}",
        "official_intelligence_rank": rank,
        "context_length": 131072,
        "max_completion_tokens": max_completion_tokens,
        "prompt_price_per_million": prompt,
        "completion_price_per_million": completion,
        "supported_parameters": ["max_tokens", "temperature"],
        "synthetic_fixture_only": synthetic,
    }


class PriceRankedOrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.envelope = {
            "task_characters": 2000,
            "required_context_tokens": 16000,
            "completion_capacity_advisory_tokens": 4096,
        }
        self.catalog = {
            "endpoints": [
                endpoint(
                    "cheap-a/model",
                    "p1",
                    "cheap-a",
                    rank=12,
                    prompt=0.05,
                    completion=0.1,
                ),
                endpoint(
                    "cheap-a/model-2",
                    "p2",
                    "cheap-a",
                    rank=8,
                    prompt=0.06,
                    completion=0.12,
                ),
                endpoint(
                    "cheap-b/model",
                    "p3",
                    "cheap-b",
                    rank=20,
                    prompt=0.07,
                    completion=0.14,
                ),
                endpoint(
                    "quality-c/model",
                    "p4",
                    "quality-c",
                    rank=2,
                    prompt=0.08,
                    completion=0.16,
                ),
                endpoint(
                    "quality-d/model",
                    "p5",
                    "quality-d",
                    rank=5,
                    prompt=0.09,
                    completion=0.18,
                ),
                endpoint(
                    "reserve-e/model",
                    "p6",
                    "reserve-e",
                    rank=9,
                    prompt=0.10,
                    completion=0.20,
                ),
                endpoint(
                    "reserve-f/model",
                    "p7",
                    "reserve-f",
                    rank=11,
                    prompt=0.11,
                    completion=0.22,
                ),
            ]
        }

    def test_cheapest_distinct_company_set_is_selected(self) -> None:
        proposal, audit = build_price_ranked_proposal(
            catalog=self.catalog,
            task_envelope=self.envelope,
            expert_count=4,
            recovery_calls=1,
        )
        candidate_set = audit["cheapest_candidate_set"]
        costs = [
            row["estimated_call_cost_usd"] for row in candidate_set
        ]
        companies = [row["company"] for row in candidate_set]
        self.assertEqual(costs, sorted(costs))
        self.assertEqual(len(companies), len(set(companies)))
        self.assertEqual(4, len(proposal["nodes"]))
        self.assertEqual(
            ["expert-final-synthesis"],
            proposal["final_nodes"],
        )
        final = next(
            row
            for row in proposal["nodes"]
            if row["node_id"] == "expert-final-synthesis"
        )
        self.assertEqual("quality-c/model", final["model"])
        self.assertEqual(1, len(final["recovery"]))
        self.assertEqual(0, audit["claude_calls"])
        self.assertEqual(0, audit["gpt_selection_calls"])
        self.assertTrue(
            audit["networkx_used_for_dag_validation"]
        )

    def test_recovery_selection_skips_cheaper_insufficient_capacity(self) -> None:
        catalog = {
            "endpoints": [
                endpoint(
                    f"expert-{index}/model",
                    f"p{index}",
                    f"expert-{index}",
                    rank=index,
                    prompt=0.001 * index,
                    completion=0.002 * index,
                )
                for index in range(1, 5)
            ]
            + [
                endpoint(
                    "cheap-low-cap/model",
                    "p5",
                    "cheap-low-cap",
                    rank=5,
                    prompt=0.05,
                    completion=0.10,
                    max_completion_tokens=2048,
                ),
                endpoint(
                    "capable-reserve/model",
                    "p6",
                    "capable-reserve",
                    rank=6,
                    prompt=0.06,
                    completion=0.12,
                    max_completion_tokens=8192,
                ),
            ]
        }
        proposal, audit = build_price_ranked_proposal(
            catalog=catalog,
            task_envelope=self.envelope,
            expert_count=4,
            recovery_calls=1,
        )
        self.assertEqual(
            6144,
            audit["recovery_native_output_floor_tokens"],
        )
        self.assertEqual(
            ["capable-reserve/model"],
            [row["model"] for row in audit["recovery_endpoints"]],
        )
        attached = [
            recovery["model"]
            for node in proposal["nodes"]
            for recovery in node["recovery"]
        ]
        self.assertEqual(["capable-reserve/model"], attached)

    def test_insufficient_capable_recovery_pool_fails_closed(self) -> None:
        catalog = {
            "endpoints": [
                endpoint(
                    f"expert-{index}/model",
                    f"p{index}",
                    f"expert-{index}",
                    rank=index,
                    prompt=0.001 * index,
                    completion=0.002 * index,
                )
                for index in range(1, 5)
            ]
            + [
                endpoint(
                    "low-cap/model",
                    "p5",
                    "low-cap",
                    rank=5,
                    prompt=0.05,
                    completion=0.10,
                    max_completion_tokens=2048,
                )
            ]
        }
        with self.assertRaisesRegex(
            PriceRankedOrchestrationError,
            "provider-native output capacity",
        ):
            build_price_ranked_proposal(
                catalog=catalog,
                task_envelope=self.envelope,
                expert_count=4,
                recovery_calls=1,
            )

    def test_synthetic_fixture_requires_explicit_dry_run_permission(
        self,
    ) -> None:
        catalog = {
            "endpoints": [
                endpoint(
                    f"c{i}/m",
                    f"p{i}",
                    f"c{i}",
                    rank=i,
                    prompt=0.01 * i,
                    completion=0.02 * i,
                    synthetic=True,
                )
                for i in range(1, 5)
            ]
        }
        with self.assertRaises(PriceRankedOrchestrationError):
            rank_endpoints(catalog, self.envelope)
        rows = rank_endpoints(
            catalog,
            self.envelope,
            allow_synthetic_fixture=True,
        )
        self.assertEqual(4, len(rows))

    def test_insufficient_distinct_companies_fails_closed(self) -> None:
        catalog = {
            "endpoints": [
                endpoint(
                    f"same/m{i}",
                    f"p{i}",
                    "same",
                    rank=i,
                    prompt=0.01,
                    completion=0.02,
                )
                for i in range(1, 7)
            ]
        }
        with self.assertRaisesRegex(
            PriceRankedOrchestrationError,
            "distinct model companies",
        ):
            build_price_ranked_proposal(
                catalog=catalog,
                task_envelope=self.envelope,
                expert_count=3,
                recovery_calls=0,
            )


if __name__ == "__main__":
    unittest.main()
