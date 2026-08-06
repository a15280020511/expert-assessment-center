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
import v5_price_ranked_ticket_gate as gate  # noqa: E402


class GovernancePlanAdmissionIntegrationTests(unittest.TestCase):
    def test_invalid_ticket_guard_uses_governed_wrapper(self) -> None:
        text = (ROOT / ".github/workflows/invalid-ticket-rejection.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "python open-model-market/v5_price_ranked_issue_ticket.py prepare", text
        )
        self.assertIn(
            "python open-model-market/v5_price_ranked_issue_ticket.py render", text
        )
        self.assertNotIn(
            "python open-model-market/v5_issue_ticket.py prepare", text
        )
        self.assertNotIn(
            "python open-model-market/v5_issue_ticket.py render", text
        )

    def test_none_cost_output_finishes_as_empty_github_output(self) -> None:
        status = {
            "accepted": True,
            "cost_anomaly_usd": None,
            "model_plan_sha256": "a" * 64,
            "selected_expert_count": 3,
            "selected_recovery_count": 1,
            "model_selection_authority": "decision-system-governance",
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "github-output.txt"
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}):
                governed_ticket._rewrite_outputs(status)  # noqa: SLF001
            values: dict[str, str] = {}
            for line in output.read_text(encoding="utf-8").splitlines():
                key, value = line.split("=", 1)
                values[key] = value
            self.assertEqual(values["cost_anomaly_usd"], "")
            self.assertEqual(values["selected_expert_count"], "3")

    def test_optional_cost_sentinels_are_absent(self) -> None:
        for value in ("", "None", "none", "NULL", " null "):
            with self.subTest(value=value):
                self.assertIsNone(gate._optional_float(value))  # noqa: SLF001

    def test_optional_cost_numeric_value_is_preserved(self) -> None:
        self.assertEqual(gate._optional_float("0.125"), 0.125)  # noqa: SLF001

    def test_optional_cost_rejects_nonfinite_negative_and_garbage(self) -> None:
        for value in ("nan", "inf", "-0.01", "garbage"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    gate._optional_float(value)  # noqa: SLF001

    def test_governance_runtime_gate_accepts_missing_cost_advisory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ticket-status.json").write_text(
                json.dumps(
                    {
                        "accepted": True,
                        "runtime_version": "v5-governance-plan-runtime-1",
                        "calls": 4,
                        "maximum_recovery_calls": 1,
                        "maximum_initial_calls": 3,
                        "claude_mechanism_enabled": False,
                        "governance_model_calls": 0,
                        "cost_anomaly_usd": None,
                    }
                ),
                encoding="utf-8",
            )
            (root / "ticket.json").write_text(
                json.dumps({"route": "expert-team"}), encoding="utf-8"
            )
            (root / "task.txt").write_text("test", encoding="utf-8")
            argv = [
                "v5_price_ranked_ticket_gate.py",
                "--output-dir",
                str(root),
                "--expected-calls",
                "4",
                "--expected-recovery-calls",
                "1",
                "--expected-cost-anomaly-usd",
                "None",
            ]
            with mock.patch.object(sys, "argv", argv):
                self.assertEqual(gate.main(), 0)


if __name__ == "__main__":
    unittest.main()
