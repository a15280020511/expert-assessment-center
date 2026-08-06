import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_catalog_view import CatalogViewError  # noqa: E402
from v5_endpoint_catalog import (  # noqa: E402
    ZDR_ENDPOINTS_URL,
    fetch_live_endpoint_payloads,
)


class V5EndpointCatalogTests(unittest.TestCase):
    @staticmethod
    def _zdr_rows(models):
        return [
            {
                "model_id": model.id,
                "tag": f"provider-{index}",
            }
            for index, model in enumerate(models)
        ]

    def test_fetch_is_concurrent_bounded_and_deterministic(self):
        models = [
            SimpleNamespace(id=f"company-{index}/model-{index}")
            for index in range(20)
        ]
        run = SimpleNamespace(
            api_key="test-key",
            catalog_timeout_seconds=5,
            catalog_max_retries=0,
        )
        lock = threading.Lock()
        active = 0
        maximum_active = 0
        endpoint_calls = 0

        def fake_request(url, api_key, timeout, retries):
            nonlocal active, maximum_active, endpoint_calls
            self.assertEqual(api_key, "test-key")
            self.assertEqual(timeout, 5)
            self.assertEqual(retries, 0)
            if url == ZDR_ENDPOINTS_URL:
                return {"data": self._zdr_rows(models)}
            index = int(url.split("model-")[-1].split("/")[0])
            with lock:
                active += 1
                endpoint_calls += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.01)
            with lock:
                active -= 1
            return {
                "url": url,
                "data": {
                    "endpoints": [
                        {"tag": f"provider-{index}"},
                        {"tag": "not-zdr"},
                    ]
                },
            }

        with patch(
            "v5_endpoint_catalog.request_json",
            side_effect=fake_request,
        ) as request:
            result = fetch_live_endpoint_payloads(
                models,
                run,
                maximum_models=15,
                maximum_workers=4,
            )

        expected = [model.id for model in models[:15]]
        self.assertEqual(list(result), expected)
        self.assertEqual(endpoint_calls, 15)
        self.assertEqual(request.call_count, 16)
        self.assertGreater(maximum_active, 1)
        self.assertLessEqual(maximum_active, 4)
        for index, model in enumerate(models[:15]):
            endpoints = result[model.id]["data"]["endpoints"]
            self.assertEqual([{"tag": f"provider-{index}"}], endpoints)
            self.assertTrue(result[model.id]["zdr_endpoint_filter"]["required"])

    def test_maximum_models_is_applied_before_scheduling(self):
        models = [
            SimpleNamespace(id=f"company-{index}/model-{index}")
            for index in range(12)
        ]
        run = SimpleNamespace(
            api_key="test-key",
            catalog_timeout_seconds=5,
            catalog_max_retries=0,
        )

        def fake_request(url, *_args):
            if url == ZDR_ENDPOINTS_URL:
                return {"data": self._zdr_rows(models)}
            index = int(url.split("model-")[-1].split("/")[0])
            return {"data": {"endpoints": [{"tag": f"provider-{index}"}]}}

        with patch(
            "v5_endpoint_catalog.request_json",
            side_effect=fake_request,
        ) as request:
            result = fetch_live_endpoint_payloads(
                models,
                run,
                maximum_models=7,
                maximum_workers=16,
            )
        self.assertEqual(list(result), [model.id for model in models[:7]])
        self.assertEqual(request.call_count, 8)

    def test_missing_zdr_inventory_fails_closed_before_endpoint_fetch(self):
        models = [SimpleNamespace(id="deepseek/model")]
        run = SimpleNamespace(
            api_key="test-key",
            catalog_timeout_seconds=5,
            catalog_max_retries=0,
        )
        with patch(
            "v5_endpoint_catalog.request_json",
            return_value={"data": []},
        ) as request:
            with self.assertRaises(CatalogViewError):
                fetch_live_endpoint_payloads(models, run)
        request.assert_called_once_with(ZDR_ENDPOINTS_URL, "test-key", 5, 0)


if __name__ == "__main__":
    unittest.main()
