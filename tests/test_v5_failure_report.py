import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_failure_report import ensure_failure_report  # noqa: E402


class TestV5FailureReport(unittest.TestCase):
    def test_preflight_rejection_produces_publishable_failure_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "v5-execution-summary.json").write_text(
                json.dumps({
                    "status": "failed",
                    "stop_reason": "native-runtime-preflight-rejected",
                    "execution_budget": {"calls_reserved": 0},
                    "cost_preflight": {
                        "status": "rejected",
                        "estimated_initial_cost_usd": 0.24374962,
                        "risk_adjusted_cost_upper_usd": 0.28762455,
                        "cost_anomaly_usd": 0.25,
                        "policy": "native-runtime-preflight-before-first-call",
                        "blockers": [
                            "preflight-risk-adjusted-cost-above-anomaly-limit"
                        ],
                    },
                }),
                encoding="utf-8",
            )

            path = ensure_failure_report(
                root,
                RuntimeError("V5 graph rejected before model calls"),
            )
            report = path.read_text(encoding="utf-8")

            self.assertTrue((root / "expert-team-report.md").is_file())
            self.assertIn("deterministic fail-closed report", report)
            self.assertIn("0.24374962", report)
            self.assertIn("0.28762455", report)
            self.assertIn("preflight-risk-adjusted-cost-above-anomaly-limit", report)
            self.assertIn("No alternate runtime was invoked", report)

    def test_existing_valid_report_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = "# Existing report\n\nPreserve this evidence.\n"
            (root / "v5-final-report.md").write_text(original, encoding="utf-8")

            ensure_failure_report(root, RuntimeError("later failure"))

            self.assertEqual(
                (root / "v5-final-report.md").read_text(encoding="utf-8"),
                original,
            )
            self.assertEqual(
                (root / "expert-team-report.md").read_text(encoding="utf-8"),
                original,
            )


if __name__ == "__main__":
    unittest.main()
