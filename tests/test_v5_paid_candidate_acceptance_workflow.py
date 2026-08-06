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
        self.assertIn("/accept-governed-main", text)
        self.assertNotIn("OPENROUTER_API_KEY", text)
        self.assertNotIn("v5_price_ranked_production_ticket.py", text)

    def test_trigger_is_owner_only_and_sha_exact(self) -> None:
        text = self.text
        self.assertIn("github.actor == github.repository_owner", text)
        self.assertIn("startsWith(github.event.issue.title, '[acceptance]')", text)
        self.assertIn(
            "startsWith(github.event.comment.body, '/accept-governed-main ')",
            text,
        )
        self.assertIn(r"/^\/accept-governed-main ([0-9a-f]{40})$/", text)
        self.assertIn("candidate !== mainSha", text)
        self.assertIn("candidate === productionSha", text)

    def test_candidate_is_checked_out_by_immutable_sha(self) -> None:
        text = self.text
        self.assertIn("ref: ${{ steps.source.outputs.candidate_sha }}", text)
        self.assertIn('test "$checked" = "$CANDIDATE_SHA"', text)
        self.assertIn('test "$main" = "$CANDIDATE_SHA"', text)
        self.assertIn('test "$production" = "$PREVIOUS_PRODUCTION_SHA"', text)

    def test_governance_signed_ticket_is_the_only_selection_source(self) -> None:
        text = self.text
        required = (
            "governance_model_plan is required",
            'plan.get("selection_authority") != "decision-system-governance"',
            "governance plan must prove zero model calls",
            "expert center reranking must be disabled",
            "model substitution must be disabled",
            "v5_price_ranked_issue_ticket.prepare",
            "v5_price_ranked_ticket_gate.py",
            "v5_price_ranked_production_ticket.py",
            "v5_price_ranked_execution_auditor.py",
            "v5_price_ranked_independent_revalidation.py",
        )
        for value in required:
            with self.subTest(value=value):
                self.assertIn(value, text)
        forbidden = (
            "python-price-ranked-orchestrator",
            "v5_gpt_expert_selector.py",
            "v5_claude_red_team_policy.py",
            "build_price_ranked_proposal",
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
        self.assertIn("cost_anomaly_usd // empty", text)
        self.assertIn("--cost-anomaly-usd", text)
        self.assertNotIn("maximum_cost_usd", text)

    def test_acceptance_requires_complete_pass_chain(self) -> None:
        text = self.text
        checks = (
            "EXECUTE_OUTCOME",
            "AUDIT_OUTCOME",
            "INDEPENDENT_OUTCOME",
            "INDEPENDENT_STATUS",
            "artifact-manifest.json",
            "independent-revalidation.json",
            "final-governance-owned-attestation.json",
            "candidate-acceptance-receipt.json",
            "Verify authoritative governance-owned acceptance outcome",
            'test "$(jq -r \'.status\' candidate-acceptance-receipt.json)" = "PASS"',
            'test "${{ steps.attest.outputs.status }}" = "PASS"',
        )
        for value in checks:
            with self.subTest(value=value):
                self.assertIn(value, text)

    def test_governance_owned_identity_is_explicitly_attested(self) -> None:
        text = self.text
        self.assertIn('authority = "decision-system-governance"', text)
        self.assertIn('"selection_authority": authority', text)
        self.assertIn('"model_selection_performed_locally": False', text)
        self.assertIn('"model_reranking_performed_locally": False', text)
        self.assertIn('"orchestration_library": "networkx"', text)
        self.assertIn('"governance_model_calls": 0', text)
        self.assertIn("selection-local-selection-not-disabled", text)
        self.assertIn("selection-local-reranking-not-disabled", text)
        self.assertIn("selection-local-substitution-not-disabled", text)


if __name__ == "__main__":
    unittest.main()
