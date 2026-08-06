from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_task_adaptive_scoring import (  # noqa: E402
    PRINCIPLES,
    build_role_metrics,
    build_task_demand_profile,
    dynamic_role_weights,
    role_token_profile,
)


def _candidates() -> list[dict]:
    return [
        {
            "model": "cheap/strong-enough",
            "company": "cheap",
            "prompt_usd_per_million": 0.20,
            "completion_usd_per_million": 0.50,
            "request_usd": 0.0,
            "official_intelligence_rank": 12,
            "popularity_rank": 3,
            "context_length": 131072,
            "max_completion_tokens": 32768,
            "required_context_tokens": 8192,
        },
        {
            "model": "premium/slightly-stronger",
            "company": "premium",
            "prompt_usd_per_million": 4.00,
            "completion_usd_per_million": 12.00,
            "request_usd": 0.0,
            "official_intelligence_rank": 10,
            "popularity_rank": 2,
            "context_length": 131072,
            "max_completion_tokens": 32768,
            "required_context_tokens": 8192,
        },
        {
            "model": "mid/balanced",
            "company": "mid",
            "prompt_usd_per_million": 0.80,
            "completion_usd_per_million": 1.50,
            "request_usd": 0.0,
            "official_intelligence_rank": 18,
            "popularity_rank": 1,
            "context_length": 65536,
            "max_completion_tokens": 16384,
            "required_context_tokens": 8192,
        },
    ]


def _simple_packet() -> dict:
    return {
        "task": {
            "question": "比较A和B并给出建议。",
            "requirements": ["给出结论"],
            "language": "zh-CN",
        },
        "evidence": [],
        "execution_acceptance": ["有最终建议"],
        "governance_model_plan": {"required_context_tokens": 8192},
    }


def _complex_packet() -> dict:
    return {
        "task": {
            "question": "X" * 12000,
            "requirements": [f"requirement-{index}" for index in range(12)],
            "required_outputs": [f"field-{index}" for index in range(8)],
            "language": "zh-CN",
        },
        "evidence": [{"text": "E" * 1200} for _ in range(8)],
        "execution_acceptance": [f"accept-{index}" for index in range(8)],
        "governance_model_plan": {"required_context_tokens": 16384},
    }


class TaskAdaptiveScoringTests(unittest.TestCase):
    def test_three_principles_are_machine_readable(self) -> None:
        profile = build_task_demand_profile(_simple_packet(), _candidates())
        self.assertEqual(profile["principles"], list(PRINCIPLES))
        self.assertFalse(profile["semantic_keyword_routing_used"])
        self.assertFalse(profile["domain_hardcoding_used"])
        self.assertFalse(profile["cross_task_history_used"])
        self.assertFalse(profile["provider_metric_used"])

    def test_complex_task_shifts_weight_from_cost_to_intelligence(self) -> None:
        simple = build_task_demand_profile(_simple_packet(), _candidates())
        complex_ = build_task_demand_profile(_complex_packet(), _candidates())
        self.assertGreater(
            complex_["pressure"]["overall"], simple["pressure"]["overall"]
        )
        simple_weights = dynamic_role_weights(simple, "synthesis")
        complex_weights = dynamic_role_weights(complex_, "synthesis")
        self.assertLess(complex_weights["task_cost"], simple_weights["task_cost"])
        self.assertGreater(
            complex_weights["intelligence"], simple_weights["intelligence"]
        )

    def test_downstream_roles_reserve_more_native_context(self) -> None:
        profile = build_task_demand_profile(_simple_packet(), _candidates())
        evidence = role_token_profile(profile, "evidence")
        review = role_token_profile(profile, "review")
        synthesis = role_token_profile(profile, "synthesis")
        self.assertLess(evidence["required_context_tokens"], review["required_context_tokens"])
        self.assertLess(review["required_context_tokens"], synthesis["required_context_tokens"])

    def test_small_effort_large_return_penalizes_expensive_tiny_gain(self) -> None:
        profile = build_task_demand_profile(_simple_packet(), _candidates())
        metrics = build_role_metrics(_candidates(), profile, "evidence")
        cheap = metrics["cheap/strong-enough"]
        premium = metrics["premium/slightly-stronger"]
        self.assertLess(
            cheap["ranks"]["marginal_return"],
            premium["ranks"]["marginal_return"],
        )
        self.assertLess(
            cheap["estimated_task_cost_usd"], premium["estimated_task_cost_usd"]
        )

    def test_role_specific_capacity_can_exclude_only_downstream_role(self) -> None:
        candidates = _candidates()
        candidates[0]["context_length"] = 11_000
        profile = build_task_demand_profile(_simple_packet(), candidates)
        evidence = build_role_metrics(candidates, profile, "evidence")
        synthesis = build_role_metrics(candidates, profile, "synthesis")
        self.assertTrue(evidence["cheap/strong-enough"]["compatible"])
        self.assertFalse(synthesis["cheap/strong-enough"]["compatible"])


if __name__ == "__main__":
    unittest.main()
