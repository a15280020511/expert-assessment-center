from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_paid_acceptance_free_first_guard as guard  # noqa: E402
import v5_price_ranked_production_ticket as production_ticket  # noqa: E402


SHA = "a" * 40


def _canary() -> dict:
    return {
        "schema_version": "v5-zero-cost-free-model-canary-1",
        "status": "PASS",
        "target_sha": SHA,
        "zero_call_qualification_run_id": "123",
        "model_requests": 1,
        "successful_model_calls": 1,
        "paid_model_calls": 0,
        "actual_cost_usd": 0.0,
        "requested_model": "openrouter/free",
        "actual_model": "company/free-model:free",
        "provider_object_present": False,
        "external_tools_allowed": False,
        "synthetic_prompt_only": True,
        "formal_model_identity_qualified": False,
        "production_ref_moved": False,
    }


def _zero_call() -> dict:
    return {
        "schema_version": "v5-top50-task-adaptive-ortools-zero-call-qualification-2",
        "target_sha": SHA,
        "status": "PASS",
        "model_calls": 0,
        "candidate_pool_authority": "decision-system-governance",
        "model_assignment_authority": "expert-assessment-center-ortools",
        "candidate_pool_size": 50,
        "popularity_period": "week",
        "optimizer": "ortools-cp-sat",
        "optimizer_required_status": "OPTIMAL",
        "selection_principles": [
            "concrete-problem-concrete-analysis",
            "dynamic-adaptation",
            "small-effort-large-return",
        ],
        "task_adaptive_value_scoring_required": True,
        "semantic_keyword_routing_used": False,
        "cross_task_history_used": False,
        "primary_expert_count": 4,
        "warm_recovery_count": 4,
        "provider_routing_mode": "unrestricted-openrouter",
        "provider_restrictions_applied": False,
        "model_substitution_allowed": False,
        "production_ref_moved": False,
    }


class PaidAcceptanceFreeFirstGuardTests(unittest.TestCase):
    def test_matching_zero_call_and_zero_cost_canary_authorize_paid_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(
                guard,
                "_find_free_canary",
                return_value=(456, 789, _canary()),
            ), patch.object(
                guard,
                "_api_json",
                return_value={"conclusion": "success", "head_sha": SHA},
            ), patch.object(
                guard,
                "_artifact_for_run",
                return_value=(321, {"id": 321}),
            ), patch.object(
                guard,
                "_api_bytes",
                return_value=b"unused",
            ), patch.object(
                guard,
                "_read_json_from_zip",
                return_value=_zero_call(),
            ):
                verdict = guard.enforce_free_first(
                    output_dir=root,
                    expected_sha=SHA,
                    repository="owner/repo",
                    token="token",
                )
            self.assertEqual(verdict["status"], "PASS")
            self.assertTrue(verdict["paid_acceptance_allowed"])
            receipt = json.loads((root / "free-first-preflight-receipt.json").read_text())
            self.assertEqual(receipt["target_sha"], SHA)
            self.assertEqual(receipt["simulation"]["model_calls"], 0)
            self.assertEqual(receipt["free_canary"]["actual_cost_usd"], 0.0)
            self.assertEqual(
                receipt["evidence"]["selection_principles"],
                [
                    "concrete-problem-concrete-analysis",
                    "dynamic-adaptation",
                    "small-effort-large-return",
                ],
            )

    def test_legacy_zero_call_receipt_is_rejected(self) -> None:
        receipt = _zero_call()
        receipt["schema_version"] = "v5-top50-ortools-zero-call-qualification-1"
        with self.assertRaisesRegex(
            guard.PaidAcceptanceFreeFirstError,
            "schema_version",
        ):
            guard._validate_zero_call_receipt(receipt, SHA)

    def test_missing_free_canary_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            guard,
            "_find_free_canary",
            side_effect=guard.PaidAcceptanceFreeFirstError("missing"),
        ):
            with self.assertRaisesRegex(
                guard.PaidAcceptanceFreeFirstError,
                "missing",
            ):
                guard.enforce_free_first(
                    output_dir=Path(tmp),
                    expected_sha=SHA,
                    repository="owner/repo",
                    token="token",
                )

    def test_production_ticket_only_enforces_guard_for_paid_acceptance_app(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            production_ticket,
            "enforce_free_first",
            return_value={"status": "PASS", "paid_acceptance_allowed": True},
        ) as enforce:
            with patch.dict(os.environ, {}, clear=True):
                production_ticket._enforce_paid_acceptance_free_first(Path(tmp))
                enforce.assert_not_called()
            with patch.dict(
                os.environ,
                {
                    "OPENROUTER_APP_NAME": production_ticket.PAID_ACCEPTANCE_APP_NAME,
                    "AUTHORITATIVE_EXECUTION_SHA": SHA,
                },
                clear=True,
            ):
                production_ticket._enforce_paid_acceptance_free_first(Path(tmp))
                enforce.assert_called_once_with(output_dir=Path(tmp), expected_sha=SHA)

    def test_paid_acceptance_guard_failure_persists_zero_call_error_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            production_ticket,
            "enforce_free_first",
            side_effect=guard.PaidAcceptanceFreeFirstError("no evidence"),
        ), patch.dict(
            os.environ,
            {
                "OPENROUTER_APP_NAME": production_ticket.PAID_ACCEPTANCE_APP_NAME,
                "AUTHORITATIVE_EXECUTION_SHA": SHA,
            },
            clear=True,
        ):
            root = Path(tmp)
            with self.assertRaisesRegex(
                guard.PaidAcceptanceFreeFirstError,
                "no evidence",
            ):
                production_ticket._enforce_paid_acceptance_free_first(root)
            error = json.loads((root / "free-first-preflight-error.json").read_text())
            self.assertEqual(error["status"], "FAIL")
            self.assertEqual(error["model_calls"], 0)
            self.assertEqual(error["paid_model_calls"], 0)


if __name__ == "__main__":
    unittest.main()
