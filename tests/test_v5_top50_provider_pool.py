from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_soft_proposal_materializer as materializer  # noqa: E402
from v5_production_expert_policy import ProductionExpertPromptPolicy  # noqa: E402
from v5_provider_lock import canonical_provider_lock, provider_routing_is_unrestricted  # noqa: E402
from v5_soft_resource_governance import SoftResourcePromptPolicy  # noqa: E402


class Top50ProviderPoolTests(unittest.TestCase):
    def test_open_route_rejects_every_provider_restriction(self) -> None:
        self.assertTrue(provider_routing_is_unrestricted({}))
        self.assertTrue(provider_routing_is_unrestricted({"provider": {"allow_fallbacks": True}}))
        for provider in (
            {"only": ["a"]},
            {"order": ["a", "b"]},
            {"ignore": ["a"]},
            {"zdr": True},
            {"data_collection": "deny"},
            {"max_price": {"prompt": 1}},
            {"quantizations": ["fp8"]},
            {"require_parameters": True},
            {"allow_fallbacks": False},
        ):
            self.assertFalse(provider_routing_is_unrestricted({"provider": provider}), provider)

    def test_legacy_exact_lock_remains_validator_compatible_only(self) -> None:
        legacy = {
            "provider": {
                "only": ["primary"],
                "order": ["primary"],
                "allow_fallbacks": False,
                "require_parameters": True,
            }
        }
        self.assertTrue(canonical_provider_lock(legacy))
        self.assertFalse(provider_routing_is_unrestricted(legacy))

    def test_materializer_strips_provider_config(self) -> None:
        request = materializer._open_request(
            {
                "provider": {
                    "only": ["primary"],
                    "order": ["primary"],
                    "zdr": True,
                    "data_collection": "deny",
                },
                "reasoning": {"effort": "high"},
            }
        )
        self.assertNotIn("provider", request)
        self.assertEqual(request["reasoning"]["effort"], "high")

    def test_production_policy_removes_provider_object_even_if_base_adds_one(self) -> None:
        base_payload = {
            "model": "company/model",
            "messages": [{"role": "system", "content": "x"}],
            "provider": {
                "only": ["primary"],
                "order": ["primary"],
                "allow_fallbacks": False,
                "require_parameters": True,
                "zdr": True,
            },
        }
        with patch.object(SoftResourcePromptPolicy, "build_payload", return_value=base_payload):
            payload = ProductionExpertPromptPolicy().build_payload(
                SimpleNamespace(node_id="n1"),
                "task",
                [],
            )
        self.assertNotIn("provider", payload)
        self.assertEqual(payload["model"], "company/model")


if __name__ == "__main__":
    unittest.main()
