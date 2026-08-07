from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from model_market import ExpertTeamError, build_run_config  # noqa: E402


class DynamicRankingLimitTests(unittest.TestCase):
    def test_large_positive_ranking_limit_is_advisory_not_a_hard_gate(self) -> None:
        run = build_run_config(
            SimpleNamespace(
                task="dynamic candidate inventory",
                ranking_limit=1_000_000,
            )
        )
        self.assertEqual(run.ranking_limit, 1_000_000)

    def test_non_positive_ranking_limit_is_rejected_as_structurally_invalid(self) -> None:
        for value in (0, -1):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ExpertTeamError, "must be positive"):
                    build_run_config(
                        SimpleNamespace(
                            task="dynamic candidate inventory",
                            ranking_limit=value,
                        )
                    )


if __name__ == "__main__":
    unittest.main()
