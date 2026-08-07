from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_runtime as runtime  # noqa: E402
import v5_top50_pool_optimizer as optimizer  # noqa: E402
from execution_graph import ExecutionGraph, SelectedNode  # noqa: E402


def _node(node_id: str, model: str) -> SelectedNode:
    return SelectedNode(
        node_id=node_id,
        assigned_work=(f"work-{node_id}",),
        professional_capabilities={},
        functions=("analysis",),
        prompt_profile={},
        reasoning_profile={"effort": "medium"},
        parameter_profile={},
        model=model,
        provider_endpoint=f"{model}@openrouter-auto",
        output_contract={},
        estimated_quality=0.0,
        quality_uncertainty=0.0,
        estimated_cost=0.001,
        failure_probability=0.0,
        request_config={},
    )


def _graph(*, standby_count: int = 0) -> ExecutionGraph:
    nodes = (_node("n1", "vendor/main-1"), _node("n2", "vendor/main-2"))
    recovery_rows = [
        {
            "model": "backup-a/model",
            "provider_endpoint": "backup-a/model@openrouter-auto",
        },
        {
            "model": "backup-b/model",
            "provider_endpoint": "backup-b/model@openrouter-auto",
        },
        {
            "model": "backup-c/model",
            "provider_endpoint": "backup-c/model@openrouter-auto",
        },
    ]
    standby = [
        {
            "model": f"standby-{index}/model",
            "provider_endpoint": f"standby-{index}/model@openrouter-auto",
            "estimated_cost": 0.001,
        }
        for index in range(1, standby_count + 1)
    ]
    return ExecutionGraph(
        nodes=nodes,
        edges=(),
        execution_stages=(("n1", "n2"),),
        entry_nodes=("n1", "n2"),
        final_nodes=("n2",),
        required_work=("work-n1", "work-n2"),
        estimated_quality=0.0,
        quality_floor=0.0,
        estimated_total_cost=0.002,
        metadata={
            "recovery_pool": {
                "n1": [dict(row) for row in recovery_rows],
                "n2": [dict(row) for row in recovery_rows],
            },
            "standby_inventory": standby,
            "runtime_feedback_replanning": {
                "enabled": bool(standby),
                "promotion_depth_fixed": False,
            },
        },
    )


def _candidate(index: int, *, free: bool) -> dict[str, object]:
    company = "free-shared" if free else f"paid-{index}"
    suffix = ":free" if free else ""
    return {
        "model": f"{company}/reasoner-{index}{suffix}",
        "company": company,
        "popularity_rank": 1,
        "official_intelligence_rank": 1,
        "prompt_usd_per_million": 0.0 if free else 0.2,
        "completion_usd_per_million": 0.0 if free else 0.8,
        "request_usd": 0.0,
        "context_length": 262_144,
        "max_completion_tokens": 32_768,
    }


def _optimizer_packet() -> dict[str, object]:
    candidates = [
        *[_candidate(index, free=True) for index in range(1, 11)],
        *[_candidate(index, free=False) for index in range(11, 31)],
    ]
    return {
        "task_id": "recovery-resilience-fixture",
        "task": {
            "question": "比较A/B/C并给出三种条件化建议。",
            "requirements": [f"requirement-{index}" for index in range(5)],
            "language": "zh-CN",
        },
        "evidence": [
            {"option": "A", "value": 1},
            {"option": "B", "value": 2},
            {"option": "C", "value": 3},
        ],
        "execution_acceptance": [f"accept-{index}" for index in range(6)],
        "governance_model_plan": {
            "selection_authority": "decision-system-governance",
            "candidate_pool_authority": "decision-system-governance",
            "expert_candidate_pool": candidates,
            "provider_routing_mode": "unrestricted-openrouter",
            "provider_restrictions_applied": False,
        },
    }


def _attempt(
    category: runtime.FailureCategory,
    *,
    status: str = "call_failed",
) -> runtime.RuntimeAttempt:
    return runtime.RuntimeAttempt(
        attempt_index=1,
        attempt_kind="replacement",
        candidate_id="n1",
        model="candidate/model",
        provider_endpoint="candidate/model@openrouter-auto",
        request={},
        status=status,
        answer=None,
        quality_score=0.0,
        gate_reasons=[category.value],
        latency_seconds=0.1,
        usage={},
        response_id=None,
        response_model=None,
        response_provider=None,
        failure={"category": category.value, "retryable": False},
    )


class DynamicRecoveryResilienceTests(unittest.TestCase):
    def test_team_and_recovery_shape_are_computed_from_current_task(self) -> None:
        profile = {
            "task_characters": 272,
            "evidence_characters": 233,
            "requirement_count": 5,
            "evidence_count": 3,
            "acceptance_count": 6,
            "delivery_item_count": 0,
            "pressure": {"overall": 37},
        }
        ratio = optimizer._recovery_resilience_ratio(profile)  # noqa: SLF001
        primary, recovery = optimizer._dynamic_team_shape(  # noqa: SLF001
            profile,
            400,
        )
        self.assertGreaterEqual(ratio, 0.10)
        self.assertLessEqual(ratio, 0.90)
        self.assertGreaterEqual(primary, 1)
        self.assertEqual(
            recovery,
            min(400 - primary, math.ceil(primary * ratio)),
        )

        simpler = {
            **profile,
            "requirement_count": 0,
            "evidence_count": 0,
            "acceptance_count": 0,
            "pressure": {"overall": 0},
        }
        self.assertNotEqual(
            optimizer._dynamic_team_shape(simpler, 400),  # noqa: SLF001
            (primary, recovery),
        )

    def test_shared_pool_capacity_counts_unique_backups_not_node_copies(self) -> None:
        graph = _graph()
        self.assertEqual(runtime._recovery_capacity(graph), 3)  # noqa: SLF001
        self.assertEqual(runtime._standby_capacity(graph), 0)  # noqa: SLF001
        budget = runtime.BudgetController(
            runtime.RuntimeConfig(
                total_call_limit=99,
                recovery_call_limit=99,
                cost_anomaly_usd=None,
            ),
            graph,
        )
        snapshot = budget.snapshot()
        self.assertEqual(snapshot["maximum_initial_calls"], 2)
        self.assertEqual(snapshot["maximum_recovery_calls"], 3)
        self.assertEqual(snapshot["maximum_total_calls"], 5)
        self.assertEqual(
            snapshot["recovery_capacity_source"],
            "active-recovery-plus-runtime-promotable-standby",
        )
        self.assertEqual(snapshot["active_recovery_capacity"], 3)
        self.assertEqual(snapshot["runtime_promotable_standby_capacity"], 0)
        self.assertTrue(snapshot["runtime_resilience_parameters_dynamic"])
        self.assertTrue(snapshot["runtime_feedback_replanning_enabled"])
        self.assertFalse(snapshot["standby_promotion_depth_fixed"])

    def test_standby_is_finite_capacity_but_not_precalled(self) -> None:
        graph = _graph(standby_count=9)
        self.assertEqual(runtime._standby_capacity(graph), 9)  # noqa: SLF001
        budget = runtime.BudgetController(
            runtime.RuntimeConfig(
                total_call_limit=1,
                recovery_call_limit=0,
                cost_anomaly_usd=None,
            ),
            graph,
        )
        snapshot = budget.snapshot()
        self.assertEqual(snapshot["maximum_initial_calls"], 2)
        self.assertEqual(snapshot["maximum_recovery_calls"], 12)
        self.assertEqual(snapshot["maximum_total_calls"], 14)
        self.assertEqual(snapshot["calls_reserved"], 0)
        self.assertEqual(snapshot["runtime_promotable_standby_capacity"], 9)

    def test_promotion_depth_recomputes_from_current_run_feedback(self) -> None:
        graph = _graph(standby_count=9)
        engine = object.__new__(runtime.ExecutionEngine)
        engine._initialize_feedback(graph)  # noqa: SLF001
        one_failure = [_attempt(runtime.FailureCategory.PROVIDER_TIMEOUT)]
        low_depth = engine._dynamic_promotion_depth(one_failure)  # noqa: SLF001

        for _ in range(4):
            engine._record_feedback(  # noqa: SLF001
                _attempt(runtime.FailureCategory.QUALITY_GATE_FAILED)
            )
        high_depth = engine._dynamic_promotion_depth(  # noqa: SLF001
            [
                _attempt(runtime.FailureCategory.QUALITY_GATE_FAILED),
                _attempt(runtime.FailureCategory.PROVIDER_TIMEOUT),
            ]
        )
        self.assertGreaterEqual(low_depth, 1)
        self.assertGreater(high_depth, low_depth)
        snapshot = engine._feedback_snapshot()  # noqa: SLF001
        self.assertEqual(snapshot["observed_attempts"], 4)
        self.assertEqual(snapshot["observed_failures"], 4)
        self.assertEqual(snapshot["observed_quality_gate_failures"], 4)
        self.assertTrue(snapshot["promotion_depth_recomputed_from_current_run"])
        self.assertFalse(snapshot["promotion_depth_fixed"])
        self.assertFalse(snapshot["cross_task_history_used"])

    def test_standby_claims_are_unique_across_runtime(self) -> None:
        graph = _graph(standby_count=3)
        engine = object.__new__(runtime.ExecutionEngine)
        engine._initialize_feedback(graph)  # noqa: SLF001
        claimed = [engine._claim_next_standby() for _ in range(4)]  # noqa: SLF001
        models = [row["model"] for row in claimed if row]
        self.assertEqual(len(models), 3)
        self.assertEqual(len(models), len(set(models)))
        self.assertIsNone(claimed[-1])

    def test_provider_failure_circuit_threshold_is_graph_derived(self) -> None:
        graph = _graph()
        budget = runtime.BudgetController(
            runtime.RuntimeConfig(
                total_call_limit=5,
                recovery_call_limit=3,
                cost_anomaly_usd=None,
            ),
            graph,
        )
        engine = object.__new__(runtime.ExecutionEngine)
        selected = graph.nodes[0]
        replacement = _node(selected.node_id, "backup-a/model")
        attempt = runtime.RuntimeAttempt(
            attempt_index=1,
            attempt_kind="replacement",
            candidate_id=selected.node_id,
            model=replacement.model,
            provider_endpoint=replacement.provider_endpoint,
            request={},
            status="call_failed",
            answer=None,
            quality_score=0.0,
            gate_reasons=[runtime.FailureCategory.PROVIDER_INVALID_RESPONSE.value],
            latency_seconds=0.1,
            usage={},
            response_id=None,
            response_model=None,
            response_provider=None,
            failure={
                "category": runtime.FailureCategory.PROVIDER_INVALID_RESPONSE.value,
                "retryable": False,
            },
        )
        with mock.patch.object(
            runtime._LegacyExecutionEngine,  # noqa: SLF001
            "_recorded_call",
            return_value=attempt,
        ):
            engine._recorded_call(  # noqa: SLF001
                selected,
                [],
                "task",
                [],
                None,
                lambda *_: ({}, 0.0),
                budget,
                replacement,
                "replacement",
            )
        snapshot = budget.snapshot()
        threshold = snapshot["provider_circuit"]["max_failures"]
        self.assertEqual(threshold, math.ceil(3 / 2))
        self.assertEqual(
            snapshot["provider_circuit"]["failures"][replacement.provider_endpoint],
            1,
        )
        self.assertTrue(budget.endpoint_available(replacement.provider_endpoint))

        budget.fail_endpoint(
            replacement.provider_endpoint,
            runtime.FailureCategory.PROVIDER_INVALID_RESPONSE,
        )
        self.assertFalse(budget.endpoint_available(replacement.provider_endpoint))

    def test_free_recovery_has_no_fixed_penalty_or_gate(self) -> None:
        materialized, _ = optimizer.materialize_top50_selection(
            _optimizer_packet()
        )
        plan = materialized["governance_model_plan"]
        recovery = plan["recovery_models"]
        audit = plan["optimizer_audit"]["recovery_resilience"]
        self.assertGreaterEqual(len(recovery), 1)
        self.assertFalse(audit["free_models_forbidden"])
        self.assertFalse(audit["company_diversity_hard_constraint"])
        self.assertFalse(audit["provider_diversity_hard_constraint"])
        self.assertFalse(audit["capacity_hard_constraint"])
        self.assertEqual(audit["free_route_soft_penalty"], 0)
        self.assertEqual(audit["primary_company_overlap_soft_penalty"], 0)
        self.assertEqual(audit["recovery_company_concentration_soft_penalty"], 0)
        self.assertEqual(
            [row["warm_recovery_priority"] for row in recovery],
            list(range(1, len(recovery) + 1)),
        )
        self.assertTrue(all("recovery_resilience" in row for row in recovery))
        self.assertTrue(
            all(
                row["recovery_resilience"]["soft_free_route_penalty"] == 0
                for row in recovery
            )
        )


if __name__ == "__main__":
    unittest.main()
