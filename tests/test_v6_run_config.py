from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "open-model-market"
if str(MODULE) not in sys.path:
    sys.path.insert(0, str(MODULE))

from v6_production_ticket import _run_config  # noqa: E402


class V6RunConfigTests(unittest.TestCase):
    def test_governance_completion_profile_is_used_exactly(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ,
            {
                "OPENROUTER_API_KEY": "test-only-key",
                "CATALOG_TIMEOUT_SECONDS": "31",
                "CATALOG_MAX_RETRIES": "1",
                "MODEL_TIMEOUT_SECONDS": "241",
                "PARALLEL_WORKERS": "3",
            },
            clear=False,
        ):
            run = _run_config(
                "task",
                Path(tmp),
                completion_tokens=7_777,
                maximum_total_calls=5,
                maximum_recovery_calls=1,
                require_live_catalog=True,
            )
        self.assertEqual(run.max_completion_tokens, 7_777)
        self.assertEqual(run.maximum_total_calls, 5)
        self.assertEqual(run.maximum_recovery_calls, 1)
        self.assertEqual(run.maximum_replacements, 1)
        self.assertEqual(run.minimum_context_length, 8_192)
        self.assertEqual(
            run.catalog_sorts,
            ["intelligence-high-to-low", "pricing-low-to-high"],
        )
        self.assertEqual(run.reasoning_effort, "medium")
        self.assertEqual(run.temperature, 0.0)
        self.assertEqual(run.model_max_retries, 0)
        self.assertEqual(run.parallel_workers, 3)
        self.assertTrue(run.require_live_catalog)
        self.assertFalse(run.provider["allow_fallbacks"])
        self.assertTrue(run.provider["require_parameters"])
        self.assertTrue(run.provider["explicit_provider_lock_required"])

    def test_missing_key_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ,
            {"OPENROUTER_API_KEY": ""},
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "OPENROUTER_API_KEY"):
                _run_config(
                    "task",
                    Path(tmp),
                    completion_tokens=2_000,
                    maximum_total_calls=3,
                    maximum_recovery_calls=0,
                    require_live_catalog=True,
                )

    def test_non_positive_completion_profile_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ,
            {"OPENROUTER_API_KEY": "test-only-key"},
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "must be positive"):
                _run_config(
                    "task",
                    Path(tmp),
                    completion_tokens=0,
                    maximum_total_calls=3,
                    maximum_recovery_calls=0,
                    require_live_catalog=True,
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
