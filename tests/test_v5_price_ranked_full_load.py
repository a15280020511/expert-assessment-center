from __future__ import annotations

import copy
import json
import math
import random
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import networkx as nx

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from v5_price_ranked_orchestrator import (  # noqa: E402
    PriceRankedOrchestrationError,
    build_price_ranked_proposal,
)


def catalog_endpoint(
    index: int,
    *,
    author: str | None = None,
    company: str | None = None,
    rank: int | None = None,
    context_length: int = 131_072,
    completion_tokens: int = 8_192,
    prompt_price: float | str = 0.05,
    completion_price: float | str = 0.10,
) -> dict[str, object]:
    model_author = author or f"vendor-{index}"
    declared_company = company if company is not None else model_author
    model = f"{model_author}/model-{index}"
    provider = f"provider-{index % 23}"
    return {
        "model": model,
        "provider": provider,
        "company": declared_company,
        "provider_endpoint": f"{model}@{provider}",
        "official_intelligence_rank": rank or (index % 150) + 1,
        "context_length": context_length,
        "max_completion_tokens": completion_tokens,
        "prompt_price_per_million": prompt_price,
        "completion_price_per_million": completion_price,
        "supported_parameters": ["max_tokens", "temperature"],
        "input_modalities": ["text"],
        "output_modalities": ["text"],
    }


def task_envelope() -> dict[str, int]:
    return {
        "task_characters": 24_000,
        "required_context_tokens": 65_536,
        "completion_capacity_advisory_tokens": 8_192,
    }


def proposal_signature(proposal: dict[str, object], audit: dict[str, object]) -> str:
    value = {
        "nodes": proposal["nodes"],
        "edges": proposal["edges"],
        "final_nodes": proposal["final_nodes"],
        "chosen": audit["cheapest_candidate_set"],
        "recoveries": audit["recovery_endpoints"],
    }
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class PriceRankedFullLoadTests(unittest.TestCase):
    def test_all_team_sizes_and_recovery_reserves_are_fully_materialized(self) -> None:
        catalog = {
            "endpoints": [
                catalog_endpoint(
                    index,
                    prompt_price=0.01 + index / 100_000,
                    completion_price=0.02 + index / 50_000,
                )
                for index in range(1, 40)
            ]
        }
        for expert_count in range(3, 7):
            for recovery_calls in range(0, 11):
                with self.subTest(
                    expert_count=expert_count,
                    recovery_calls=recovery_calls,
                ):
                    proposal, audit = build_price_ranked_proposal(
                        catalog=catalog,
                        task_envelope=task_envelope(),
                        expert_count=expert_count,
                        recovery_calls=recovery_calls,
                    )
                    attached = sum(
                        len(node["recovery"])
                        for node in proposal["nodes"]
                    )
                    self.assertEqual(recovery_calls, attached)
                    self.assertEqual(recovery_calls, audit["attached_recovery_count"])
                    self.assertEqual(
                        recovery_calls,
                        sum(audit["recovery_distribution"].values()),
                    )

    def test_generated_topology_is_a_bounded_three_stage_dag(self) -> None:
        catalog = {
            "endpoints": [catalog_endpoint(index) for index in range(1, 20)]
        }
        for expert_count in range(3, 7):
            proposal, _ = build_price_ranked_proposal(
                catalog=catalog,
                task_envelope=task_envelope(),
                expert_count=expert_count,
                recovery_calls=2,
            )
            graph = nx.DiGraph()
            graph.add_nodes_from(node["node_id"] for node in proposal["nodes"])
            graph.add_edges_from(
                (edge["source"], edge["target"])
                for edge in proposal["edges"]
            )
            self.assertTrue(nx.is_directed_acyclic_graph(graph))
            self.assertEqual(2 * expert_count - 3, graph.number_of_edges())
            self.assertEqual(0, graph.out_degree("expert-final-synthesis"))
            generations = [set(row) for row in nx.topological_generations(graph)]
            self.assertEqual(3, len(generations))
            self.assertEqual({"expert-cross-review"}, generations[1])
            self.assertEqual({"expert-final-synthesis"}, generations[2])
            for node_id in graph:
                if node_id != "expert-final-synthesis":
                    self.assertTrue(
                        nx.has_path(graph, node_id, "expert-final-synthesis")
                    )

    def test_large_catalog_remains_fast_and_deterministic(self) -> None:
        rows = [
            catalog_endpoint(
                index,
                prompt_price=0.001 + (index % 500) / 100_000,
                completion_price=0.002 + (index % 500) / 50_000,
            )
            for index in range(1, 20_001)
        ]
        started = time.perf_counter()
        proposal, audit = build_price_ranked_proposal(
            catalog={"endpoints": rows},
            task_envelope=task_envelope(),
            expert_count=6,
            recovery_calls=10,
        )
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 5.0)
        self.assertEqual(20_000, audit["ranked_endpoint_count"])
        self.assertEqual(16, 6 + audit["attached_recovery_count"])
        baseline = proposal_signature(proposal, audit)
        for seed in range(30):
            shuffled = list(rows)
            random.Random(seed).shuffle(shuffled)
            candidate, candidate_audit = build_price_ranked_proposal(
                catalog={"endpoints": shuffled},
                task_envelope=task_envelope(),
                expert_count=6,
                recovery_calls=10,
            )
            self.assertEqual(baseline, proposal_signature(candidate, candidate_audit))

    def test_concurrent_builds_have_no_shared_state_or_input_mutation(self) -> None:
        catalog = {
            "endpoints": [
                catalog_endpoint(
                    index,
                    prompt_price=0.01 + (index % 100) / 10_000,
                    completion_price=0.02 + (index % 100) / 5_000,
                )
                for index in range(1, 1_501)
            ]
        }
        original = copy.deepcopy(catalog)

        def build(_: int) -> str:
            proposal, audit = build_price_ranked_proposal(
                catalog=catalog,
                task_envelope=task_envelope(),
                expert_count=6,
                recovery_calls=6,
            )
            return proposal_signature(proposal, audit)

        with ThreadPoolExecutor(max_workers=32) as pool:
            signatures = list(pool.map(build, range(256)))
        self.assertEqual(1, len(set(signatures)))
        self.assertEqual(original, catalog)

    def test_company_alias_spoofing_fails_closed(self) -> None:
        rows = [
            catalog_endpoint(1, author="same-vendor", company="fake-a"),
            catalog_endpoint(2, author="same-vendor", company="fake-b"),
            catalog_endpoint(3),
            catalog_endpoint(4),
        ]
        with self.assertRaisesRegex(
            PriceRankedOrchestrationError,
            "identity or capacity",
        ):
            build_price_ranked_proposal(
                catalog={"endpoints": rows},
                task_envelope=task_envelope(),
                expert_count=3,
            )

    def test_invalid_prices_never_become_zero_cost_candidates(self) -> None:
        invalid_prices: tuple[object, ...] = (
            -1,
            "invalid",
            math.nan,
            math.inf,
            -math.inf,
        )
        for value in invalid_prices:
            with self.subTest(value=value):
                rows = [
                    catalog_endpoint(1, prompt_price=value),
                    catalog_endpoint(2),
                    catalog_endpoint(3),
                    catalog_endpoint(4),
                ]
                with self.assertRaisesRegex(
                    PriceRankedOrchestrationError,
                    "invalid pricing",
                ):
                    build_price_ranked_proposal(
                        catalog={"endpoints": rows},
                        task_envelope=task_envelope(),
                        expert_count=3,
                    )

    def test_zero_price_is_valid_when_explicit_and_finite(self) -> None:
        rows = [
            catalog_endpoint(1, prompt_price=0.0, completion_price=0.0),
            catalog_endpoint(2),
            catalog_endpoint(3),
            catalog_endpoint(4),
        ]
        _, audit = build_price_ranked_proposal(
            catalog={"endpoints": rows},
            task_envelope=task_envelope(),
            expert_count=3,
        )
        self.assertEqual(0.0, audit["cheapest_candidate_set"][0]["estimated_call_cost_usd"])

    def test_capacity_rank_identity_and_negative_recovery_are_rejected(self) -> None:
        invalid_rows = (
            catalog_endpoint(1, context_length=4_096),
            catalog_endpoint(1, completion_tokens=128),
            catalog_endpoint(1, rank=151),
        )
        for row in invalid_rows:
            with self.subTest(row=row):
                rows = [row, catalog_endpoint(2), catalog_endpoint(3), catalog_endpoint(4)]
                with self.assertRaises(PriceRankedOrchestrationError):
                    build_price_ranked_proposal(
                        catalog={"endpoints": rows},
                        task_envelope=task_envelope(),
                        expert_count=3,
                    )
        with self.assertRaisesRegex(
            PriceRankedOrchestrationError,
            "non-negative",
        ):
            build_price_ranked_proposal(
                catalog={"endpoints": [catalog_endpoint(i) for i in range(1, 8)]},
                task_envelope=task_envelope(),
                expert_count=3,
                recovery_calls=-1,
            )


if __name__ == "__main__":
    unittest.main()
