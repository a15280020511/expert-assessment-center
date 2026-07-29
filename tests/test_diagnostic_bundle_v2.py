from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "diagnostic_bundle",
    ROOT / "open-model-market" / "diagnostic_bundle.py",
)
assert SPEC and SPEC.loader
diagnostic_bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostic_bundle)


class DiagnosticBundleV2Tests(unittest.TestCase):
    def test_failure_chain_and_secret_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ticket-status.json").write_text(
                json.dumps({"accepted": True, "task_id": "expert-diag-001"}),
                encoding="utf-8",
            )
            (root / "expert-team-error.json").write_text(
                json.dumps(
                    {
                        "error_code": "EXPERT_TIMEOUT",
                        "stage": "experts",
                        "message": "provider timed out",
                        "retryable": True,
                    }
                ),
                encoding="utf-8",
            )
            (root / "request-audit.json").write_text(
                json.dumps(
                    {
                        "status": "PASS",
                        "captured_request_count": 1,
                        "expected_request_count": 1,
                    }
                ),
                encoding="utf-8",
            )
            (root / "call-ledger.json").write_text(
                json.dumps({"summary": {"call_count": 1}}),
                encoding="utf-8",
            )
            (root / "execution-console.log").write_text(
                "timeout\n", encoding="utf-8"
            )
            with mock.patch.dict(
                os.environ,
                {
                    "GITHUB_RUN_ID": "99",
                    "OPENROUTER_API_KEY": "must-not-leak",
                },
                clear=False,
            ):
                result = diagnostic_bundle.build(
                    root,
                    execute_outcome="failure",
                    publish_outcome="skipped",
                    state_outcome="success",
                )
            self.assertEqual(result["schema_version"], "expert-diagnostics-v2")
            self.assertEqual(result["primary_failure"]["code"], "EXPERT_TIMEOUT")
            self.assertEqual(result["diagnostic_confidence"], "high")
            self.assertGreaterEqual(len(result["failure_chain"]), 1)
            serialized = json.dumps(result, ensure_ascii=False)
            self.assertNotIn("must-not-leak", serialized)
            self.assertFalse(result["security"]["secret_values_included"])


if __name__ == "__main__":
    unittest.main()
