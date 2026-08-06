from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

import v5_price_ranked_ticket_gate as gate  # noqa: E402


class Top50TicketGateCapacityTests(unittest.TestCase):
    def _fixture(self, root: Path, *, calls: int = 8, recovery: int = 4) -> None:
        status = {
            "accepted": True,
            "runtime_version": "v5-governance-top50-ortools-open-provider-runtime-1",
            "calls": calls,
            "maximum_recovery_calls": recovery,
            "maximum_initial_calls": calls - recovery,
            "selected_expert_count": 4,
            "selected_recovery_count": 4,
            "optimizer": "ortools-cp-sat",
            "optimizer_optimality_proven": True,
            "claude_mechanism_enabled": False,
            "governance_model_calls": 0,
            "provider_routing_mode": "unrestricted-openrouter",
            "provider_restrictions_applied": False,
            "provider_fallback_allowed": True,
            "unrestricted_provider_fallback_allowed": True,
            "openrouter_selects_provider": True,
            "model_substitution_allowed": False,
            "cost_anomaly_usd": None,
        }
        (root / "ticket-status.json").write_text(json.dumps(status), encoding="utf-8")
        (root / "ticket.json").write_text(json.dumps({"route": "expert-team"}), encoding="utf-8")
        (root / "task.txt").write_text("task", encoding="utf-8")

    def _run(self, root: Path, *, calls: int, recovery: int) -> int:
        argv = [
            "v5_price_ranked_ticket_gate.py",
            "--output-dir",
            str(root),
            "--expected-calls",
            str(calls),
            "--expected-recovery-calls",
            str(recovery),
        ]
        with patch.object(sys, "argv", argv):
            return gate.main()

    def test_exact_four_plus_four_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._fixture(root, calls=8, recovery=4)
            self.assertEqual(self._run(root, calls=8, recovery=4), 0)

    def test_less_than_four_recovery_calls_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._fixture(root, calls=8, recovery=1)
            with self.assertRaisesRegex(RuntimeError, "exactly four warm recovery"):
                self._run(root, calls=8, recovery=1)

    def test_missing_four_primary_capacity_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._fixture(root, calls=7, recovery=4)
            with self.assertRaisesRegex(RuntimeError, "8-16 approved total calls"):
                self._run(root, calls=7, recovery=4)


if __name__ == "__main__":
    unittest.main()
