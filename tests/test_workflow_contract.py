import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "execution-ticket.yml"


class WorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_only_explicit_comment_commands_trigger_execution(self):
        self.assertIn("issue_comment:", self.text)
        self.assertIn("types: [created]", self.text)
        self.assertIn("startsWith(github.event.comment.body, '/run-expert-team ')", self.text)
        self.assertIn("startsWith(github.event.comment.body, '/retry-expert-team ')", self.text)

    def test_both_jobs_checkout_frozen_production_ref(self):
        self.assertEqual(2, self.text.count("ref: production"))
        self.assertGreaterEqual(self.text.count('test "$main" = "$production"'), 2)
        self.assertGreaterEqual(self.text.count('test "$checked" = "$production"'), 2)
        self.assertIn("checked-out-production-sha.txt", self.text)

    def test_active_path_is_price_ranked_and_zero_claude(self):
        for name in (
            "v5_price_ranked_issue_ticket.py",
            "v5_price_ranked_ticket_gate.py",
            "v5_price_ranked_production_ticket.py",
            "v5_price_ranked_execution_auditor.py",
            "v5_price_ranked_independent_revalidation.py",
        ):
            self.assertIn(name, self.text)
        self.assertNotIn("v5_production_claude_request.py", self.text)
        self.assertNotIn("v5_claude_red_team_policy.py", self.text)
        self.assertNotIn("v5_governance_runtime.py", self.text)
        self.assertNotIn("claude-opus", self.text.casefold())
        self.assertNotIn("anthropic", self.text.casefold())

    def test_production_is_serial_and_fail_closed(self):
        self.assertIn("group: expert-production-admission", self.text)
        self.assertIn("group: expert-production-global", self.text)
        self.assertIn("cancel-in-progress: false", self.text)
        self.assertIn("v5_admission_lock.py", self.text)
        self.assertIn("EXECUTION_REJECTED", self.text)
        self.assertIn("--require-live-catalog", self.text)

    def test_audit_artifact_revalidation_and_attestation_order(self):
        execute = self.text.index("name: Execute explicit price-ranked production runtime")
        prepare = self.text.index("name: Prepare report publication package")
        audit = self.text.index("name: Audit complete price-ranked evidence before publication")
        freeze = self.text.index("name: Freeze primary artifact manifest after audit")
        upload = self.text.index("name: Upload primary ticket artifacts")
        revalidate = self.text.index("name: Independently revalidate uploaded primary artifact")
        publish = self.text.index("name: Publish report only after audit and artifact freeze")
        final = self.text.index("name: Render authoritative V5 final status")
        attest = self.text.index("name: Generate post-upload final attestation")
        proof = self.text.index("name: Upload final attestation artifact")
        self.assertLess(execute, prepare)
        self.assertLess(prepare, audit)
        self.assertLess(audit, freeze)
        self.assertLess(freeze, upload)
        self.assertLess(upload, revalidate)
        self.assertLess(revalidate, publish)
        self.assertLess(publish, final)
        self.assertLess(final, attest)
        self.assertLess(attest, proof)
        self.assertIn("steps.audit.outputs.status == 'PASS'", self.text)
        self.assertIn("steps.independent.outputs.status == 'PASS'", self.text)

    def test_authoritative_failure_is_visible_job_failure(self):
        marker = self.text.index("name: Verify authoritative V5 final outcome")
        tail = self.text[marker:]
        for field in (
            "steps.execute.outcome",
            "steps.prepare_report.outcome",
            "steps.audit.outcome",
            "steps.audit.outputs.status",
            "steps.manifest.outcome",
            "steps.ticket_artifact.outcome",
            "steps.independent.outcome",
            "steps.publish.outcome",
            "steps.attest.outcome",
            "steps.proof_artifact.outcome",
            "steps.final.outputs.status",
        ):
            self.assertIn(field, tail)


if __name__ == "__main__":
    unittest.main()
