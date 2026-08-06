from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import SelectedNode  # noqa: E402
import v5_runtime  # noqa: E402
import v5_soft_proposal_materializer as materializer  # noqa: E402
from v5_provider_lock import canonical_provider_lock  # noqa: E402


class Top50ProviderPoolTests(unittest.TestCase):
    def _node(self, request_config: dict) -> SelectedNode:
        return SelectedNode(
            node_id="n1",
            assigned_work=("analysis",),
            professional_capabilities={},
            functions=("analysis",),
            prompt_profile={},
            reasoning_profile={},
            parameter_profile={},
            model="company/model",
            provider_endpoint="company/model@legacy-primary",
            output_contract={},
            estimated_quality=0.8,
            quality_uncertainty=0.1,
            estimated_cost=0.01,
            request_config=request_config,
        )

    def test_open_route_rejects_every_provider_restriction(self) -> None:
        self.assertTrue(canonical_provider_lock({}))
        self.assertTrue(canonical_provider_lock({"provider": {"allow_fallbacks": True}}))
        for provider in (
            {"only": ["a"]},
            {"order": ["a", "b"]},
            {"ignore": ["a"]},
            {"zdr": True},
            {"data_collection": "deny"},
            {"max_price": {"prompt": 1}},
            {"quantizations": ["fp8"]},
            {"allow_fallbacks": False},
        ):
            self.assertFalse(canonical_provider_lock({"provider": provider}), provider)

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

    def test_runtime_removes_provider_object_even_if_legacy_builder_adds_one(self) -> None:
        base_payload = {
            "messages": [{"role": "system", "content": "x"}],
            "provider": {
                "only": ["primary"],
                "order": ["primary"],
                "allow_fallbacks": False,
                "require_parameters": True,
                "zdr": True,
            },
        }
        with patch.object(
            v5_runtime._legacy.cost_hardening,
            "hardened_build_node_payload",
            return_value=base_payload,
        ), patch.object(
            v5_runtime._legacy.dynamic_prompt,
            "dynamic_system_prompt",
            return_value="system",
        ):
            payload = v5_runtime.PromptPolicy().build_payload(
                self._node({}),
                "task",
                [],
            )
        self.assertNotIn("provider", payload)
        self.assertEqual(payload["model"] if "model" in payload else "company/model", "company/model")


if __name__ == "__main__":
    unittest.main()
