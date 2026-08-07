from __future__ import annotations

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


def _graph() -> ExecutionGraph:
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
            }
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


class DynamicRecoveryResilienceTests(unittest.TestCase):
    def test_real_production_shape_expands_to_three_recoveries(self) -> None:
        profile = {
            "task_characters": 272,
            "evidence_characters": 233,
            "requirement_count": 5,
            "evidence_count": 3,
            "acceptance_count": 6,
            "delivery_item_count": 0,
            "pressure": {"overall": 37},
        }
        self.assertEqual(
            optimizer._dynamic_team_shape(profile, 400),  # noqa: SLF001
            (5, 3),
        )
        self.assertAlmostEqual(
            optimizer._recovery_resilience_ratio(profile),  # noqa: SLF001
            0.4766666667,
            places=6,
        )

    def test_shared_pool_capacity_counts_unique_backups_not_node_copies(self) -> None:
        graph = _graph()
        self.assertEqual(runtime._recovery_capacity(graph), 3)  # noqa: SLF001
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
            "unique-current-run-recovery-identities",
        )

    def test_provider_invalid_response_enters_one_failure_run_circuit(self) -> None:
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
            runtime._legacy.ExecutionEngine,  # noqa: SLF001
            "_recorded_call",
            return_value=attempt,
        ):
            returned = engine._recorded_call(  # noqa: SLF001
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
        self.assertIs(returned, attempt)
        self.assertFalse(budget.endpoint_available(replacement.provider_endpoint))
        circuit = budget.snapshot()["provider_circuit"]
        self.assertEqual(circuit["max_failures"], 1)
        self.assertEqual(circuit["failures"][replacement.provider_endpoint], 1)

    def test_free_recovery_is_softly_penalized_not_forbidden(self) -> None:
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
        self.assertTrue(
            any(not str(row["model"]).endswith(":free") for row in recovery)
        )
        self.assertEqual(
            [row["warm_recovery_priority"] for row in recovery],
            list(range(1, len(recovery) + 1)),
        )
        self.assertTrue(
            all("recovery_resilience" in row for row in recovery)
        )


if __name__ == "__main__":
    unittest.main()
