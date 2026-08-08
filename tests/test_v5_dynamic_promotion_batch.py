from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
if str(MARKET) not in sys.path:
    sys.path.insert(0, str(MARKET))

from v5_priority_preserving_heterogeneity import dynamic_company_entropy_batch  # noqa: E402


class DynamicPromotionBatchTests(unittest.TestCase):
    def test_run396_shape_does_not_expand_to_twenty_six_promotions(self) -> None:
        depth = dynamic_company_entropy_batch(
            parent_depth=26,
            eligible_count=199,
            distinct_company_count=35,
            feedback_pressure=1.0,
        )
        self.assertEqual(6, depth)

    def test_batch_is_current_pool_and_feedback_derived(self) -> None:
        broad = dynamic_company_entropy_batch(
            parent_depth=20,
            eligible_count=100,
            distinct_company_count=31,
            feedback_pressure=1.0,
        )
        narrow = dynamic_company_entropy_batch(
            parent_depth=20,
            eligible_count=100,
            distinct_company_count=3,
            feedback_pressure=0.5,
        )
        self.assertGreater(broad, narrow)
        self.assertGreaterEqual(narrow, 1)

    def test_parent_structural_depth_remains_an_upper_bound(self) -> None:
        self.assertEqual(
            2,
            dynamic_company_entropy_batch(
                parent_depth=2,
                eligible_count=199,
                distinct_company_count=100,
                feedback_pressure=1.0,
            ),
        )


if __name__ == "__main__":
    unittest.main()
