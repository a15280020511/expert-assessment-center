from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from openrouter_api import OpenRouterRequestError, request_json  # noqa: E402


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class OpenRouterHardDeadlineTests(unittest.TestCase):
    def test_complete_request_is_bounded_by_wall_clock_deadline(self) -> None:
        def slow_urlopen(*_args, **_kwargs):
            time.sleep(0.40)
            return _Response({"ok": True})

        started = time.monotonic()
        with patch("openrouter_api.urllib.request.urlopen", slow_urlopen):
            with self.assertRaises(OpenRouterRequestError) as raised:
                request_json(
                    "https://example.invalid/chat",
                    "key",
                    0.05,
                    0,
                    {"model": "test"},
                )
        elapsed = time.monotonic() - started
        self.assertEqual("timeout", raised.exception.category)
        self.assertTrue(raised.exception.retryable)
        self.assertLess(elapsed, 0.30)

    def test_fast_response_still_decodes_normally(self) -> None:
        with patch(
            "openrouter_api.urllib.request.urlopen",
            return_value=_Response({"ok": True}),
        ):
            value = request_json(
                "https://example.invalid/models",
                "key",
                1.0,
                0,
            )
        self.assertEqual({"ok": True}, value)


if __name__ == "__main__":
    unittest.main()
