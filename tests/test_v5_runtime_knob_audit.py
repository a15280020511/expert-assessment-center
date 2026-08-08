from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from v5_runtime_knob_audit import audit_runtime_knob_coverage  # noqa: E402


def timeout_node_results() -> list[dict]:
    return [
        {
            "node_id": "n1",
            "attempts": [
                {
                    "attempt_index": 1,
                    "model": "vendor/model-a",
                    "request": {
                        "model": "vendor/model-a",
                        "reasoning": {"effort": "high", "exclude": True},
                        "max_tokens": 1024,
                    },
                    "answer_transformations": [
                        {
                            "type": "dynamic-model-timeout-binding",
                            "status": "PASS",
                            "effective_timeout_seconds": 75,
                            "safety_cap_seconds": 240,
                        }
                    ],
                }
            ],
        }
    ]


class RuntimeKnobAuditTests(unittest.TestCase):
    def test_passes_when_all_execution_knobs_reach_real_attempt(self) -> None:
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
        audit = audit_runtime_knob_coverage(
            graph,
            requests,
            timeout_node_results(),
        )
        self.assertEqual("PASS", audit["status"])
        self.assertEqual([], audit["computed_but_unused"])
        self.assertEqual(1, audit["reasoning_binding_count"])
        self.assertEqual(1, audit["requests_with_dynamic_output_allowance"])
        self.assertEqual(1, audit["attempts_with_dynamic_timeout_binding"])
        self.assertEqual("PASS", audit["dynamic_timeout_binding_status"])

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

    def test_production_mode_fails_when_timeout_binding_is_missing(self) -> None:
        graph = {
            "nodes": [
                {
                    "node_id": "n1",
                    "model": "vendor/model-a",
                    "reasoning_profile": {"effort": "low"},
                }
            ]
        }
        requests = [
            {
                "model": "vendor/model-a",
                "reasoning": {"effort": "low"},
                "max_tokens": 512,
            }
        ]
        node_results = [
            {
                "node_id": "n1",
                "attempts": [
                    {
                        "attempt_index": 1,
                        "model": "vendor/model-a",
                        "request": requests[0],
                        "answer_transformations": [],
                    }
                ],
            }
        ]
        audit = audit_runtime_knob_coverage(graph, requests, node_results)
        self.assertEqual("FAIL", audit["status"])
        self.assertEqual("FAIL", audit["dynamic_timeout_binding_status"])
        self.assertTrue(
            any(
                row.get("parameter") == "dynamic-model-timeout"
                for row in audit["computed_but_unused"]
            )
        )


if __name__ == "__main__":
    unittest.main()
