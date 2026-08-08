from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_quality_status_integrity import enforce_result_integrity  # noqa: E402


class ProviderAccountTerminalReasonTests(unittest.TestCase):
    def test_account_credit_failure_survives_degraded_integrity_rewrite(self) -> None:
        result = {
            "status": "success",
            "completion_mode": "degraded",
            "quality_status": "degraded_success",
            "stop_reason": "partial-success-deterministic-synthesis",
            "final_answer": "# V5降级合成结果\n\n## 未覆盖工作\nw1",
            "node_results": [
                {
                    "node_id": "n1",
                    "status": "failed",
                    "contract": {"required_fields_complete": False},
                    "attempts": [],
                }
            ],
            "work_coverage": {
                "coverage_ratio": 0.0,
                "minimum_degraded_coverage": 0.0,
                "successful_content_nodes": 0,
            },
            "delivery_policy": {
                "allow_degraded_success": True,
                "blockers": [],
                "missing_non_degradable_work_ids": [],
            },
            "provider_account_transport_state": {
                "blocked": True,
                "reason": "openrouter-http-402-insufficient-credits",
                "model_replacement_can_repair": False,
            },
            "degradation": {"used": True},
        }

        normalized = enforce_result_integrity(result)

        self.assertEqual(normalized["status"], "failed")
        self.assertEqual(
            normalized["stop_reason"],
            "provider-account-credit-insufficient",
        )
        self.assertTrue(normalized["degradation"]["root_cause_preserved"])
        self.assertEqual(normalized["quality_integrity"]["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
