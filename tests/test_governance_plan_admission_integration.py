import argparse
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

import v5_price_ranked_issue_ticket as governed_ticket  # noqa: E402


class GovernancePlanAdmissionIntegrationTests(unittest.TestCase):
    def test_invalid_ticket_guard_uses_dynamic_governed_wrapper(self) -> None:
        text = (ROOT / ".github/workflows/invalid-ticket-rejection.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "python open-model-market/v5_price_ranked_issue_ticket.py prepare", text
        )
        self.assertIn(
            "python open-model-market/v5_price_ranked_issue_ticket.py render", text
        )
        self.assertNotIn("v5_price_ranked_ticket_gate.py", text)
        self.assertNotIn("v5_issue_ticket.py prepare", text)

    def test_cost_advisory_is_optional_and_never_an_admission_gate(self) -> None:
        self.assertIsNone(governed_ticket._cost_advisory({}))  # noqa: SLF001
        self.assertIsNone(
            governed_ticket._cost_advisory({"approved_budget": {}})  # noqa: SLF001
        )
        self.assertIsNone(
            governed_ticket._cost_advisory(  # noqa: SLF001
                {"approved_budget": {"cost_anomaly_usd": -1}}
            )
        )
        self.assertEqual(
            governed_ticket._cost_advisory(  # noqa: SLF001
                {"approved_budget": {"cost_anomaly_usd": "0.125"}}
            ),
            0.125,
        )

    def test_task_text_preserves_requirements_and_evidence(self) -> None:
        text = governed_ticket._task_text(  # noqa: SLF001
            {
                "task": {
                    "question": "compare A and B",
                    "requirements": ["show assumptions", "give a recommendation"],
                },
                "evidence": [{"source": "fixture", "value": 1}],
            }
        )
        self.assertIn("compare A and B", text)
        self.assertIn("show assumptions", text)
        self.assertIn("give a recommendation", text)
        self.assertIn("fixture", text)

    def test_github_output_boolean_is_lowercase(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "github-output.txt"
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}):
                governed_ticket._write_output("accepted", True)  # noqa: SLF001
                governed_ticket._write_output("provider", "unrestricted-openrouter")  # noqa: SLF001
            values = dict(
                line.split("=", 1)
                for line in output.read_text(encoding="utf-8").splitlines()
            )
            self.assertEqual(values["accepted"], "true")
            self.assertEqual(values["provider"], "unrestricted-openrouter")

    def test_malformed_issue_body_produces_rejection_receipt_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(
                event_path="",
                comments_path="",
                issue_title="fixture",
                issue_body="not-json",
                issue_number=123,
                actor="owner",
                author_association="OWNER",
                comment_body="/run-expert-team fixture",
                output_dir=directory,
            )
            self.assertEqual(governed_ticket.prepare(args), 0)
            status = json.loads(
                (Path(directory) / "ticket-status.json").read_text(encoding="utf-8")
            )
            self.assertFalse(status["accepted"])
            self.assertEqual(status["admission_mode"], "dynamic-no-business-gates")
            self.assertFalse(status["free_first_required"])
            self.assertFalse(status["canary_required"])
            self.assertEqual(
                status["provider_routing_mode"], "unrestricted-openrouter"
            )
            self.assertTrue(status["errors"])


if __name__ == "__main__":
    unittest.main()
