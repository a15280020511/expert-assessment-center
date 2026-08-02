from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from openrouter_api import OpenRouterRequestError  # noqa: E402
from v5_runtime import ExecutionEngine, FailureCategory  # noqa: E402


class V5ResponseDiagnosticsEvidenceTests(unittest.TestCase):
    def test_protocol_diagnostics_are_preserved_in_attempt_failure(self) -> None:
        error = OpenRouterRequestError(
            "invalid response",
            category="invalid_response",
            retryable=False,
            request_sent=True,
            response_received=True,
            response_diagnostics={
                "schema_version": "openrouter-response-diagnostics-1",
                "body_sha256": "a" * 64,
                "content_type": "text/event-stream",
                "bytes_received": 1234,
            },
        )
        node = SimpleNamespace(
            model="example/model",
            provider_endpoint="example/model@example-provider",
        )
        failure = ExecutionEngine._failure_from_exception(error, node).to_dict()
        self.assertEqual(
            FailureCategory.PROVIDER_INVALID_RESPONSE.value,
            failure["category"],
        )
        self.assertEqual(
            "openrouter-response-diagnostics-1",
            failure["response_diagnostics"]["schema_version"],
        )
        self.assertEqual("a" * 64, failure["response_diagnostics"]["body_sha256"])
        self.assertEqual(1234, failure["response_diagnostics"]["bytes_received"])


if __name__ == "__main__":
    unittest.main()
