import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_executor  # noqa: E402
import v5_live_benchmark  # noqa: E402
import v5_live_benchmark_hardened as hardened  # noqa: E402


class TestV5LiveBenchmarkHardening(unittest.TestCase):
    def config(self, root: Path, max_cost: float = 20.0) -> Path:
        path = root / "benchmark-config.json"
        path.write_text(json.dumps({"max_cost_usd": max_cost}), encoding="utf-8")
        return path

    def test_credit_preflight_rejects_low_api_key_remaining_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self.config(root)
            payload = {
                "data": {
                    "label": "benchmark-key",
                    "limit": 20,
                    "limit_remaining": 0.02,
                    "usage": 19.98,
                    "is_free_tier": False,
                    "is_management_key": False,
                }
            }
            with patch.object(hardened, "request_json", return_value=payload), patch.dict(
                os.environ,
                {"OPENROUTER_API_KEY": "test-key", "OPENROUTER_MANAGEMENT_KEY": ""},
                clear=False,
            ):
                code = hardened.credit_preflight(config, root)
            self.assertEqual(code, 3)
            report = json.loads((root / "credit-preflight.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "insufficient")
            self.assertIn("api-key-limit-remaining-below-benchmark-reserve", report["blockers"])

    def test_credit_preflight_rejects_unbounded_key_without_remaining_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self.config(root)
            payload = {
                "data": {
                    "label": "unbounded-key",
                    "limit": None,
                    "limit_remaining": None,
                    "usage": 1.9,
                    "is_free_tier": False,
                    "is_management_key": False,
                }
            }
            with patch.object(hardened, "request_json", return_value=payload), patch.dict(
                os.environ,
                {"OPENROUTER_API_KEY": "test-key", "OPENROUTER_MANAGEMENT_KEY": ""},
                clear=False,
            ):
                code = hardened.credit_preflight(config, root)
            self.assertEqual(code, 3)
            report = json.loads((root / "credit-preflight.json").read_text(encoding="utf-8"))
            self.assertIn("finite-api-key-spending-limit-required", report["blockers"])

    def test_credit_preflight_accepts_sufficient_key_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self.config(root, 12.0)
            payload = {
                "data": {
                    "label": "benchmark-key",
                    "limit": 50,
                    "limit_remaining": 30,
                    "usage": 20,
                    "is_free_tier": False,
                    "is_management_key": False,
                }
            }
            with patch.object(hardened, "request_json", return_value=payload), patch.dict(
                os.environ,
                {"OPENROUTER_API_KEY": "test-key", "OPENROUTER_MANAGEMENT_KEY": ""},
                clear=False,
            ):
                code = hardened.credit_preflight(config, root)
            self.assertEqual(code, 0)
            report = json.loads((root / "credit-preflight.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "key_limit_verified")
            self.assertFalse(report["blockers"])

    def test_credit_error_detection_covers_openrouter_402(self):
        self.assertTrue(hardened._is_credit_error("HTTP 402: This request requires more credits"))
        self.assertFalse(hardened._is_credit_error("HTTP 429 rate limited"))

    def test_output_allowance_does_not_suppress_execution_exception(self):
        original_safe = v5_live_benchmark._safe_payload
        original_node = v5_executor.build_node_payload
        original_execute = v5_live_benchmark.execute_v5_graph

        def boom(*args, **kwargs):
            raise RuntimeError("preserve-me")

        try:
            v5_live_benchmark.execute_v5_graph = boom
            hardened._install_output_allowance()
            with self.assertRaisesRegex(RuntimeError, "preserve-me"):
                v5_live_benchmark.execute_v5_graph(output_dir=None)
        finally:
            v5_live_benchmark._safe_payload = original_safe
            v5_executor.build_node_payload = original_node
            v5_live_benchmark.execute_v5_graph = original_execute
            importlib.reload(hardened)

    def test_benchmark_uses_max_tokens_when_endpoint_advertises_it(self):
        original_safe = v5_live_benchmark._safe_payload
        original_node = v5_executor.build_node_payload
        original_execute = v5_live_benchmark.execute_v5_graph
        endpoint = {
            "model_id": "vendor/model",
            "provider_slug": "provider",
            "supported_parameters": ["reasoning", "temperature", "max_tokens"],
        }
        try:
            hardened._install_output_allowance()
            payload = v5_live_benchmark._safe_payload(endpoint, "system", "user")
            self.assertEqual(payload["max_tokens"], 10000)
            self.assertNotIn("max_completion_tokens", payload)
            self.assertNotIn("tools", payload)
        finally:
            v5_live_benchmark._safe_payload = original_safe
            v5_executor.build_node_payload = original_node
            v5_live_benchmark.execute_v5_graph = original_execute
            importlib.reload(hardened)

    def test_benchmark_uses_max_completion_tokens_only_when_advertised(self):
        original_safe = v5_live_benchmark._safe_payload
        original_node = v5_executor.build_node_payload
        original_execute = v5_live_benchmark.execute_v5_graph
        endpoint = {
            "model_id": "vendor/model",
            "provider_slug": "provider",
            "supported_parameters": ["reasoning", "max_completion_tokens"],
        }
        try:
            hardened._install_output_allowance()
            payload = v5_live_benchmark._safe_payload(endpoint, "system", "user")
            self.assertEqual(payload["max_completion_tokens"], 10000)
            self.assertNotIn("max_tokens", payload)
        finally:
            v5_live_benchmark._safe_payload = original_safe
            v5_executor.build_node_payload = original_node
            v5_live_benchmark.execute_v5_graph = original_execute
            importlib.reload(hardened)


if __name__ == "__main__":
    unittest.main()
