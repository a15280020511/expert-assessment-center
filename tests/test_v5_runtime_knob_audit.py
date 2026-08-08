from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from v5_runtime_knob_audit import audit_runtime_knob_coverage  # noqa: E402


class RuntimeKnobAuditTests(unittest.TestCase):
    def test_passes_when_planned_reasoning_and_allowance_reach_request(self) -> None:
        graph = {
            "nodes": [
                {
                    "node_id": "n1",
                    "model": "vendor/model-a",
                    "reasoning_profile": {"effort": "high"},
                }
            ]
        }
        requests = [
            {
                "model": "vendor/model-a",
                "reasoning": {"effort": "high", "exclude": True},
                "max_tokens": 1024,
            }
        ]
        audit = audit_runtime_knob_coverage(graph, requests)
        self.assertEqual("PASS", audit["status"])
        self.assertEqual([], audit["computed_but_unused"])
        self.assertEqual(1, audit["reasoning_binding_count"])
        self.assertEqual(1, audit["requests_with_dynamic_output_allowance"])

    def test_fails_computed_but_unused_reasoning(self) -> None:
        graph = {
            "nodes": [
                {
                    "node_id": "n1",
                    "model": "vendor/model-a",
                    "reasoning_profile": {"effort": "medium"},
                }
            ]
        }
        requests = [{"model": "vendor/model-a", "max_tokens": 512}]
        audit = audit_runtime_knob_coverage(graph, requests)
        self.assertEqual("FAIL", audit["status"])
        self.assertTrue(
            any(
                row.get("parameter") == "role-reasoning-effort"
                for row in audit["computed_but_unused"]
            )
        )


if __name__ == "__main__":
    unittest.main()
