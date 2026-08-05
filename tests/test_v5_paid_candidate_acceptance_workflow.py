from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "v5-price-ranked-paid-candidate-acceptance.yml"
DEPRECATED_WORKFLOW = WORKFLOWS / "v5-paid-candidate-acceptance.yml"


class PaidCandidateAcceptanceWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.deprecated_text = DEPRECATED_WORKFLOW.read_text(encoding="utf-8")

    def test_deprecated_entrypoint_is_zero_call_rejection_only(self) -> None:
        text = self.deprecated_text
        self.assertIn("DEPRECATED_ACCEPTANCE_REJECTED", text)
        self.assertIn("/accept-price-main", text)
        self.assertNotIn("OPENROUTER_API_KEY", text)
        self.assertNotIn("v5_production_ticket.py", text)
        self.assertNotIn("v5_price_ranked_production_ticket.py", text)

    def test_trigger_is_owner_only_and_sha_exact(self) -> None:
        text = self.text
        self.assertIn("github.actor == github.repository_owner", text)
        self.assertIn("startsWith(github.event.issue.title, '[acceptance]')", text)
        self.assertIn(
            "startsWith(github.event.comment.body, '/accept-price-main ')",
            text,
        )
        self.assertIn(r"/^\/accept-price-main ([0-9a-f]{40})$/", text)
        self.assertIn("candidate !== mainSha", text)
        self.assertIn("candidate === productionSha", text)

    def test_candidate_is_checked_out_by_immutable_sha(self) -> None:
        text = self.text
        self.assertIn("ref: ${{ steps.source.outputs.candidate_sha }}", text)
        self.assertIn('test "$checked" = "$CANDIDATE_SHA"', text)
        self.assertIn('test "$main" = "$CANDIDATE_SHA"', text)
        self.assertIn('test "$production" = "$PREVIOUS_PRODUCTION_SHA"', text)

    def test_price_ranked_production_evidence_chain_is_used(self) -> None:
        text = self.text
        required = (
            "import v5_price_ranked_issue_ticket",
            "v5_price_ranked_issue_ticket.prepare",
            "v5_price_ranked_ticket_gate.py",
            "v5_price_ranked_production_ticket.py",
            "publish_report.py",
            "v5_price_ranked_execution_auditor.py",
            "artifact_manifest",
            "v5_price_ranked_independent_revalidation.py",
            "final-price-ranked-attestation.json",
            "candidate-acceptance-receipt.json",
        )
        for value in required:
            with self.subTest(value=value):
                self.assertIn(value, text)
        forbidden = (
            "open-model-market/v5_production_ticket.py",
            "open-model-market/v5_execution_auditor_integrity.py",
            "open-model-market/v5_independent_artifact_revalidation.py",
            "v5_claude_red_team_policy.py",
            "v5_gpt_expert_selector.py",
        )
        for value in forbidden:
            with self.subTest(value=value):
                self.assertNotIn(value, text)

    def test_acceptance_cannot_move_repository_refs(self) -> None:
        text = self.text
        permissions = text.split("concurrency:", 1)[0]
        self.assertIn("contents: read", permissions)
        self.assertNotIn("contents: write", permissions)
        self.assertNotRegex(text, re.compile(r"\bgit push\b"))
        self.assertNotIn("update-ref", text)
        self.assertNotIn('"production_moved": True', text)
        self.assertIn('"production_moved": False', text)

    def test_no_token_or_cost_hard_stop_is_reintroduced(self) -> None:
        text = self.text
        self.assertNotIn("--max-completion-tokens", text)
        self.assertNotIn("--governance-max-completion-tokens", text)
        self.assertIn("COST_ADVISORY_USD", text)
        self.assertIn("--cost-advisory-usd", text)
        self.assertIn('"cost_policy": "prompt_led_soft_governance"', text)

    def test_acceptance_requires_complete_pass_chain(self) -> None:
        text = self.text
        checks = (
            "EXECUTE_OUTCOME",
            "AUDIT_OUTCOME",
            "INDEPENDENT_OUTCOME",
            "INDEPENDENT_STATUS",
            "artifact-manifest.json",
            "independent-revalidation.json",
            "final-price-ranked-attestation.json",
            "candidate-acceptance-receipt.json",
            "Verify authoritative price-ranked acceptance outcome",
            'test "$(jq -r \'.status\' candidate-acceptance-receipt.json)" = "PASS"',
            'test "${{ steps.attest.outputs.status }}" = "PASS"',
        )
        for value in checks:
            with self.subTest(value=value):
                self.assertIn(value, text)

    def test_price_ranked_identity_is_explicitly_attested(self) -> None:
        text = self.text
        self.assertIn('"selection_authority": "python-price-ranked-orchestrator"', text)
        self.assertIn('"orchestration_library": "networkx"', text)
        self.assertIn('"claude_mechanism_enabled": False', text)
        self.assertIn('"governance_model_calls": 0', text)
        self.assertIn("selected-companies-not-distinct", text)
        self.assertIn("candidate-cost-order-invalid", text)


if __name__ == "__main__":
    unittest.main()
