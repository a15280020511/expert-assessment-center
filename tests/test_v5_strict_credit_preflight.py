import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_live_benchmark_economy as economy  # noqa: E402


class TestSingleKeyEconomyCreditPreflight(unittest.TestCase):
    @staticmethod
    def _write_config(root: Path) -> Path:
        path = root / "benchmark-config.json"
        path.write_text(
            json.dumps(
                {
                    "max_cost_usd": 1.5,
                    "max_calls": 46,
                    "output_allowance_tokens": 1800,
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_unbounded_standard_api_key_is_accepted_under_hard_ceiling(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self._write_config(root)
            key_payload = {
                "data": {
                    "label": "unbounded-key",
                    "limit": None,
                    "limit_remaining": None,
                    "usage": 1.9777,
                }
            }
            with patch.object(
                economy.hardened,
                "request_json",
                return_value=key_payload,
            ), patch.dict(
                os.environ,
                {"OPENROUTER_API_KEY": "test-key"},
                clear=False,
            ):
                code = economy.credit_preflight(config, root)

            report = json.loads(
                (root / "credit-preflight.json").read_text(encoding="utf-8")
            )
            self.assertEqual(code, 0)
            self.assertEqual(report["status"], "bounded-key-accepted")
            self.assertFalse(report["blockers"])
            self.assertEqual(report["model_inference_calls"], 0)
            self.assertFalse(report["production_entrypoint_changed"])
            self.assertFalse(report["v3_deleted"])

    def test_known_low_finite_key_limit_is_rejected_before_model_calls(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self._write_config(root)
            key_payload = {
                "data": {
                    "label": "bounded-key",
                    "limit": 2.0,
                    "limit_remaining": 0.25,
                    "usage": 1.75,
                }
            }
            with patch.object(
                economy.hardened,
                "request_json",
                return_value=key_payload,
            ), patch.dict(
                os.environ,
                {"OPENROUTER_API_KEY": "test-key"},
                clear=False,
            ):
                code = economy.credit_preflight(config, root)

            report = json.loads(
                (root / "credit-preflight.json").read_text(encoding="utf-8")
            )
            self.assertEqual(code, 3)
            self.assertIn(
                "api-key-limit-remaining-below-economy-reserve",
                report["blockers"],
            )
            self.assertEqual(report["model_inference_calls"], 0)

    def test_formal_economy_workflow_requires_only_standard_api_key(self):
        workflow = (
            ROOT / ".github" / "workflows" / "v5-live-benchmark-final.yml"
        ).read_text(encoding="utf-8")
        verified_source = (
            ROOT
            / "open-model-market"
            / "v5_live_benchmark_economy_verified.py"
        ).read_text(encoding="utf-8")
        self.assertIn("secrets.OPENROUTER_API_KEY", workflow)
        self.assertNotIn("secrets.OPENROUTER_MANAGEMENT_KEY", workflow)
        self.assertNotIn("verified-account-credit-reserve-required", verified_source)
        self.assertNotIn("OPENROUTER_MANAGEMENT_KEY", verified_source)


if __name__ == "__main__":
    unittest.main()
