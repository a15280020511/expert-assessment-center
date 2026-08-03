import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "execution-ticket.yml"
PROMOTION = ROOT / ".github" / "workflows" / "promote-v5-production.yml"
PAID_ACCEPTANCE = (
    ROOT / ".github" / "workflows" / "v5-final-paid-claude-acceptance-20260803.yml"
)
LEGACY_PAID_ACCEPTANCE = (
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

    def test_control_plane_and_execution_source_are_one_frozen_version(self):
        self.assertEqual(
            self.text.count("Enforce frozen production control plane"),
            1,
        )
        self.assertIn("Resolve authoritative execution source", self.text)
        self.assertGreaterEqual(
            self.text.count('test "$main" = "$production"'),
            2,
        )
        self.assertGreaterEqual(
            self.text.count('test "$checked" = "$production"'),
            2,
        )
        self.assertIn("checked-out-production-sha.txt", self.text)
        self.assertIn(
            '--commit-sha "$AUTHORITATIVE_EXECUTION_SHA"',
            self.text,
        )
        self.assertNotIn('--commit-sha "${{ github.sha }}"', self.text)

    def test_production_has_atomic_admission_and_execution_groups(self):
        self.assertIn("group: expert-production-admission", self.text)
        self.assertIn("group: expert-production-global", self.text)
        self.assertIn("cancel-in-progress: false", self.text)
        self.assertIn("v5_admission_lock.py", self.text)
        self.assertIn("EXECUTION_REJECTED", self.text)

    def test_report_audit_primary_artifact_and_final_attestation_order(self):
        prepare = self.text.index("name: Prepare report publication package")
        audit = self.text.index(
            "name: Audit complete native V5 evidence before publication"
        )
        freeze = self.text.index(
            "name: Freeze primary artifact manifest after audit"
        )
        upload = self.text.index("name: Upload primary ticket artifacts")
        publish_report = self.text.index(
            "name: Publish report only after audit and artifact freeze"
        )
        final = self.text.index("name: Render authoritative V5 final status")
        attest = self.text.index("name: Generate post-upload final attestation")
        proof = self.text.index("name: Upload final attestation artifact")
        publish_status = self.text.index(
            "name: Publish authoritative V5 final status"
        )
        self.assertLess(prepare, audit)
        self.assertLess(audit, freeze)
        self.assertLess(freeze, upload)
        self.assertLess(upload, publish_report)
        self.assertLess(publish_report, final)
        self.assertLess(final, attest)
        self.assertLess(attest, proof)
        self.assertLess(proof, publish_status)
        self.assertIn("steps.audit.outputs.status == 'PASS'", self.text)
        self.assertIn("ticket-artifacts/final-status.json", self.text)

    def test_authoritative_failure_is_visible_job_failure(self):
        marker = self.text.index("name: Verify authoritative V5 final outcome")
        tail = self.text[marker:]
        self.assertIn("steps.execute.outcome", tail)
        self.assertIn("steps.prepare_report.outcome", tail)
        self.assertIn("steps.ticket_artifact.outcome", tail)
        self.assertIn("steps.audit.outcome", tail)
        self.assertIn("steps.audit.outputs.status", tail)
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

    def test_promotion_is_read_only_until_explicit_acceptance(self):
        self.assertIn("workflow_dispatch:", self.promotion)
        self.assertIn("permissions:\n  contents: read", self.promotion)
        self.assertIn("group: v5-production-qualification", self.promotion)
        self.assertIn("task-independent advisory matrix", self.promotion)
        self.assertIn("~openai/gpt-latest", self.promotion)
        self.assertIn("~anthropic/claude-opus-latest", self.promotion)
        self.assertIn("claude_is_advisory_only", self.promotion)
        self.assertIn("claude_gatekeeping_allowed", self.promotion)
        self.assertIn("gpt_synthesis_calls", self.promotion)
        self.assertIn("deterministic-constitutional-validator", self.promotion)
        self.assertIn("test ! -e .release-authorized", self.promotion)
        self.assertNotIn("v5-adaptive-search.json", self.promotion)
        self.assertNotIn("v5-optimization.json", self.promotion)
        self.assertNotIn("git push", self.promotion)
        self.assertNotIn("refs/heads/production", self.promotion)
        self.assertNotIn("OPENROUTER_API_KEY", self.promotion)

    def test_paid_acceptance_is_explicit_bounded_current_and_independent(self):
        paid = self.paid_acceptance
        self.assertIn("workflow_dispatch:", paid)
        self.assertIn('branches:\n      - "acceptance/v5-final-paid-*"', paid)
        self.assertIn("RUN-EXACTLY-ONCE", paid)
        self.assertIn("v5-final-paid-acceptance-request-1", paid)
        self.assertIn("git -C request-source rev-parse HEAD^", paid)
        self.assertIn("v5_production_ticket.py", paid)
        self.assertIn('MAXIMUM_TOTAL_CALLS: "4"', paid)
        self.assertIn('MAXIMUM_RECOVERY_CALLS: "0"', paid)
        self.assertIn('COST_CAP_USD: "0.25"', paid)
        self.assertIn('MAX_COMPLETION_TOKENS: "512"', paid)
        self.assertIn("Complete all zero-call release gates before paid execution", paid)
        self.assertIn("Run zero-cost free Canary and API-key limit preflight", paid)
        self.assertIn("https://openrouter.ai/api/v1/key", paid)
        self.assertIn('"model": "openrouter/free"', paid)
        self.assertIn("claude_review_count", paid)
        self.assertIn("gpt_synthesis_count", paid)
        self.assertIn("claude_is_advisory_only", paid)
        self.assertIn("claude_gatekeeping_allowed", paid)
        self.assertIn("old_local_planner_used", paid)
        self.assertIn("artifact_manifest", paid)
        self.assertIn("v5_independent_artifact_revalidation.py", paid)
        self.assertIn("paid-acceptance-attestation.json", paid)
        self.assertIn("independently_recomputed_from_primitive_evidence", paid)
        self.assertIn("paid_acceptance_verdict_used_as_source", paid)
        self.assertIn("formal_model_identity_qualified", paid)
        self.assertIn("production_ref_moved: false", paid)
        self.assertNotIn("quality_tier", paid)
        self.assertNotIn("--quality-tier", paid)
        self.assertNotIn("git push", paid)
        self.assertNotIn("refs/heads/production", paid)
        self.assertFalse(LEGACY_PAID_ACCEPTANCE.exists())
        self.assertFalse(DETACHED_ATTESTATION.exists())
        self.assertFalse(DETACHED_ATTESTATION_REQUEST.exists())


if __name__ == "__main__":
    unittest.main()
