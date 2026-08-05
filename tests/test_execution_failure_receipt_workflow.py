import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "execution-failure-receipt.yml"


class ExecutionFailureReceiptWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_monitors_only_failed_owner_issue_comment_runs(self):
        self.assertIn("workflow_run:", self.text)
        self.assertIn("Execution Ticket Price-Ranked Expert Team V5", self.text)
        self.assertIn("github.event.workflow_run.event == 'issue_comment'", self.text)
        self.assertIn("github.event.workflow_run.conclusion == 'failure'", self.text)
        self.assertIn("github.event.workflow_run.actor.login == github.repository_owner", self.text)

    def test_resolves_the_exact_execution_issue(self):
        self.assertIn("issue.title === title", self.text)
        self.assertIn("gov-\\d+-expert", self.text)
        self.assertIn('includes(`"task_id": "${taskId}"`)', self.text)

    def test_does_not_duplicate_a_trusted_terminal_receipt(self):
        self.assertIn("github-actions[bot]", self.text)
        for heading in (
            "EXECUTION_COMPLETED",
            "EXECUTION_FAILED",
            "EXECUTION_DEGRADED",
            "EXECUTION_REJECTED",
        ):
            self.assertIn(heading, self.text)
        self.assertIn("trustedTerminalExists", self.text)

    def test_failure_is_published_as_a_machine_readable_terminal(self):
        self.assertIn("## EXECUTION_REJECTED", self.text)
        self.assertIn("CHILD_WORKFLOW_FAILED_BEFORE_TERMINAL_RECEIPT", self.text)
        self.assertIn("Business success claimed: `false`", self.text)
        self.assertIn("Model-call evidence: `not attested`", self.text)
        self.assertIn("issues.createComment", self.text)


if __name__ == "__main__":
    unittest.main()
