import unittest
from pathlib import Path


class TestPaidTestPause(unittest.TestCase):
    def test_scheduled_live_canary_requires_explicit_paid_test_opt_in(self):
        workflow = Path(".github/workflows/expert-team-canary.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("vars.PAID_TESTS_ENABLED == 'true'", workflow)
        self.assertIn('cron: "23 3 1 * *"', workflow)
        self.assertIn("TOTAL_MODEL_CALLS: \"4\"", workflow)

    def test_production_workflow_is_not_guarded_by_paid_test_switch(self):
        workflow = Path(".github/workflows/expert-team.yml").read_text(encoding="utf-8")
        self.assertNotIn("PAID_TESTS_ENABLED", workflow)


if __name__ == "__main__":
    unittest.main()
