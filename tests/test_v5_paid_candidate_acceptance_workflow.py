from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/v5-paid-candidate-acceptance.yml"


class PaidCandidateAcceptanceWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_trigger_is_owner_only_and_sha_exact(self) -> None:
        text = self.text
        self.assertIn("github.actor == github.repository_owner", text)
        self.assertIn("startsWith(github.event.issue.title, '[acceptance]')", text)
        self.assertIn("startsWith(github.event.comment.body, '/accept-main ')", text)
        self.assertIn(r"/^\/accept-main ([0-9a-f]{40})$/", text)
        self.assertIn("candidate !== mainSha", text)
        self.assertIn("candidate === productionSha", text)

    def test_candidate_is_checked_out_by_immutable_sha(self) -> None:
        text = self.text
        self.assertIn("ref: ${{ steps.source.outputs.candidate_sha }}", text)
        self.assertIn('test "$checked" = "$CANDIDATE_SHA"', text)
        self.assertIn('test "$main" = "$CANDIDATE_SHA"', text)
        self.assertIn('test "$production" = "$PREVIOUS_PRODUCTION_SHA"', text)

    def test_same_native_production_evidence_chain_is_used(self) -> None:
        text = self.text
        required = (
            "import v5_issue_ticket",
            "v5_issue_ticket.prepare",
            "v5_ticket_gate.py",
            "v5_production_ticket.py",
            "publish_report.py",
            "v5_execution_auditor_integrity.py",
            "artifact_manifest",
            "v5_independent_artifact_revalidation.py",
            "v5_final_status.py",
            "v5_final_attestation.py",
        )
        for value in required:
            with self.subTest(value=value):
                self.assertIn(value, text)

    def test_acceptance_cannot_move_repository_refs(self) -> None:
        text = self.text
        permissions = text.split("concurrency:", 1)[0]
        self.assertIn("contents: read", permissions)
        self.assertNotIn("contents: write", permissions)
        self.assertNotRegex(text, re.compile(r"\bgit push\b"))
        self.assertNotIn("update-ref", text)
        self.assertNotIn("production_ref_moved\": True", text)
        self.assertIn('"production_ref_moved": False', text)

    def test_no_token_or_cost_hard_stop_is_reintroduced(self) -> None:
        text = self.text
        self.assertNotIn("max-completion-tokens", text)
        self.assertNotIn("governance-max-completion-tokens", text)
        self.assertIn("COST_ADVISORY_USD", text)
        self.assertIn("--cost-advisory-usd", text)
        self.assertIn('"cost_policy": "prompt_led_soft_governance"', text)

    def test_promotion_requires_complete_pass_chain(self) -> None:
        text = self.text
        checks = (
            'steps.execute.outcome }}" = "success"',
            'steps.audit.outputs.status }}" = "PASS"',
            'steps.independent.outputs.status }}" = "PASS"',
            'steps.final.outputs.status }}" = "PASS"',
            'steps.attest.outcome }}" = "success"',
            "candidate-acceptance.json",
        )
        for value in checks:
            with self.subTest(value=value):
                self.assertIn(value, text)


if __name__ == "__main__":
    unittest.main()
