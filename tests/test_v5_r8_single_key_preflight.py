import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_r8_single_key_preflight as preflight  # noqa: E402


class TestV5R8SingleKeyPreflight(unittest.TestCase):
    def test_finite_ordinary_key_limit_is_sufficient(self):
        calls = []

        def request(url, key, timeout, retries):
            calls.append((url, key, timeout, retries))
            return {
                "data": {
                    "label": "ordinary-key",
                    "limit": 5.0,
                    "limit_remaining": 2.0,
                    "usage": 3.0,
                    "is_free_tier": False,
                }
            }

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "credit-preflight.json"
            report = preflight.check_single_api_key(
                "sk-test",
                0.25,
                request_fn=request,
                output_path=path,
            )
            self.assertEqual(report["status"], "verified-finite-key-limit")
            self.assertEqual(report["blockers"], [])
            self.assertEqual(report["model_inference_calls"], 0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], preflight.CURRENT_KEY_URL)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), report)

    def test_insufficient_key_limit_blocks_before_inference(self):
        report = preflight.check_single_api_key(
            "sk-test",
            0.25,
            request_fn=lambda *_: {
                "data": {"limit": 1.0, "limit_remaining": 0.10, "usage": 0.90}
            },
        )
        self.assertEqual(report["status"], "insufficient")
        self.assertIn(
            "api-key-limit-remaining-below-required-reserve",
            report["blockers"],
        )
        self.assertEqual(report["model_inference_calls"], 0)

    def test_unbounded_or_unreported_limit_uses_runtime_hard_cap(self):
        report = preflight.check_single_api_key(
            "sk-test",
            0.25,
            request_fn=lambda *_: {
                "data": {"limit": None, "limit_remaining": None, "usage": 0.0}
            },
        )
        self.assertEqual(
            report["status"],
            "ordinary-key-accepted-with-runtime-hard-cap",
        )
        self.assertFalse(report["production_entrypoint_changed"])
        self.assertFalse(report["v3_deleted"])

    def test_missing_key_is_rejected(self):
        with self.assertRaisesRegex(Exception, "OPENROUTER_API_KEY"):
            preflight.check_single_api_key(
                "",
                0.25,
                request_fn=lambda *_: {},
            )

    def test_source_contains_no_second_key_contract(self):
        text = Path(preflight.__file__).read_text(encoding="utf-8")
        self.assertIn("ordinary-openrouter-api-key-only", text)
        self.assertNotIn("MANAGEMENT", text)
        self.assertNotIn("CREDITS_URL", text)
        self.assertNotIn("/api/v1/credits", text)


if __name__ == "__main__":
    unittest.main()
