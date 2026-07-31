import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import GraphLimits  # noqa: E402


class V5ConstitutionDefaultTests(unittest.TestCase):
    def test_default_delivery_is_complete_and_fail_closed(self):
        limits = GraphLimits()
        self.assertEqual(limits.min_required_work_coverage, 1.0)
        self.assertFalse(limits.allow_degraded_success)

    def test_emergency_ceilings_do_not_define_task_topology(self):
        limits = GraphLimits()
        self.assertGreaterEqual(limits.max_nodes, 1)
        self.assertGreaterEqual(limits.max_model_calls, 1)
        self.assertNotEqual(limits.max_nodes, limits.min_successful_content_nodes)


if __name__ == "__main__":
    unittest.main()
