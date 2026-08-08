from __future__ import annotations

import sys
import unittest
from pathlib import Path

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
            "response_diagnostics": {
                "provider_account_credit_insufficient": True,
            },
        },
    )


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
        self.assertEqual(["vendor/cheap", "vendor/expensive"], [row["model"] for row in ranked])

    def test_zero_cost_depth_is_current_space_and_pressure_derived(self) -> None:
        selected = node(pressure=0.53)
        self.assertEqual(2, dynamic_zero_cost_recovery_depth(selected, 14))
        self.assertEqual(0, dynamic_zero_cost_recovery_depth(selected, 0))
        self.assertLessEqual(
            dynamic_zero_cost_recovery_depth(selected, 100),
            100,
        )

    def test_402_can_recover_through_current_free_standby(self) -> None:
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
        self.assertIsInstance(engine, CreditAwareTaskScopedExecutionEngine)
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
        snapshot = engine._feedback_snapshot()
        credit = snapshot["provider_credit_zero_cost_recovery"]
        self.assertEqual(1, credit["candidate_count"])
        self.assertEqual(1, credit["attempt_count"])
        self.assertFalse(credit["normal_free_first_gate_enabled"])

    def test_402_terminal_reason_is_not_overwritten_by_empty_evidence(self) -> None:
        runtime = build_production_runtime(
            RuntimeConfig(
                total_call_limit=3,
                recovery_call_limit=1,
                cost_anomaly_usd=None,
                tools_allowed=False,
                live_catalog_required=True,
                provider_lock_required=False,
            )
        )
        engine = runtime.execution_engine
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
        # build_production_runtime installs the credit-aware scope extension.
        build_production_runtime(
            RuntimeConfig(
                total_call_limit=3,
                recovery_call_limit=1,
                cost_anomaly_usd=None,
                tools_allowed=False,
                live_catalog_required=True,
                provider_lock_required=False,
            )
        )
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
