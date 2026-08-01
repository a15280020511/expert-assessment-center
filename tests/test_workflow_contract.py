import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "execution-ticket.yml"
PROMOTION = ROOT / ".github" / "workflows" / "promote-v5-production.yml"
PAID_ACCEPTANCE = (
    ROOT / ".github" / "workflows" / "v5-one-time-paid-acceptance.yml"
)
DETACHED_ATTESTATION = (
    ROOT / ".github" / "workflows" / "v5-paid-acceptance-attest.yml"
)
DETACHED_ATTESTATION_REQUEST = (
    ROOT / ".github" / "v5-paid-acceptance-attest-request.json"
)


class WorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.promotion = PROMOTION.read_text(encoding="utf-8")
        cls.paid_acceptance = PAID_ACCEPTANCE.read_text(encoding="utf-8")

    def test_dynamic_graph_uses_ticket_approved_call_ceiling(self):
        self.assertNotIn('TOTAL_MODEL_CALLS: "16"', self.text)
        self.assertIn("--maximum-total-calls", self.text)
        self.assertIn("--maximum-recovery-calls", self.text)
        self.assertIn("Execute explicit V5 production runtime", self.text)
        self.assertNotIn("Execute fixed 3+1 expert team", self.text)

    def test_production_uses_only_native_v5_entrypoints(self):
        self.assertIn("v5_issue_ticket.py prepare", self.text)
        self.assertIn("v5_production_ticket.py", self.text)
        self.assertIn("v5_execution_auditor_integrity.py", self.text)
        self.assertIn("v5_final_status.py", self.text)
        self.assertIn("v5_final_attestation.py", self.text)
        self.assertNotIn("expert_team_hardened.py", self.text)
        self.assertNotIn(
            "python open-model-market/execution_auditor.py", self.text
        )
        self.assertNotIn("python open-model-market/final_status.py", self.text)

    def test_only_explicit_comment_commands_can_trigger_execution(self):
        self.assertIn("issue_comment:", self.text)
        self.assertIn("types: [created]", self.text)
        self.assertNotIn("issues:\n", self.text)
        self.assertNotIn("types: [opened, reopened]", self.text)
        self.assertIn(
            "startsWith(github.event.comment.body, '/run-expert-team ')",
            self.text,
        )
        self.assertIn(
            "startsWith(github.event.comment.body, '/retry-expert-team ')",
            self.text,
        )

    def test_both_jobs_checkout_production_ref(self):
        self.assertEqual(self.text.count("ref: production"), 2)
        self.assertIn("Checkout production source for admission", self.text)
        self.assertIn("Checkout pinned production source", self.text)
        self.assertNotIn("ref: main", self.text)

    def test_production_has_atomic_admission_and_execution_groups(self):
        self.assertIn("group: expert-production-admission", self.text)
        self.assertIn("group: expert-production-global", self.text)
        self.assertIn("cancel-in-progress: false", self.text)
        self.assertIn("v5_admission_lock.py", self.text)
        self.assertIn("EXECUTION_REJECTED", self.text)

    def test_report_audit_primary_artifact_and_final_attestation_order(self):
        report = self.text.index("name: Prepare public report comments")
        audit = self.text.index("name: Audit native V5 execution")
        refresh = self.text.index("name: Refresh primary artifact manifest")
        upload = self.text.index("name: Upload primary ticket artifacts")
        final = self.text.index("name: Render authoritative V5 final status")
        report_publish = self.text.index("name: Publish audited report comments")
        attest = self.text.index("name: Generate post-upload final attestation")
        proof = self.text.index("name: Upload final attestation artifact")
        status_publish = self.text.index(
            "name: Publish authoritative V5 final status"
        )
        self.assertLess(report, audit)
        self.assertLess(audit, refresh)
        self.assertLess(refresh, upload)
        self.assertLess(upload, final)
        self.assertLess(final, report_publish)
        self.assertLess(report_publish, attest)
        self.assertLess(attest, proof)
        self.assertLess(proof, status_publish)
        self.assertIn("ticket-artifacts/final-status.json", self.text)

    def test_authoritative_failure_is_visible_job_failure(self):
        marker = self.text.index("name: Verify authoritative V5 final outcome")
        tail = self.text[marker:]
        self.assertIn("steps.ticket_artifact.outcome", tail)
        self.assertIn("steps.audit.outcome", tail)
        self.assertIn("steps.manifest.outcome", tail)
        self.assertIn("steps.publish.outcome", tail)
        self.assertIn("steps.attest.outcome", tail)
        self.assertIn("steps.proof_artifact.outcome", tail)
        self.assertIn("steps.final.outputs.status", tail)

    def test_production_has_anomaly_guard_not_fixed_cost_rewrite(self):
        self.assertNotIn("MAX_ESTIMATED_COST_USD", self.text)
        self.assertIn("cost_anomaly_usd", self.text)
        self.assertIn("--cost-anomaly-usd", self.text)

    def test_no_legacy_or_history_runtime_path_exists(self):
        legacy_version = "v" + "3"
        self.assertNotIn(legacy_version, self.text.casefold())
        self.assertNotIn("Restore latest model performance history", self.text)
        self.assertNotIn("model-performance-state", self.text)
        self.assertNotIn("MODEL_HISTORY_PATH", self.text)

    def test_promotion_is_read_only_until_constitutional_acceptance(self):
        self.assertIn("workflow_dispatch:", self.promotion)
        self.assertIn("permissions:\n  contents: read", self.promotion)
        self.assertIn("group: v5-production-qualification", self.promotion)
        self.assertIn("task-independent constitutional matrix", self.promotion)
        self.assertIn("v5-adaptive-search.json", self.promotion)
        self.assertIn("task_specific_production_branching", self.promotion)
        self.assertIn("case_derived_compaction_applied", self.promotion)
        self.assertIn("test ! -e .release-authorized", self.promotion)
        self.assertNotIn("git push", self.promotion)
        self.assertNotIn("refs/heads/production", self.promotion)
        self.assertNotIn("OPENROUTER_API_KEY", self.promotion)

    def test_paid_attestation_is_bound_to_same_run_artifact(self):
        paid = self.paid_acceptance
        self.assertIn("needs: paid-generic-acceptance", paid)
        self.assertIn("EXPECTED_ARTIFACT_NAME: v5-paid-acceptance-", paid)
        self.assertIn(
            '"/repos/$REPOSITORY/actions/runs/$PAID_RUN_ID/artifacts',
            paid,
        )
        self.assertIn("v5_paid_acceptance_revalidation.py", paid)
        self.assertIn("paid-acceptance-task.txt", paid)
        self.assertIn("paid_artifact_digest", paid)
        self.assertIn('--head-sha "$PAID_HEAD_SHA"', paid)
        self.assertIn("Independently recompute raw Artifact evidence", paid)
        self.assertIn("paid_artifact_digest", paid)
        self.assertIn('test "$(git rev-parse HEAD)" = "$PAID_HEAD_SHA"', paid)
        self.assertNotIn('PR_NUMBER: "170"', paid)
        self.assertNotIn("fix/v5-constitution-p0-publication-integrity", paid)
        self.assertIn('test "$(git rev-parse HEAD)" = "$PAID_HEAD_SHA"', paid)
        self.assertNotIn("status=completed&per_page=20", paid)
        self.assertFalse(DETACHED_ATTESTATION.exists())
        self.assertFalse(DETACHED_ATTESTATION_REQUEST.exists())


if __name__ == "__main__":
    unittest.main()
