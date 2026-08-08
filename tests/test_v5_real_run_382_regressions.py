from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from execution_graph import SelectedNode  # noqa: E402
from v5_quality_status_integrity import enforce_result_integrity  # noqa: E402
from v5_runtime import (  # noqa: E402
    ExecutionEngine,
    FailureCategory,
    RuntimeAttempt,
)
from v5_task_constraints import (  # noqa: E402
    closed_world_numeric_prompt,
    compile_task_constraints,
    validate_answer_evidence,
)


def node(model: str = "openai/test-model") -> SelectedNode:
    return SelectedNode(
        node_id="n1",
        assigned_work=("w1",),
        professional_capabilities={},
        functions=("analysis",),
        prompt_profile={},
        reasoning_profile={"reasoning_enabled": True, "effort": "medium"},
        parameter_profile={},
        model=model,
        provider_endpoint=f"{model}@openrouter-auto",
        output_contract={"required_fields": ["结论"]},
        estimated_quality=0.8,
        quality_uncertainty=0.1,
        estimated_cost=0.01,
        failure_probability=0.1,
        request_config={},
    )


class FakeCreditError(RuntimeError):
    http_status = 402
    retryable = False
    retry_after_seconds = None
    request_sent = True
    response_received = True
    response_diagnostics = {}

    def __init__(self) -> None:
        super().__init__("HTTP 402 from OpenRouter: Insufficient credits")


class OpenRouterAccountCircuitTests(unittest.TestCase):
    def test_402_is_account_level_nonrecoverable_failure(self) -> None:
        selected = node()
        failure = ExecutionEngine._failure_from_exception(
            FakeCreditError(), selected
        )
        self.assertEqual(failure.category, FailureCategory.BUDGET_INSUFFICIENT)
        self.assertFalse(failure.retryable)
        self.assertEqual(failure.http_status, 402)
        self.assertTrue(
            failure.response_diagnostics["provider_account_credit_insufficient"]
        )
        self.assertFalse(
            failure.response_diagnostics["model_replacement_can_repair"]
        )

    def test_account_circuit_suppresses_standby_and_future_calls(self) -> None:
        selected = node()
        engine = object.__new__(ExecutionEngine)
        engine._ensure_feedback_state()
        engine._standby_inventory = [
            {
                "model": "vendor/standby",
                "provider_endpoint": "vendor/standby@openrouter-auto",
            }
        ]
        failure = ExecutionEngine._failure_from_exception(
            FakeCreditError(), selected
        )
        attempt = RuntimeAttempt(
            attempt_index=1,
            attempt_kind="initial",
            candidate_id=selected.node_id,
            model=selected.model,
            provider_endpoint=selected.provider_endpoint,
            request={"model": selected.model},
            status="call_failed",
            answer=None,
            quality_score=0.0,
            gate_reasons=[FailureCategory.BUDGET_INSUFFICIENT.value],
            latency_seconds=0.01,
            usage={},
            response_id=None,
            response_model=None,
            response_provider=None,
            failure=failure.to_dict(),
        )
        engine._record_feedback(attempt)
        engine._mark_provider_account_blocked(attempt)

        snapshot = engine._feedback_snapshot()
        self.assertTrue(snapshot["provider_account_blocked"])
        self.assertEqual(
            snapshot["provider_account_block_reason"],
            "openrouter-http-402-insufficient-credits",
        )
        self.assertEqual(engine._dynamic_promotion_depth([attempt]), 0)
        self.assertIsNone(engine._claim_next_standby())

        calls = 0

        def should_not_call(*_args: object, **_kwargs: object) -> tuple[dict, float]:
            nonlocal calls
            calls += 1
            return {}, 0.0

        suppressed = engine._recorded_call(
            selected,
            [],
            "task",
            [],
            object(),
            should_not_call,
            object(),
            selected,
            "replacement",
        )
        self.assertIsNone(suppressed)
        self.assertEqual(calls, 0)


class DegradedStatusTruthTests(unittest.TestCase):
    def test_zero_coverage_shell_is_not_degraded_success(self) -> None:
        result = {
            "status": "success",
            "completion_mode": "degraded",
            "quality_status": "degraded_success",
            "final_answer": "# V5降级合成结果\n\n## 未覆盖工作\nw1",
            "node_results": [
                {
                    "node_id": "n1",
                    "status": "failed",
                    "contract": {"required_fields_complete": False},
                    "attempts": [],
                }
            ],
            "work_coverage": {
                "coverage_ratio": 0.0,
                "minimum_degraded_coverage": 0.0,
                "successful_content_nodes": 0,
            },
            "delivery_policy": {
                "allow_degraded_success": True,
                "blockers": [],
                "missing_non_degradable_work_ids": [],
            },
            "degradation": {"used": True},
        }
        normalized = enforce_result_integrity(result)
        self.assertEqual(normalized["status"], "failed")
        self.assertEqual(normalized["completion_mode"], "none")
        self.assertEqual(normalized["quality_status"], "failed")
        self.assertIsNone(normalized["final_answer"])
        self.assertEqual(normalized["quality_integrity"]["status"], "FAIL")
        self.assertTrue(
            normalized["quality_integrity"][
                "invalid_degraded_success_rejected"
            ]
        )

    def test_positive_strict_content_can_still_be_degraded_success(self) -> None:
        result = {
            "status": "success",
            "completion_mode": "degraded",
            "quality_status": "degraded_success",
            "final_answer": "可用的部分分析结果",
            "node_results": [
                {
                    "node_id": "n1",
                    "status": "success",
                    "contract": {"required_fields_complete": True},
                    "attempts": [],
                },
                {
                    "node_id": "n2",
                    "status": "failed",
                    "contract": {"required_fields_complete": False},
                    "attempts": [],
                },
            ],
            "work_coverage": {
                "coverage_ratio": 0.5,
                "minimum_degraded_coverage": 0.5,
                "successful_content_nodes": 1,
            },
            "delivery_policy": {
                "allow_degraded_success": True,
                "blockers": [],
                "missing_non_degradable_work_ids": [],
            },
            "degradation": {"used": True},
        }
        normalized = enforce_result_integrity(result)
        self.assertEqual(normalized["status"], "success")
        self.assertEqual(normalized["completion_mode"], "degraded")
        self.assertEqual(normalized["quality_status"], "degraded_success")
        self.assertEqual(normalized["quality_integrity"]["status"], "DEGRADED")


class ClosedWorldDerivedQuantityTests(unittest.TestCase):
    TASK = (
        "仅依据题面，不得调用外部工具，不得补充题目之外的数据。"
        "单次业务损失为1800元，计划使用3年。"
        "请计算三年期预期总成本，并给出敏感性分析和切换临界值。"
    )

    def test_closed_world_calculation_prompt_allows_derived_numbers(self) -> None:
        policy = compile_task_constraints(self.TASK)
        self.assertFalse(policy.external_facts_allowed)
        self.assertFalse(policy.unsupported_precise_quantities_allowed)
        prompt = closed_world_numeric_prompt(self.TASK, policy)
        self.assertIn("显式算式", prompt)
        self.assertIn("派生精确数量", prompt)
        self.assertIn("不得引入任何题外参数", prompt)

    def test_provenance_backed_derived_quantities_are_allowed(self) -> None:
        answer = (
            "计算结果：三年期预期总成本 = 10260元。\n"
            "临界值推导：切换阈值 = 1963.64元。"
        )
        self.assertEqual(validate_answer_evidence(self.TASK, answer), [])

    def test_unproven_external_quantity_is_still_rejected(self) -> None:
        answer = "行业平均业务损失为2500元。"
        violations = validate_answer_evidence(self.TASK, answer)
        self.assertTrue(
            any(
                value.startswith("closed-world-unproven-derived-quantity:")
                for value in violations
            )
        )

    def test_derived_quantity_cannot_be_promoted_to_task_fact(self) -> None:
        answer = "事实：计算结果为10260元。"
        violations = validate_answer_evidence(self.TASK, answer)
        self.assertTrue(
            any(
                value.startswith("closed-world-unproven-derived-quantity:")
                for value in violations
            )
        )


if __name__ == "__main__":
    unittest.main()
