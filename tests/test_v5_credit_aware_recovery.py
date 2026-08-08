from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from execution_graph import ExecutionGraph, SelectedNode  # noqa: E402
from v5_cost_effectiveness_runtime import (  # noqa: E402
    CostEffectiveContinuousExecutionEngine,
)
from v5_credit_aware_recovery import (  # noqa: E402
    CreditAwareTaskScopedExecutionEngine,
    dynamic_zero_cost_recovery_depth,
    privacy_policy_endpoint_unavailable,
    zero_cost_candidate_row,
)
from v5_recovery_runtime import build_production_runtime  # noqa: E402
from v5_runtime import (  # noqa: E402
    FailureCategory,
    RuntimeAttempt,
    RuntimeConfig,
)
from v5_soft_proposal_materializer import _standby_row  # noqa: E402
import v5_task_scope_quality_circuit as task_scope  # noqa: E402


def node(model: str = "vendor/paid", pressure: float = 0.53) -> SelectedNode:
    return SelectedNode(
        node_id="n1",
        assigned_work=("w1",),
        professional_capabilities={"analysis": 1.0},
        functions=("analysis",),
        prompt_profile={"modules": ["analysis"], "role": "动态角色"},
        reasoning_profile={"reasoning_enabled": True, "effort": "medium"},
        parameter_profile={
            "runtime_resource_parameter_values": {
                "resource-efficiency-balance": {
                    "overall_pressure": pressure,
                }
            }
        },
        model=model,
        provider_endpoint=f"{model}@openrouter-auto",
        output_contract={
            "required_fields": ["核心判断"],
            "final_delivery_node": True,
        },
        estimated_quality=0.8,
        quality_uncertainty=0.1,
        estimated_cost=0.01,
        failure_probability=0.1,
        request_config={},
    )


def graph(selected: SelectedNode) -> ExecutionGraph:
    return ExecutionGraph(
        nodes=(selected,),
        edges=(),
        execution_stages=((selected.node_id,),),
        entry_nodes=(selected.node_id,),
        final_nodes=(selected.node_id,),
        required_work=("w1",),
        estimated_quality=0.8,
        quality_floor=0.0,
        estimated_total_cost=selected.estimated_cost,
        metadata={
            "recovery_pool": {selected.node_id: []},
            "standby_inventory": [],
        },
    )


def runtime_engine() -> CreditAwareTaskScopedExecutionEngine:
    runtime = build_production_runtime(
        RuntimeConfig(
            total_call_limit=10,
            recovery_call_limit=5,
            cost_anomaly_usd=None,
            tools_allowed=False,
            live_catalog_required=True,
            provider_lock_required=False,
        )
    )
    engine = runtime.execution_engine
    assert isinstance(engine, CreditAwareTaskScopedExecutionEngine)
    return engine


def credit_attempt(selected: SelectedNode) -> RuntimeAttempt:
    return RuntimeAttempt(
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
        failure={
            "category": FailureCategory.BUDGET_INSUFFICIENT.value,
            "retryable": False,
            "http_status": 402,
            "message": "Insufficient credits",
            "response_diagnostics": {
                "provider_account_credit_insufficient": True,
            },
        },
    )


def privacy_404_attempt(index: int, model: str) -> RuntimeAttempt:
    return RuntimeAttempt(
        attempt_index=index,
        attempt_kind="replacement",
        candidate_id="n1",
        model=model,
        provider_endpoint=f"{model}@openrouter-auto",
        request={"model": model},
        status="call_failed",
        answer=None,
        quality_score=0.0,
        gate_reasons=[FailureCategory.PROVIDER_INVALID_RESPONSE.value],
        latency_seconds=0.02,
        usage={},
        response_id=None,
        response_model=None,
        response_provider=None,
        failure={
            "category": FailureCategory.PROVIDER_INVALID_RESPONSE.value,
            "retryable": False,
            "http_status": 404,
            "message": (
                "No endpoints available matching your guardrail restrictions "
                "and data policy. Configure privacy settings."
            ),
        },
    )


def timeout_attempt(index: int, model: str) -> RuntimeAttempt:
    return RuntimeAttempt(
        attempt_index=index,
        attempt_kind="replacement",
        candidate_id="n1",
        model=model,
        provider_endpoint=f"{model}@openrouter-auto",
        request={"model": model},
        status="call_failed",
        answer=None,
        quality_score=0.0,
        gate_reasons=[FailureCategory.PROVIDER_TIMEOUT.value],
        latency_seconds=1.0,
        usage={},
        response_id=None,
        response_model=None,
        response_provider=None,
        failure={
            "category": FailureCategory.PROVIDER_TIMEOUT.value,
            "retryable": False,
            "http_status": 504,
            "message": "timeout",
        },
    )


def free_rows(count: int) -> list[dict]:
    return [
        {
            "model": f"vendor/free-{index}:free",
            "provider_endpoint": f"vendor/free-{index}:free@openrouter-auto",
            "estimated_cost": 0.0,
            "zero_cost_candidate": True,
            "cost_rank_signal": 0.0,
        }
        for index in range(1, count + 1)
    ]


class CreditAwareRecoveryTests(unittest.TestCase):
    def test_paid_placeholder_zero_is_not_mistaken_for_free(self) -> None:
        self.assertFalse(
            zero_cost_candidate_row(
                {"model": "vendor/paid", "estimated_cost": 0.0}
            )
        )
        self.assertTrue(
            zero_cost_candidate_row(
                {"model": "nvidia/model:free", "estimated_cost": 9.0}
            )
        )
        self.assertTrue(
            zero_cost_candidate_row(
                {"model": "vendor/explicit", "zero_cost_candidate": True}
            )
        )

    def test_standby_materialization_preserves_catalog_price_signal(self) -> None:
        paid = _standby_row(
            {
                "model": "vendor/paid",
                "prompt_usd_per_million": 0.4,
                "completion_usd_per_million": 1.6,
                "price_rank_usd_per_million": 1.0,
            }
        )
        free = _standby_row(
            {
                "model": "vendor/free:free",
                "prompt_usd_per_million": 0.0,
                "completion_usd_per_million": 0.0,
                "price_rank_usd_per_million": 0.0,
            }
        )
        self.assertFalse(paid["estimated_task_cost_available"])
        self.assertEqual(1.0, paid["cost_rank_signal"])
        self.assertFalse(paid["zero_cost_candidate"])
        self.assertTrue(free["zero_cost_candidate"])
        self.assertEqual(0.0, free["cost_rank_signal"])
        self.assertFalse(free["cost_rank_signal_is_execution_gate"])

    def test_soft_recovery_rank_consumes_preserved_catalog_signal(self) -> None:
        expensive = {
            "model": "vendor/expensive",
            "estimated_quality": 0.8,
            "failure_probability": 0.1,
            "estimated_task_cost_usd": 0.0,
            "estimated_task_cost_available": False,
            "estimated_cost": 0.0,
            "cost_rank_signal": 5.0,
        }
        cheap = {
            "model": "vendor/cheap",
            "estimated_quality": 0.8,
            "failure_probability": 0.1,
            "estimated_task_cost_usd": 0.0,
            "estimated_task_cost_available": False,
            "estimated_cost": 0.0,
            "cost_rank_signal": 0.5,
        }
        ranked = sorted(
            [expensive, cheap],
            key=lambda row: CostEffectiveContinuousExecutionEngine._failure_rank_key(
                row,
                FailureCategory.PROVIDER_TIMEOUT,
                set(),
            ),
        )
        self.assertEqual(
            ["vendor/cheap", "vendor/expensive"],
            [row["model"] for row in ranked],
        )

    def test_zero_cost_depth_is_current_space_pressure_and_feedback_derived(self) -> None:
        selected = node(pressure=0.53)
        self.assertEqual(2, dynamic_zero_cost_recovery_depth(selected, 14))
        self.assertEqual(
            4,
            dynamic_zero_cost_recovery_depth(
                selected,
                12,
                transport_feedback_pressure=1.0,
            ),
        )
        self.assertEqual(0, dynamic_zero_cost_recovery_depth(selected, 0))

    def test_privacy_404_is_detected_from_real_openrouter_message_shape(self) -> None:
        attempt = privacy_404_attempt(2, "vendor/free:free")
        self.assertTrue(privacy_policy_endpoint_unavailable(attempt))
        self.assertFalse(
            privacy_policy_endpoint_unavailable(
                timeout_attempt(2, "vendor/free:free")
            )
        )

    def test_402_can_recover_through_current_free_standby(self) -> None:
        engine = runtime_engine()
        selected = node()
        engine._initialize_feedback(graph(selected))
        engine._provider_account_blocked = True
        engine._provider_account_block_reason = (
            "openrouter-http-402-insufficient-credits"
        )
        engine._standby_inventory = [
            {
                "model": "vendor/paid-placeholder",
                "provider_endpoint": "vendor/paid-placeholder@openrouter-auto",
                "estimated_cost": 0.0,
            },
            {
                "model": "nvidia/recovery:free",
                "provider_endpoint": "nvidia/recovery:free@openrouter-auto",
                "estimated_cost": 0.0,
                "zero_cost_candidate": True,
            },
        ]
        attempts = [credit_attempt(selected)]
        called_models: list[str] = []
        engine._probe_user_policy_models = lambda: None  # type: ignore[method-assign]

        def fake_call(candidate: SelectedNode, kind: str) -> RuntimeAttempt:
            called_models.append(candidate.model)
            attempt = RuntimeAttempt(
                attempt_index=len(attempts) + 1,
                attempt_kind=kind,
                candidate_id=selected.node_id,
                model=candidate.model,
                provider_endpoint=candidate.provider_endpoint,
                request={"model": candidate.model},
                status="passed",
                answer="## 核心判断\n可执行答案",
                quality_score=1.0,
                gate_reasons=[],
                latency_seconds=0.01,
                usage={},
                response_id="r-free",
                response_model=candidate.model,
                response_provider="openrouter-auto",
                failure=None,
            )
            attempts.append(attempt)
            return attempt

        recovered, _best, resolved = engine._recover_node(
            selected,
            attempts,
            [],
            FailureCategory.BUDGET_INSUFFICIENT,
            None,
            fake_call,
        )
        self.assertIsNotNone(recovered)
        self.assertEqual("success_recovered", recovered.status)
        self.assertEqual("nvidia/recovery:free", resolved.model)
        self.assertEqual(["nvidia/recovery:free"], called_models)
        self.assertNotIn("vendor/paid-placeholder", called_models)

    def test_two_privacy_404s_replan_beyond_initial_depth_until_third_succeeds(self) -> None:
        engine = runtime_engine()
        selected = node(pressure=0.53)
        engine._initialize_feedback(graph(selected))
        engine._provider_account_blocked = True
        engine._provider_account_block_reason = "openrouter-http-402-insufficient-credits"
        engine._standby_inventory = free_rows(4)
        engine._probe_user_policy_models = lambda: None  # type: ignore[method-assign]
        attempts = [credit_attempt(selected)]
        called: list[str] = []

        def fake_call(candidate: SelectedNode, kind: str) -> RuntimeAttempt:
            del kind
            called.append(candidate.model)
            if len(called) <= 2:
                attempt = privacy_404_attempt(len(attempts) + 1, candidate.model)
            else:
                attempt = RuntimeAttempt(
                    attempt_index=len(attempts) + 1,
                    attempt_kind="replacement",
                    candidate_id=selected.node_id,
                    model=candidate.model,
                    provider_endpoint=candidate.provider_endpoint,
                    request={"model": candidate.model},
                    status="passed",
                    answer="## 核心判断\n第三个免费候选成功",
                    quality_score=1.0,
                    gate_reasons=[],
                    latency_seconds=0.01,
                    usage={},
                    response_id="r3",
                    response_model=candidate.model,
                    response_provider="openrouter-auto",
                    failure=None,
                )
            attempts.append(attempt)
            return attempt

        recovered, _best, resolved = engine._recover_node(
            selected,
            attempts,
            [],
            FailureCategory.BUDGET_INSUFFICIENT,
            None,
            fake_call,
        )
        self.assertIsNotNone(recovered)
        self.assertEqual("success_recovered", recovered.status)
        self.assertEqual(3, len(called))
        self.assertEqual(called[-1], resolved.model)
        snapshot = engine._feedback_snapshot()["provider_credit_zero_cost_recovery"]
        self.assertEqual(2, snapshot["privacy_policy_404_count"])
        self.assertGreaterEqual(snapshot["continuous_replan_count"], 3)
        self.assertFalse(snapshot["depth_fixed"])

    def test_live_user_policy_view_skips_known_incompatible_free_models(self) -> None:
        engine = runtime_engine()
        selected = node()
        engine._initialize_feedback(graph(selected))
        engine._provider_account_blocked = True
        engine._provider_account_block_reason = "openrouter-http-402-insufficient-credits"
        rows = free_rows(3)
        engine._standby_inventory = rows
        compatible = rows[1]["model"]
        engine._probe_user_policy_models = lambda: {compatible}  # type: ignore[method-assign]
        attempts = [credit_attempt(selected)]
        called: list[str] = []

        def fake_call(candidate: SelectedNode, kind: str) -> RuntimeAttempt:
            del kind
            called.append(candidate.model)
            attempt = RuntimeAttempt(
                attempt_index=len(attempts) + 1,
                attempt_kind="replacement",
                candidate_id=selected.node_id,
                model=candidate.model,
                provider_endpoint=candidate.provider_endpoint,
                request={"model": candidate.model},
                status="passed",
                answer="## 核心判断\n兼容候选成功",
                quality_score=1.0,
                gate_reasons=[],
                latency_seconds=0.01,
                usage={},
                response_id="r-user",
                response_model=candidate.model,
                response_provider="openrouter-auto",
                failure=None,
            )
            attempts.append(attempt)
            return attempt

        recovered, _best, _resolved = engine._recover_node(
            selected,
            attempts,
            [],
            FailureCategory.BUDGET_INSUFFICIENT,
            None,
            fake_call,
        )
        self.assertIsNotNone(recovered)
        self.assertEqual([compatible], called)
        snapshot = engine._feedback_snapshot()["provider_credit_zero_cost_recovery"]
        self.assertGreaterEqual(snapshot["known_user_policy_incompatible_skipped"], 2)

    def test_nonprivacy_failures_do_not_expand_beyond_initial_dynamic_depth(self) -> None:
        engine = runtime_engine()
        selected = node(pressure=0.53)
        engine._initialize_feedback(graph(selected))
        engine._provider_account_blocked = True
        engine._provider_account_block_reason = "openrouter-http-402-insufficient-credits"
        engine._standby_inventory = free_rows(4)
        engine._probe_user_policy_models = lambda: None  # type: ignore[method-assign]
        attempts = [credit_attempt(selected)]
        called: list[str] = []

        def fake_call(candidate: SelectedNode, kind: str) -> RuntimeAttempt:
            del kind
            called.append(candidate.model)
            attempt = timeout_attempt(len(attempts) + 1, candidate.model)
            attempts.append(attempt)
            return attempt

        recovered, _best, _resolved = engine._recover_node(
            selected,
            attempts,
            [],
            FailureCategory.BUDGET_INSUFFICIENT,
            None,
            fake_call,
        )
        self.assertIsNone(recovered)
        self.assertEqual(2, len(called))

    def test_user_policy_probe_is_metadata_only_and_does_not_change_privacy(self) -> None:
        engine = runtime_engine()
        engine._initialize_feedback(graph(node()))
        payload = {"data": [{"id": "vendor/free:free"}]}
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}, clear=False):
            with patch(
                "v5_credit_aware_recovery.openrouter_api.request_json",
                return_value=payload,
            ) as request:
                models = engine._probe_user_policy_models()
        self.assertEqual({"vendor/free:free"}, models)
        request.assert_called_once()
        snapshot = engine._feedback_snapshot()["provider_credit_zero_cost_recovery"]
        self.assertTrue(snapshot["user_policy_probe"]["available"])
        self.assertFalse(snapshot["user_policy_probe"]["model_call"])
        self.assertFalse(snapshot["privacy_policy_relaxed_or_overridden"])

    def test_402_terminal_reason_is_not_overwritten_by_empty_evidence(self) -> None:
        engine = runtime_engine()
        reason = engine._constitutional_failure_reason(
            {
                "status": "failed",
                "final_answer": None,
                "provider_account_transport_state": {"blocked": True},
            },
            {"status": "PASS"},
            {"status": "FAIL"},
            object(),
        )
        self.assertEqual("provider-account-credit-insufficient", reason)

    def test_routing_meta_clause_is_not_business_delivery(self) -> None:
        runtime_engine()
        task = (
            "比较A和B。\n\n执行要求：\n"
            "- 不得使用固定职业关键词路由\n"
            "- 最终报告给出明确决策表"
        )
        projected, audit = task_scope.project_business_task(task)
        self.assertNotIn("关键词路由", projected)
        self.assertIn("最终报告给出明确决策表", projected)
        self.assertGreaterEqual(audit["control_clause_count"], 1)


if __name__ == "__main__":
    unittest.main()
