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
    def _node(self, provider: dict) -> SelectedNode:
        return SelectedNode(
            node_id="n1",
            assigned_work=("analysis",),
            professional_capabilities={},
            functions=("analysis",),
            prompt_profile={},
            reasoning_profile={},
            parameter_profile={},
            model="company/model",
            provider_endpoint="company/model@primary",
            output_contract={},
            estimated_quality=0.8,
            quality_uncertainty=0.1,
            estimated_cost=0.01,
            request_config={"provider": provider},
        )

    def test_provider_lock_accepts_only_explicit_identical_pool(self) -> None:
        provider = {
            "only": ["primary", "secondary"],
            "order": ["primary", "secondary"],
            "allow_fallbacks": True,
            "require_parameters": True,
        }
        self.assertTrue(canonical_provider_lock({"provider": provider}))
        self.assertFalse(
            canonical_provider_lock(
                {
                    "provider": {
                        "order": ["primary", "secondary"],
                        "allow_fallbacks": True,
                        "require_parameters": True,
                    }
                }
            )
        )
        self.assertFalse(
            canonical_provider_lock(
                {
                    "provider": {
                        "only": ["primary", "secondary"],
                        "order": ["primary"],
                        "allow_fallbacks": True,
                        "require_parameters": True,
                    }
                }
            )
        )

    def test_materializer_keeps_primary_first_and_all_qualified_routes(self) -> None:
        catalog = {
            "endpoints": [
                {
                    "model": "company/model",
                    "provider": "secondary",
                    "prompt_price_per_million": 1.0,
                    "completion_price_per_million": 1.0,
                },
                {
                    "model": "company/model",
                    "provider": "primary",
                    "prompt_price_per_million": 3.0,
                    "completion_price_per_million": 3.0,
                },
                {
                    "model": "other/model",
                    "provider": "unrelated",
                    "prompt_price_per_million": 0.1,
                    "completion_price_per_million": 0.1,
                },
            ]
        }
        order = materializer._provider_order(
            catalog,
            "company/model",
            "primary",
        )
        self.assertEqual(order, ["primary", "secondary"])
        request = materializer._pooled_request({}, order)
        self.assertEqual(request["provider"]["only"], order)
        self.assertEqual(request["provider"]["order"], order)
        self.assertTrue(request["provider"]["allow_fallbacks"])

    def test_runtime_rejects_unrestricted_or_mismatched_fallback(self) -> None:
        good = {
            "only": ["primary", "secondary"],
            "order": ["primary", "secondary"],
            "allow_fallbacks": True,
            "require_parameters": True,
        }
        base_payload = {"messages": [{"role": "system", "content": "x"}]}
        with patch.object(
            v5_runtime._legacy.cost_hardening,
            "hardened_build_node_payload",
            return_value={**base_payload, "provider": good},
        ), patch.object(
            v5_runtime._legacy.dynamic_prompt,
            "dynamic_system_prompt",
            return_value="system",
        ):
            payload = v5_runtime.PromptPolicy().build_payload(
                self._node(good),
                "task",
                [],
            )
        self.assertEqual(payload["provider"]["only"], good["only"])

        bad = {
            "order": ["primary", "secondary"],
            "allow_fallbacks": True,
            "require_parameters": True,
        }
        with patch.object(
            v5_runtime._legacy.cost_hardening,
            "hardened_build_node_payload",
            return_value={**base_payload, "provider": bad},
        ), patch.object(
            v5_runtime._legacy.dynamic_prompt,
            "dynamic_system_prompt",
            return_value="system",
        ):
            with self.assertRaisesRegex(RuntimeError, "audited whitelist"):
                v5_runtime.PromptPolicy().build_payload(
                    self._node(bad),
                    "task",
                    [],
                )


if __name__ == "__main__":
    unittest.main()
