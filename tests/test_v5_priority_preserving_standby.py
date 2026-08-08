from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
if str(MARKET) not in sys.path:
    sys.path.insert(0, str(MARKET))

from v5_priority_preserving_heterogeneity import (  # noqa: E402
    first_ranked_eligible_standby,
)


class PriorityPreservingStandbyTests(unittest.TestCase):
    def test_claim_takes_first_ranked_candidate_even_if_company_was_used(self) -> None:
        inventory = [
            {
                "model": "aion-labs/high-quality-repeat",
                "estimated_quality": 0.95,
                "failure_probability": 0.05,
            },
            {
                "model": "anthropic/lower-quality-new-company",
                "estimated_quality": 0.70,
                "failure_probability": 0.20,
            },
        ]
        chosen = first_ranked_eligible_standby(inventory, set(), set())
        self.assertIsNotNone(chosen)
        self.assertEqual("aion-labs/high-quality-repeat", chosen["model"])

    def test_claim_skips_claimed_and_hard_failed_models_without_reordering_rest(self) -> None:
        inventory = [
            {"model": "openai/already-claimed"},
            {"model": "deepseek/hard-failed"},
            {"model": "anthropic/first-eligible"},
            {"model": "google/later-eligible"},
        ]
        chosen = first_ranked_eligible_standby(
            inventory,
            {"openai/already-claimed"},
            {"deepseek/hard-failed"},
        )
        self.assertIsNotNone(chosen)
        self.assertEqual("anthropic/first-eligible", chosen["model"])

    def test_empty_eligible_set_returns_none(self) -> None:
        self.assertIsNone(
            first_ranked_eligible_standby(
                [{"model": "openai/claimed"}],
                {"openai/claimed"},
                set(),
            )
        )


if __name__ == "__main__":
    unittest.main()
