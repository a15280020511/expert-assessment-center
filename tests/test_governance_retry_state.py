from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "open-model-market" / "v5_governance_retry_state.py"


def _load():
    spec = importlib.util.spec_from_file_location("governance_retry_state_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


ledger = _load()


def accepted(retry_id: str) -> str:
    return f"## EXECUTION_RETRY_ACCEPTED\n\n- RETRY_ID: `{retry_id}`\n"


def failed(model_calls: int | None) -> str:
    line = "" if model_calls is None else f"- 模型调用次数：`{model_calls}`\n"
    return f"## EXECUTION_FAILED\n\n{line}"


def degraded(model_calls: int | None) -> str:
    line = "" if model_calls is None else f"- Model calls: `{model_calls}`\n"
    return f"## EXECUTION_DEGRADED\n\n{line}"


class GovernanceRetryStateTests(unittest.TestCase):
    def test_explicit_zero_call_failures_use_system_repair_ledger(self) -> None:
        comments: list[str] = []
        for index in range(4):
            comments.extend([accepted(f"repair-{index}"), failed(0)])
        state = ledger.execution_state(comments)
        self.assertEqual(state["business_retry_count"], 0)
        self.assertEqual(state["system_repair_retry_count"], 4)
        self.assertEqual(state["in_flight_retry_count"], 0)
        self.assertEqual(state["total_accepted_retry_count"], 4)
        self.assertEqual(
            ledger.current_issue_submission_reason(
                state,
                is_retry=True,
                issue_state="open",
                retry_id="repair-5",
            ),
            "",
        )

    def test_positive_or_unknown_calls_charge_business_retry(self) -> None:
        state = ledger.execution_state(
            [
                accepted("paid-1"),
                failed(1),
                accepted("unknown-2"),
                failed(None),
            ]
        )
        self.assertEqual(state["business_retry_count"], 2)
        self.assertEqual(state["system_repair_retry_count"], 0)
        self.assertIn(
            "maximum 2 business retries",
            ledger.current_issue_submission_reason(
                state,
                is_retry=True,
                issue_state="open",
                retry_id="paid-3",
            ),
        )

    def test_degraded_terminal_is_retryable_and_charged_by_calls(self) -> None:
        zero_call = ledger.execution_state(
            [accepted("degraded-zero"), degraded(0)]
        )
        self.assertTrue(zero_call["degraded"])
        self.assertTrue(zero_call["failed"])
        self.assertEqual(zero_call["system_repair_retry_count"], 1)
        self.assertEqual(zero_call["business_retry_count"], 0)
        self.assertEqual(
            ledger.current_issue_submission_reason(
                zero_call,
                is_retry=True,
                issue_state="open",
                retry_id="after-degraded-zero",
            ),
            "",
        )

        positive_call = ledger.execution_state(
            [accepted("degraded-paid"), degraded(2)]
        )
        self.assertEqual(positive_call["system_repair_retry_count"], 0)
        self.assertEqual(positive_call["business_retry_count"], 1)

    def test_in_flight_retry_blocks_parallel_submission(self) -> None:
        state = ledger.execution_state([accepted("in-flight")])
        self.assertEqual(state["in_flight_retry_count"], 1)
        self.assertIn(
            "already in progress",
            ledger.current_issue_submission_reason(
                {**state, "failed": True},
                is_retry=True,
                issue_state="open",
                retry_id="parallel",
            ),
        )

    def test_system_repair_reserve_is_finite(self) -> None:
        comments: list[str] = []
        for index in range(ledger.SYSTEM_REPAIR_RETRY_LIMIT):
            comments.extend([accepted(f"system-{index}"), failed(0)])
        state = ledger.execution_state(comments)
        self.assertIn(
            "zero-call system repair retries",
            ledger.current_issue_submission_reason(
                state,
                is_retry=True,
                issue_state="open",
                retry_id="system-final",
            ),
        )

    def test_duplicate_retry_id_and_completed_state_remain_blocked(self) -> None:
        state = ledger.execution_state([accepted("used"), failed(0)])
        self.assertIn(
            "already used",
            ledger.current_issue_submission_reason(
                state,
                is_retry=True,
                issue_state="open",
                retry_id="used",
            ),
        )
        completed_state = ledger.execution_state(
            [accepted("done"), "## EXECUTION_COMPLETED\n\n- 模型调用次数：`3`\n"]
        )
        self.assertIn(
            "completed executions",
            ledger.current_issue_submission_reason(
                completed_state,
                is_retry=True,
                issue_state="open",
                retry_id="after-done",
            ),
        )


if __name__ == "__main__":
    unittest.main()
