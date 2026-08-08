from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from v5_runtime_timeout import (  # noqa: E402
    dynamic_model_timeout_seconds,
    with_model_timeout,
)


def node(multiplier: float = 1.0) -> SimpleNamespace:
    return SimpleNamespace(
        node_id="n1",
        parameter_profile={"dynamic_model_timeout_multiplier": multiplier},
    )


def payload(*, chars: int, max_tokens: int, effort: str) -> dict:
    return {
        "model": "vendor/model",
        "messages": [{"role": "user", "content": "X" * chars}],
        "max_tokens": max_tokens,
        "reasoning": {"effort": effort},
    }


class RuntimeTimeoutTests(unittest.TestCase):
    def test_timeout_is_current_request_derived_under_safety_cap(self) -> None:
        small, small_audit = dynamic_model_timeout_seconds(
            node(),
            payload(chars=400, max_tokens=600, effort="low"),
            240,
        )
        large, large_audit = dynamic_model_timeout_seconds(
            node(),
            payload(chars=8000, max_tokens=5000, effort="high"),
            240,
        )
        self.assertGreaterEqual(small, 30)
        self.assertGreaterEqual(large, small)
        self.assertLessEqual(large, 240)
        self.assertEqual("current-request-shape", large_audit["effective_timeout_source"])
        self.assertFalse(large_audit["safety_cap_is_business_gate"])

    def test_current_run_multiplier_can_expand_without_relaxing_cap(self) -> None:
        request = payload(chars=2000, max_tokens=1500, effort="medium")
        base, _ = dynamic_model_timeout_seconds(node(1.0), request, 240)
        expanded, audit = dynamic_model_timeout_seconds(node(2.0), request, 240)
        self.assertGreaterEqual(expanded, base)
        self.assertLessEqual(expanded, 240)
        self.assertEqual(2.0, audit["current_run_timeout_multiplier"])
        self.assertEqual(240, audit["safety_cap_seconds"])

    def test_run_config_is_cloned_for_effective_timeout(self) -> None:
        original = SimpleNamespace(
            api_key="fixture",
            model_timeout_seconds=240,
            other="preserved",
        )
        cloned = with_model_timeout(original, 55)
        self.assertIsNot(cloned, original)
        self.assertEqual(55, cloned.model_timeout_seconds)
        self.assertEqual("fixture", cloned.api_key)
        self.assertEqual("preserved", cloned.other)
        self.assertEqual(240, original.model_timeout_seconds)


if __name__ == "__main__":
    unittest.main()
