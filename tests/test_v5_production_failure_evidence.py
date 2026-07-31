import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_production_ticket as production_ticket  # noqa: E402


class TestV5ProductionFailureEvidence(unittest.TestCase):
    def test_pipeline_exception_creates_report_before_normalization(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "artifacts"
            output.mkdir()
            (output / "v5-execution-summary.json").write_text(
                json.dumps({
                    "status": "failed",
                    "stop_reason": "native-runtime-preflight-rejected",
                    "execution_budget": {"calls_reserved": 0},
                    "cost_preflight": {
                        "status": "rejected",
                        "blockers": ["test-preflight-blocker"],
                    },
                }),
                encoding="utf-8",
            )
            observed = {}

            def fake_normalize(root, **kwargs):
                observed["report_exists_before_normalization"] = (
                    root / "v5-final-report.md"
                ).is_file()
                return {"status": "failed"}

            with (
                patch.object(production_ticket, "_runtime", return_value=object()),
                patch.object(
                    production_ticket.v5_pipeline,
                    "main",
                    side_effect=RuntimeError("preflight failure"),
                ),
                patch.object(
                    production_ticket,
                    "_normalize_evidence",
                    side_effect=fake_normalize,
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "preflight failure"):
                    production_ticket.main([
                        "--task",
                        "zero-cost failure path",
                        "--output-dir",
                        str(output),
                        "--maximum-total-calls",
                        "4",
                        "--maximum-recovery-calls",
                        "1",
                        "--cost-anomaly-usd",
                        "0.25",
                    ])

            self.assertTrue(observed["report_exists_before_normalization"])
            report = (output / "v5-final-report.md").read_text(encoding="utf-8")
            self.assertIn("preflight failure", report)
            self.assertIn("test-preflight-blocker", report)
            error = json.loads(
                (output / "expert-team-error.json").read_text(encoding="utf-8")
            )
            self.assertEqual(error["error_code"], "V5_PRODUCTION_EXECUTION_FAILED")
            self.assertFalse(error["fallback_used"])


if __name__ == "__main__":
    unittest.main()
