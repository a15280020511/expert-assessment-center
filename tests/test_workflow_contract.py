import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "execution-ticket.yml"


class WorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_owner_comment_commands_trigger_dynamic_execution(self):
        self.assertIn("issue_comment:", self.text)
        self.assertIn("types: [created]", self.text)
        self.assertIn("github.actor == github.repository_owner", self.text)
        self.assertIn("/run-expert-team", self.text)
        self.assertIn("/retry-expert-team", self.text)

    def test_checkout_uses_current_main_without_production_sha_gate(self):
        self.assertIn("name: Checkout current main", self.text)
        self.assertIn("ref: main", self.text)
        self.assertNotIn("ref: production", self.text)
        self.assertNotIn('test "$main" = "$production"', self.text)
        self.assertNotIn("checked-out-production-sha.txt", self.text)

    def test_active_path_is_task_dynamic_and_gate_free(self):
        self.assertIn("v5_price_ranked_issue_ticket.py", self.text)
        self.assertIn("v5_dynamic_pipeline.py", self.text)
        for legacy_gate in (
            "v5_price_ranked_ticket_gate.py",
            "v5_price_ranked_production_ticket.py",
            "v5_admission_lock.py",
            "v5_price_ranked_independent_revalidation.py",
        ):
            self.assertNotIn(legacy_gate, self.text)
        self.assertNotIn("group: expert-production-admission", self.text)
        self.assertNotIn("group: expert-production-global", self.text)

    def test_dynamic_plan_executes_without_canary_or_artifact_qualification(self):
        build = self.text.index("name: Build task-dynamic expert plan")
        execute = self.text.index("name: Execute dynamic expert graph")
        publish = self.text.index("name: Publish whatever expert delivery exists")
        self.assertLess(build, execute)
        self.assertLess(execute, publish)
        self.assertNotIn("free-first", self.text.casefold())
        self.assertNotIn("canary", self.text.casefold())
        self.assertNotIn("independently revalidate", self.text.casefold())
        self.assertNotIn("only after audit", self.text.casefold())

    def test_transport_comment_fetch_is_fully_paginated(self):
        self.assertIn("Fetch all governance candidate transport comments", self.text)
        self.assertIn("gh api --paginate --slurp", self.text)
        self.assertIn("jq 'add // []'", self.text)
        self.assertIn('type == "array"', self.text)

    def test_result_publication_is_best_effort_not_fail_closed_gate(self):
        self.assertIn("continue-on-error: true", self.text)
        self.assertIn("if: always() && steps.ticket.outputs.accepted == 'true'", self.text)
        self.assertIn("Publish whatever expert delivery exists", self.text)
        self.assertIn("Upload execution evidence", self.text)

    def test_provider_routing_is_not_pinned_in_workflow(self):
        lowered = self.text.casefold()
        self.assertNotIn("provider.only", lowered)
        self.assertNotIn("provider.order", lowered)
        self.assertNotIn("require_parameters", lowered)
        self.assertNotIn("zdr", lowered)


if __name__ == "__main__":
    unittest.main()
