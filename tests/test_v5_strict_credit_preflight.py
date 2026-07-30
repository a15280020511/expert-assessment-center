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
import v5_live_benchmark_economy_verified as verified  # noqa: E402


class TestStrictEconomyCreditPreflight(unittest.TestCase):
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

    def test_unbounded_key_without_management_credit_evidence_fails_closed(self):
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
                {
                    "OPENROUTER_API_KEY": "test-key",
                    "OPENROUTER_MANAGEMENT_KEY": "",
                },
                clear=False,
            ):
                code = verified.verified_credit_preflight(config, root)

            report = json.loads(
                (root / "credit-preflight.json").read_text(encoding="utf-8")
            )
            self.assertEqual(code, 3)
            self.assertEqual(report["status"], "insufficient")
            self.assertIn(
                "verified-account-credit-reserve-required",
                report["blockers"],
            )
            self.assertEqual(report["model_inference_calls"], 0)
            self.assertFalse(report["production_entrypoint_changed"])
            self.assertFalse(report["v3_deleted"])

    def test_management_key_with_full_account_reserve_allows_preflight(self):
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
            credits_payload = {
                "data": {
                    "total_credits": 5.0,
                    "total_usage": 3.0,
                }
            }
            with patch.object(
                economy.hardened,
                "request_json",
                side_effect=[key_payload, credits_payload],
            ), patch.dict(
                os.environ,
                {
                    "OPENROUTER_API_KEY": "test-key",
                    "OPENROUTER_MANAGEMENT_KEY": "management-key",
                },
                clear=False,
            ):
                code = verified.verified_credit_preflight(config, root)

            report = json.loads(
                (root / "credit-preflight.json").read_text(encoding="utf-8")
            )
            self.assertEqual(code, 0)
            self.assertEqual(report["status"], "verified")
            self.assertEqual(report["account_remaining_usd"], 2.0)
            self.assertFalse(report["blockers"])

    def test_verified_installation_replaces_the_weaker_economy_preflight(self):
        source = (
            ROOT
            / "open-model-market"
            / "v5_live_benchmark_economy_verified.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "economy.hardened.credit_preflight = verified_credit_preflight",
            source,
        )


if __name__ == "__main__":
    unittest.main()
