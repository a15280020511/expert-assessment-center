import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_free_first_preflight as preflight  # noqa: E402


TARGET_SHA = "a" * 40


def valid_receipt():
    return {
        "schema_version": preflight.SCHEMA_VERSION,
        "target_sha": TARGET_SHA,
        "simulation": {
            "status": "PASS",
            "model_calls": 0,
            "paid_model_calls": 0,
        },
        "free_canary": {
            "status": "PASS",
            "requested_model": "openrouter/free",
            "model_requests": 1,
            "successful_model_calls": 1,
            "paid_model_calls": 0,
            "actual_cost_usd": 0.0,
        },
        "shadow_governance": None,
        "paid_acceptance_triggered": False,
        "production_ref_moved": False,
    }


def valid_shadow():
    return {
        "status": "PASS",
        "model_requests": 3,
        "successful_model_calls": 3,
        "paid_model_calls": 0,
        "total_cost_usd": 0.0,
        "formal_model_identity_qualified": False,
    }


class FreeFirstPreflightTests(unittest.TestCase):
    def test_minimal_free_first_receipt_allows_only_paid_acceptance(self):
        verdict = preflight.evaluate_free_first_preflight(
            valid_receipt(),
            expected_sha=TARGET_SHA,
        )
        self.assertEqual(verdict["status"], "PASS")
        self.assertTrue(verdict["zero_call_simulation_passed"])
        self.assertTrue(verdict["zero_cost_free_canary_passed"])
        self.assertTrue(verdict["paid_acceptance_allowed"])
        self.assertFalse(verdict["formal_model_identity_qualified"])
        self.assertFalse(verdict["merge_allowed"])
        self.assertFalse(verdict["production_promotion_allowed"])

    def test_simulation_model_call_blocks_authorization(self):
        receipt = valid_receipt()
        receipt["simulation"]["model_calls"] = 1
        verdict = preflight.evaluate_free_first_preflight(receipt)
        self.assertEqual(verdict["status"], "FAIL")
        self.assertFalse(verdict["zero_call_simulation_passed"])
        self.assertIn("simulation-used-model-calls", verdict["reasons"])

    def test_positive_free_canary_cost_blocks_authorization(self):
        receipt = valid_receipt()
        receipt["free_canary"]["actual_cost_usd"] = 0.000001
        verdict = preflight.evaluate_free_first_preflight(receipt)
        self.assertEqual(verdict["status"], "FAIL")
        self.assertFalse(verdict["zero_cost_free_canary_passed"])
        self.assertIn("free-canary-positive-cost", verdict["reasons"])

    def test_non_free_canary_model_blocks_authorization(self):
        receipt = valid_receipt()
        receipt["free_canary"]["requested_model"] = "openai/gpt-paid"
        verdict = preflight.evaluate_free_first_preflight(receipt)
        self.assertEqual(verdict["status"], "FAIL")
        self.assertIn("free-canary-model-not-free", verdict["reasons"])

    def test_target_mismatch_blocks_authorization(self):
        verdict = preflight.evaluate_free_first_preflight(
            valid_receipt(),
            expected_sha="b" * 40,
        )
        self.assertEqual(verdict["status"], "FAIL")
        self.assertIn("target-sha-mismatch", verdict["reasons"])

    def test_shadow_can_be_required(self):
        verdict = preflight.evaluate_free_first_preflight(
            valid_receipt(),
            require_shadow=True,
        )
        self.assertEqual(verdict["status"], "FAIL")
        self.assertIn("shadow-governance-required", verdict["reasons"])

    def test_valid_shadow_still_cannot_qualify_model_identity(self):
        receipt = valid_receipt()
        receipt["shadow_governance"] = valid_shadow()
        verdict = preflight.evaluate_free_first_preflight(
            receipt,
            require_shadow=True,
        )
        self.assertEqual(verdict["status"], "PASS")
        self.assertTrue(verdict["paid_acceptance_allowed"])
        self.assertFalse(verdict["formal_model_identity_qualified"])
        self.assertFalse(verdict["production_promotion_allowed"])

    def test_shadow_cannot_claim_formal_model_identity(self):
        receipt = valid_receipt()
        receipt["shadow_governance"] = valid_shadow()
        receipt["shadow_governance"]["formal_model_identity_qualified"] = True
        verdict = preflight.evaluate_free_first_preflight(receipt)
        self.assertEqual(verdict["status"], "FAIL")
        self.assertIn(
            "shadow-claimed-formal-model-identity",
            verdict["reasons"],
        )

    def test_prior_paid_trigger_and_ref_move_fail_structurally(self):
        receipt = valid_receipt()
        receipt["paid_acceptance_triggered"] = True
        receipt["production_ref_moved"] = True
        verdict = preflight.evaluate_free_first_preflight(receipt)
        self.assertEqual(verdict["status"], "FAIL")
        self.assertIn("paid-acceptance-already-triggered", verdict["reasons"])
        self.assertIn("production-ref-already-moved", verdict["reasons"])

    def test_malformed_boolean_is_rejected(self):
        receipt = deepcopy(valid_receipt())
        receipt["production_ref_moved"] = "false"
        with self.assertRaisesRegex(
            preflight.FreeFirstPreflightError,
            "production_ref_moved must be boolean",
        ):
            preflight.evaluate_free_first_preflight(receipt)


if __name__ == "__main__":
    unittest.main()
