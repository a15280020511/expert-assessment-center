import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "execution-ticket.yml"


class WorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_dynamic_graph_call_ceiling_is_explicit(self):
        self.assertIn('TOTAL_MODEL_CALLS: "16"', self.text)
        self.assertIn("Execute hardened V5 R8 dynamic graph", self.text)
        self.assertNotIn("Execute fixed 3+1 expert team", self.text)

    def test_production_uses_only_v5_entrypoints(self):
        self.assertIn("v5_issue_ticket.py prepare", self.text)
        self.assertIn("v5_production_ticket.py", self.text)
        self.assertIn("v5_execution_auditor.py", self.text)
        self.assertIn("v5_final_status.py", self.text)
        self.assertNotIn("expert_team_hardened.py", self.text)
        self.assertNotIn("python open-model-market/execution_auditor.py", self.text)
        self.assertNotIn("python open-model-market/final_status.py", self.text)

    def test_only_issue_open_and_controlled_retry_trigger_execution(self):
        self.assertNotIn("github.event.comment.body == '/run-expert-team'", self.text)
        self.assertIn("startsWith(github.event.comment.body, '/retry-expert-team ')", self.text)

    def test_production_does_not_silently_queue_distinct_tasks(self):
        self.assertNotIn("group: expert-team-production", self.text)
        self.assertNotIn("cancel-in-progress: false", self.text)
        self.assertIn("v5_issue_ticket.py prepare", self.text)

    def test_report_and_audit_precede_manifest_and_artifact_upload(self):
        report = self.text.index("name: Publish and verify full V5 report")
        audit = self.text.index("name: Run deterministic V5 execution audit")
        refresh = self.text.index("name: Refresh final artifact manifest")
        upload = self.text.index("name: Upload ticket artifacts")
        final = self.text.index("name: Publish authoritative V5 final status")
        self.assertLess(report, audit)
        self.assertLess(audit, refresh)
        self.assertLess(refresh, upload)
        self.assertLess(upload, final)

    def test_report_idempotency_trusts_only_actions_bot(self):
        self.assertIn('select(.user.login == "github-actions[bot]")', self.text)
        self.assertIn('grep -Fqx "$marker"', self.text)
        self.assertIn("report_sha256", self.text)

    def test_missing_secret_and_delivery_failures_are_visible_job_failures(self):
        marker = self.text.index("name: Mark failed execution")
        tail = self.text[marker:]
        self.assertIn("steps.prepare.outputs.accepted == 'true'", tail)
        self.assertIn("steps.secret.outputs.present != 'true'", tail)
        self.assertIn("steps.final.outcome == 'failure'", tail)
        self.assertIn("steps.final.outputs.status == 'FAIL'", tail)
        self.assertIn("run: exit 1", tail)

    def test_production_has_no_fixed_dollar_ceiling(self):
        self.assertNotIn("MAX_ESTIMATED_COST_USD", self.text)
        self.assertNotIn("--max-estimated-cost-usd", self.text)
        self.assertIn("TOTAL_MODEL_CALLS", self.text)

    def test_no_manual_legacy_runtime_path_exists(self):
        legacy_version = "v" + "3"
        self.assertNotIn(legacy_version, self.text.casefold())
        self.assertNotIn("manual rollback", self.text.casefold())
        self.assertNotIn("Restore latest model performance history", self.text)
        self.assertNotIn("model-performance-state", self.text)


if __name__ == "__main__":
    unittest.main()
