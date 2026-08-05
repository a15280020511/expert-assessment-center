from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


class PriceRankedPaidAcceptanceWorkflowTests(unittest.TestCase):
    def test_obsolete_paid_entrypoint_is_zero_call_rejection_only(self) -> None:
        text = (WORKFLOWS / "v5-paid-candidate-acceptance.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("DEPRECATED_ACCEPTANCE_REJECTED", text)
        self.assertIn("/accept-price-main", text)
        self.assertNotIn("OPENROUTER_API_KEY", text)
        self.assertNotIn("open-model-market/v5_production_ticket.py", text)
        self.assertNotIn("open-model-market/v5_execution_auditor_integrity.py", text)
        self.assertNotIn("open-model-market/v5_independent_artifact_revalidation.py", text)

    def test_new_paid_entrypoint_uses_only_price_ranked_runtime_chain(self) -> None:
        text = (
            WORKFLOWS / "v5-price-ranked-paid-candidate-acceptance.yml"
        ).read_text(encoding="utf-8")
        required = (
            "/accept-price-main ",
            "v5_price_ranked_issue_ticket",
            "open-model-market/v5_price_ranked_ticket_gate.py",
            "open-model-market/v5_price_ranked_production_ticket.py",
            "open-model-market/v5_price_ranked_execution_auditor.py",
            "open-model-market/v5_price_ranked_independent_revalidation.py",
            "python-price-ranked-orchestrator",
            "orchestration_library\": \"networkx",
            "governance_model_calls\": 0",
            "claude_mechanism_enabled\": False",
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

    def test_new_paid_entrypoint_requires_independent_revalidation_and_attestation(self) -> None:
        text = (
            WORKFLOWS / "v5-price-ranked-paid-candidate-acceptance.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("independent-revalidation.json", text)
        self.assertIn("final-price-ranked-attestation.json", text)
        self.assertIn("candidate-acceptance-receipt.json", text)
        self.assertIn("production_moved\": False", text)
        self.assertIn("Verify authoritative price-ranked acceptance outcome", text)


if __name__ == "__main__":
    unittest.main()
