from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))
import diagnostic_bundle  # noqa: E402


class DiagnosticBundleTests(unittest.TestCase):
    def test_partial_run_and_malformed_json_still_produce_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "ticket-status.json").write_text(
                json.dumps({"accepted": True, "calls": 6}), encoding="utf-8"
            )
            (root / "expert-team-error.json").write_text(
                json.dumps(
                    {
                        "error_code": "MODEL_TIMEOUT",
                        "stage": "experts",
                        "message": "timed out",
                    }
                ),
                encoding="utf-8",
            )
            (root / "model-performance.json").write_text("{broken", encoding="utf-8")
            result = diagnostic_bundle.build(
                root,
                execute_outcome="failure",
                publish_outcome="skipped",
                state_outcome="success",
            )
            self.assertEqual(result["primary_failure"]["code"], "MODEL_TIMEOUT")
            self.assertEqual(len(result["parse_errors"]), 1)
            self.assertFalse(result["security"]["secret_values_included"])
            self.assertTrue((root / "diagnostic-summary.json").exists())


if __name__ == "__main__":
    unittest.main()
