import unittest
from pathlib import Path


LEGACY_PAID_WORKFLOWS = (
    ".github/workflows/v5-live-benchmark.yml",
    ".github/workflows/v5-live-benchmark-final.yml",
    ".github/workflows/v5-low-cost-pilot.yml",
    ".github/workflows/v5-micro-canary.yml",
)


class TestV5LegacyPaidWorkflowsDisabled(unittest.TestCase):
    def test_legacy_paid_workflows_are_manual_disabled_stubs(self):
        for filename in LEGACY_PAID_WORKFLOWS:
            with self.subTest(filename=filename):
                path = Path(filename)
                self.assertTrue(path.is_file())
                text = path.read_text(encoding="utf-8")
                self.assertIn("workflow_dispatch:", text)
                self.assertIn("if: ${{ false }}", text)
                self.assertNotIn("issues:", text)
                self.assertNotIn("schedule:", text)
                self.assertNotIn("OPENROUTER_API_KEY", text)
                self.assertNotIn("OPENROUTER_MANAGEMENT_KEY", text)
                self.assertNotIn("secrets.", text)
                self.assertNotIn("credit-check", text)
                self.assertNotIn("v5_low_cost_pilot_entry.py run", text)
                self.assertNotIn("v5_live_benchmark", text)
                self.assertNotIn("v5_micro_canary.py run", text)

    def test_disabled_stubs_are_read_only_and_zero_call(self):
        for filename in LEGACY_PAID_WORKFLOWS:
            with self.subTest(filename=filename):
                text = Path(filename).read_text(encoding="utf-8")
                self.assertIn("contents: read", text)
                self.assertNotIn("contents: write", text)
                self.assertIn("no model call is made", text.casefold())
                self.assertNotIn("pull_request_target", text)
                self.assertNotIn("workflow_run:", text)


if __name__ == "__main__":
    unittest.main()
