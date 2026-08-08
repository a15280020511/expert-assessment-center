from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from v5_production_expert_policy import EvidenceCompleteExecutionEngine  # noqa: E402
from v5_runtime import FailureCategory, RuntimeAttempt, RuntimeConfig  # noqa: E402
from v5_runtime_request_binding import (  # noqa: E402
    audit_bound_request,
    bind_request_knobs,
    dynamic_output_allowance,
)
from v5_soft_resource_governance import SoftResourceExecutionEngine, build_runtime  # noqa: E402
from v5_task_constraints import compile_task_constraints  # noqa: E402


class RuntimeKnobClosureTests(unittest.TestCase):
    @staticmethod
    def _node(*, effort: str = "medium", final: bool = False) -> SimpleNamespace:
        return SimpleNamespace(
            node_id="node-1",
            model="vendor/model",
            provider_endpoint="vendor/model@openrouter-auto",
            assigned_work=("work-1",),
            reasoning_profile={"effort": effort},
            parameter_profile={},
            output_contract={
                "required_fields": ["核心判断", "关键依据", "不确定性", "结论"],
                "final_delivery_node": final,
            },
        )

    def test_reasoning_and_dynamic_allowance_are_bound(self) -> None:
        node = self._node(effort="high")
        config, record = bind_request_knobs(node, "任务" * 500, [])
        self.assertEqual("high", config["reasoning"]["effort"])
        self.assertGreater(config["max_tokens"], 0)
        self.assertEqual("PASS", record["status"])
        self.assertFalse(record["output_allowance_is_task_admission_gate"])
        self.assertFalse(record["output_allowance_is_result_validity_gate"])
        self.assertEqual("PASS", audit_bound_request(node, config)["status"])

    def test_request_shape_recomputes_allowance(self) -> None:
        node = self._node(effort="medium")
        base = dynamic_output_allowance(node, "任务" * 100, [])
        expanded = dynamic_output_allowance(
            node,
            "任务" * 100,
            [{"answer": "上游" * 1000}, {"answer": "结果" * 500}],
        )
        self.assertGreater(expanded, base)

    def test_computed_but_unused_is_detected(self) -> None:
        node = self._node(effort="low")
        audit = audit_bound_request(node, {"max_tokens": 512})
        self.assertEqual("FAIL", audit["status"])
        self.assertIn("role-reasoning-effort", audit["computed_but_unused"])

    def test_internal_fact_label_matcher_only_failure_becomes_warning(self) -> None:
        config = RuntimeConfig(
            total_call_limit=4,
            recovery_call_limit=1,
            cost_anomaly_usd=None,
            provider_lock_required=False,
        )
        runtime = build_runtime(config)
        engine = EvidenceCompleteExecutionEngine(
            runtime.config,
            prompt_policy=runtime.prompt_policy,
            retry_policy=runtime.retry_policy,
            recovery_policy=runtime.recovery_policy,
            quality_policy=runtime.quality_policy,
            output_policy=runtime.output_policy,
        )
        node = self._node(final=False)
        attempt = RuntimeAttempt(
            attempt_index=1,
            attempt_kind="initial",
            candidate_id="c1",
            model=node.model,
            provider_endpoint=node.provider_endpoint,
            request={"model": node.model},
            status="quality_gate_failed",
            answer="可用内部分析",
            quality_score=0.8,
            gate_reasons=["unsupported-fact-label:题面事实自然语言改写"],
            latency_seconds=0.1,
            usage={},
            response_id="r1",
            response_model=node.model,
            response_provider="provider",
            failure={
                "category": FailureCategory.QUALITY_GATE_FAILED.value,
                "retryable": False,
            },
        )
        constraints = compile_task_constraints("只依据题面")
        with (
            patch(
                "v5_production_expert_policy.relabel_task_derived_fact_lines",
                return_value=(attempt.answer, {"applied": False}),
            ),
            patch.object(
                SoftResourceExecutionEngine,
                "_normalize_attempt",
                return_value=False,
            ),
        ):
            passed = engine._normalize_attempt(node, "只依据题面", attempt, constraints)
        self.assertTrue(passed)
        self.assertEqual("passed", attempt.status)
        self.assertEqual([], attempt.gate_reasons)
        self.assertIsNone(attempt.failure)
        self.assertTrue(attempt.answer_transformations[-1]["applied"])

    def test_final_fact_label_failure_remains_fail_closed(self) -> None:
        config = RuntimeConfig(
            total_call_limit=4,
            recovery_call_limit=1,
            cost_anomaly_usd=None,
            provider_lock_required=False,
        )
        runtime = build_runtime(config)
        engine = EvidenceCompleteExecutionEngine(
            runtime.config,
            prompt_policy=runtime.prompt_policy,
            retry_policy=runtime.retry_policy,
            recovery_policy=runtime.recovery_policy,
            quality_policy=runtime.quality_policy,
            output_policy=runtime.output_policy,
        )
        node = self._node(final=True)
        attempt = RuntimeAttempt(
            attempt_index=1,
            attempt_kind="initial",
            candidate_id="c1",
            model=node.model,
            provider_endpoint=node.provider_endpoint,
            request={"model": node.model},
            status="quality_gate_failed",
            answer="最终输出",
            quality_score=0.8,
            gate_reasons=["unsupported-fact-label:外部事实"],
            latency_seconds=0.1,
            usage={},
            response_id="r1",
            response_model=node.model,
            response_provider="provider",
            failure={
                "category": FailureCategory.QUALITY_GATE_FAILED.value,
                "retryable": False,
            },
        )
        constraints = compile_task_constraints("只依据题面")
        with (
            patch(
                "v5_production_expert_policy.relabel_task_derived_fact_lines",
                return_value=(attempt.answer, {"applied": False}),
            ),
            patch.object(
                SoftResourceExecutionEngine,
                "_normalize_attempt",
                return_value=False,
            ),
        ):
            passed = engine._normalize_attempt(node, "只依据题面", attempt, constraints)
        self.assertFalse(passed)
        self.assertEqual("quality_gate_failed", attempt.status)

    def test_nonretryable_model_failure_is_current_run_memory(self) -> None:
        config = RuntimeConfig(
            total_call_limit=4,
            recovery_call_limit=1,
            cost_anomaly_usd=None,
            provider_lock_required=False,
        )
        runtime = build_runtime(config)
        engine = EvidenceCompleteExecutionEngine(
            runtime.config,
            prompt_policy=runtime.prompt_policy,
            retry_policy=runtime.retry_policy,
            recovery_policy=runtime.recovery_policy,
            quality_policy=runtime.quality_policy,
            output_policy=runtime.output_policy,
        )
        attempt = RuntimeAttempt(
            attempt_index=1,
            attempt_kind="replacement",
            candidate_id="c1",
            model="bad/model",
            provider_endpoint="bad/model@openrouter-auto",
            request={"model": "bad/model"},
            status="call_failed",
            answer=None,
            quality_score=0.0,
            gate_reasons=[],
            latency_seconds=0.1,
            usage={},
            response_id=None,
            response_model=None,
            response_provider=None,
            failure={
                "category": FailureCategory.PROVIDER_INVALID_RESPONSE.value,
                "retryable": False,
                "model": "bad/model",
            },
        )
        engine._record_feedback(attempt)
        snapshot = engine._feedback_snapshot()
        self.assertEqual(["bad/model"], snapshot["hard_failed_model_ids"])
        self.assertEqual("current-run-only", snapshot["nonretryable_model_failure_memory_scope"])


if __name__ == "__main__":
    unittest.main()
