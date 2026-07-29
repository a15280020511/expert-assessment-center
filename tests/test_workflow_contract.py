import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "execution-ticket.yml"


class WorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_total_call_budget_is_forwarded_to_runtime(self):
        self.assertIn("TOTAL_MODEL_CALLS: ${{ steps.prepare.outputs.calls }}", self.text)
        self.assertNotIn("EXPERT_MAX_REPLACEMENTS: ${{ steps.prepare.outputs.maximum_replacements }}", self.text)

    def test_production_uses_hardened_entrypoints(self):
        self.assertIn("issue_ticket_hardened.py prepare", self.text)
        self.assertIn("expert_team_hardened.py", self.text)
        self.assertIn("execution_auditor.py", self.text)
        self.assertIn("final_status.py", self.text)

    def test_only_issue_open_and_controlled_retry_trigger_execution(self):
        self.assertNotIn("github.event.comment.body == '/run-expert-team'", self.text)
        self.assertIn("startsWith(github.event.comment.body, '/retry-expert-team ')", self.text)

    def test_production_does_not_silently_queue_distinct_tasks(self):
        self.assertNotIn("group: expert-team-production", self.text)
        self.assertNotIn("cancel-in-progress: false", self.text)
        self.assertIn("issue_ticket_hardened.py prepare", self.text)

    def test_report_and_audit_precede_manifest_and_artifact_upload(self):
        report = self.text.index("name: Publish and verify full report")
        audit = self.text.index("name: Run deterministic execution audit")
        refresh = self.text.index("name: Refresh final artifact manifest")
        upload = self.text.index("name: Upload ticket artifacts")
        final = self.text.index("name: Publish authoritative final status")
        self.assertLess(report, audit)
        self.assertLess(audit, refresh)
        self.assertLess(refresh, upload)
        self.assertLess(upload, final)

    def test_report_idempotency_trusts_only_actions_bot(self):
        self.assertIn('select(.user.login == "github-actions[bot]")', self.text)
        self.assertIn("grep -Fqx \"$marker\"", self.text)
        self.assertIn("report_sha256", self.text)

    def test_missing_secret_and_delivery_failures_are_visible_job_failures(self):
        marker = self.text.index("name: Mark failed execution")
        tail = self.text[marker:]
        self.assertIn("steps.prepare.outputs.accepted == 'true'", tail)
        self.assertIn("steps.secret.outputs.present != 'true'", tail)
        self.assertIn("steps.final.outcome == 'failure'", tail)
        self.assertIn("steps.final.outputs.status == 'FAIL'", tail)
        self.assertIn("run: exit 1", tail)

    def test_production_has_no_hard_monetary_ceiling(self):
        self.assertNotIn("MAX_ESTIMATED_COST_USD", self.text)
        self.assertNotIn("--max-estimated-cost-usd", self.text)
        self.assertIn("TOTAL_MODEL_CALLS", self.text)

    def test_history_restore_is_optional_validated_and_audited(self):
        marker = self.text.index("name: Restore latest model performance history")
        end = self.text.index("name: Execute fixed 3+1 expert team")
        block = self.text[marker:end]
        self.assertIn("2>/dev/null || true", block)
        self.assertIn("python -m json.tool", block)
        self.assertIn("history-restore.json", block)
        self.assertIn("fallback_used", block)
        self.assertIn("no-prior-artifact", block)
        self.assertIn("download-failed", block)
        self.assertIn("invalid-artifact", block)


if __name__ == "__main__":
    unittest.main()
