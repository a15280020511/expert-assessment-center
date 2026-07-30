import unittest
from pathlib import Path


class TestV5LowCostPilotWorkflow(unittest.TestCase):
    def test_workflow_exists_and_validates_before_secret_or_models(self):
        path = Path(".github/workflows/v5-low-cost-pilot.yml")
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        self.assertIn("startsWith(github.event.issue.title, '[v5-pilot]')", text)
        self.assertIn("github.actor == github.repository_owner", text)
        validate = text.index("Run zero-cost repository validation")
        secret = text.index("Check OpenRouter secret")
        execute = text.index("Run bounded low-cost pilot")
        self.assertLess(validate, secret)
        self.assertLess(secret, execute)
        self.assertIn("python -m unittest discover -s tests -v", text)
        self.assertIn("python -m ruff check --select E9,F63,F7,F82", text)
        self.assertIn("python -m py_compile open-model-market/*.py", text)
        self.assertIn("steps.validate.outcome == 'success'", text)

    def test_workflow_has_bounded_and_non_production_contract(self):
        text = Path(".github/workflows/v5-low-cost-pilot.yml").read_text(encoding="utf-8")
        self.assertIn("v5_low_cost_pilot.py prepare", text)
        self.assertIn("v5_low_cost_pilot.py credit-check", text)
        self.assertIn("v5_low_cost_pilot_entry.py run", text)
        self.assertIn("Production cutover eligibility: `false`", text)
        self.assertIn("Production entrypoint changed: `false`", text)
        self.assertIn("Model inference calls: `0`", text)
        self.assertNotIn("pull_request_target", text)
        self.assertNotIn("workflow_run:", text)
        self.assertNotIn("contents: write", text)


if __name__ == "__main__":
    unittest.main()
