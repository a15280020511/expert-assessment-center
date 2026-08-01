from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from v5_recovery_runtime import CrossEndpointPlannerPolicy  # noqa: E402
from v5_runtime import RuntimeConfig  # noqa: E402


class V5OperationalResilienceScopeTests(unittest.TestCase):
    @staticmethod
    def config(*, live: bool) -> RuntimeConfig:
        return RuntimeConfig(
            total_call_limit=5,
            recovery_call_limit=1,
            cost_anomaly_usd=0.35,
            quality_tier="value",
            tools_allowed=False,
            live_catalog_required=live,
            provider_lock_required=True,
        )

    @staticmethod
    def insufficient_result() -> dict:
        return {
            "execution_graph": {
                "nodes": [
                    {"node_id": f"node-{index}", "failure_probability": 0.20}
                    for index in range(4)
                ],
                "metadata": {"recovery_pool": {}},
            }
        }

    def test_synthetic_dry_run_retains_diagnostics_without_live_enforcement(self) -> None:
        policy = CrossEndpointPlannerPolicy(self.config(live=False))
        assessment = policy._assess_recovery_sufficiency(
            self.insufficient_result()
        )
        self.assertEqual("PASS", assessment["status"])
        self.assertFalse(assessment["enforced"])
        self.assertEqual([], assessment["blockers"])
        self.assertIn(
            "unrecoverable-failure-tail-above-limit",
            assessment["diagnostic_blockers"],
        )

    def test_live_catalog_fails_closed_on_insufficient_recovery(self) -> None:
        policy = CrossEndpointPlannerPolicy(self.config(live=True))
        assessment = policy._assess_recovery_sufficiency(
            self.insufficient_result()
        )
        self.assertEqual("FAIL", assessment["status"])
        self.assertTrue(assessment["enforced"])
        self.assertIn(
            "unrecoverable-failure-tail-above-limit",
            assessment["blockers"],
        )
        self.assertIn(
            "selected-node-without-executable-recovery",
            assessment["blockers"],
        )


if __name__ == "__main__":
    unittest.main()
