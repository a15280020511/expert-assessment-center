import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_endpoint_catalog import fetch_live_endpoint_payloads  # noqa: E402


class TestV5EndpointCatalog(unittest.TestCase):
    def test_top_pool_endpoint_fetch_is_concurrent_bounded_and_deterministic(self):
        models = [SimpleNamespace(id=f"company-{index}/model-{index}") for index in range(20)]
        run = SimpleNamespace(
            api_key="test-key",
            catalog_timeout_seconds=5,
            catalog_max_retries=0,
        )
        lock = threading.Lock()
        active = 0
        maximum_active = 0
        calls = 0

        def fake_request(url, api_key, timeout, retries):
            nonlocal active, maximum_active, calls
            self.assertEqual(api_key, "test-key")
            self.assertEqual(timeout, 5)
            self.assertEqual(retries, 0)
            with lock:
                active += 1
                calls += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.01)
            with lock:
                active -= 1
            return {"url": url, "data": {"endpoints": []}}

        with patch("v5_endpoint_catalog.request_json", side_effect=fake_request):
            result = fetch_live_endpoint_payloads(
                models,
                run,
                maximum_models=15,
                maximum_workers=4,
            )

        expected = [model.id for model in models[:15]]
        self.assertEqual(list(result), expected)
        self.assertEqual(calls, 15)
        self.assertGreater(maximum_active, 1)
        self.assertLessEqual(maximum_active, 4)

    def test_maximum_models_is_applied_before_scheduling(self):
        models = [SimpleNamespace(id=f"company-{index}/model-{index}") for index in range(12)]
        run = SimpleNamespace(
            api_key="test-key",
            catalog_timeout_seconds=5,
            catalog_max_retries=0,
        )
        with patch(
            "v5_endpoint_catalog.request_json",
            return_value={"data": {"endpoints": []}},
        ) as request:
            result = fetch_live_endpoint_payloads(
                models,
                run,
                maximum_models=7,
                maximum_workers=16,
            )
        self.assertEqual(list(result), [model.id for model in models[:7]])
        self.assertEqual(request.call_count, 7)


if __name__ == "__main__":
    unittest.main()
