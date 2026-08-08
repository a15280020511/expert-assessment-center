from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from execution_graph import (  # noqa: E402
    ExecutionGraph,
    SelectedEdge,
    SelectedNode,
)
from v5_continuous_spatiotemporal_replanning import (  # noqa: E402
    ContinuousSpatiotemporalExecutionEngine,
    continuous_bind_request_knobs,
    continuous_dynamic_model_timeout_seconds,
)
from v5_recovery_runtime import build_production_runtime  # noqa: E402
from v5_runtime import FailureCategory, RuntimeAttempt, RuntimeConfig  # noqa: E402


def node(
    *,
    model: str = "vendor/model",
    parameter_profile: dict | None = None,
    final: bool = False,
) -> SelectedNode:
    return SelectedNode(
        node_id="n1",
        assigned_work=("w1",),
        professional_capabilities={},
        functions=("synthesis",),
        prompt_profile={},
        reasoning_profile={"reasoning_enabled": True, "effort": "medium"},
        parameter_profile=parameter_profile or {},
        model=model,
        provider_endpoint=f"{model}@openrouter-auto",
        output_contract={
            "required_fields": ["核心判断", "关键依据", "结论"],
            "final_delivery_node": final,
        },
        estimated_quality=0.8,
        quality_uncertainty=0.1,
        estimated_cost=0.01,
        failure_probability=0.1,
        request_config={},
    )


def truncation_attempt(
    *,
    model: str = "vendor/original",
    allowance: int = 1536,
    completion: int = 1536,
) -> RuntimeAttempt:
    return RuntimeAttempt(
        attempt_index=1,
        attempt_kind="initial",
        candidate_id="n1",
        model=model,
        provider_endpoint=f"{model}@openrouter-auto",
        request={"model": model, "max_tokens": allowance},
        status="quality_gate_failed",
        answer="partial",
        quality_score=0.4,
        gate_reasons=["truncated-output"],
        latency_seconds=10.0,
        usage={"completion_tokens": completion},
        response_id="r1",
        response_model=model,
        response_provider="provider-a",
        failure={
            "category": FailureCategory.OUTPUT_TRUNCATED.value,
            "retryable": False,
        },
    )


def timeout_attempt(
    *,
    model: str = "vendor/original",
    timeout_seconds: int = 60,
    latency_seconds: float = 60.0,
) -> RuntimeAttempt:
    return RuntimeAttempt(
        attempt_index=1,
        attempt_kind="initial",
        candidate_id="n1",
        model=model,
        provider_endpoint=f"{model}@openrouter-auto",
        request={"model": model, "max_tokens": 1200},
        status="call_failed",
        answer=None,
        quality_score=0.0,
        gate_reasons=[FailureCategory.PROVIDER_TIMEOUT.value],
        latency_seconds=latency_seconds,
        usage={},
        response_id=None,
        response_model=None,
        response_provider=None,
        failure={
            "category": FailureCategory.PROVIDER_TIMEOUT.value,
            "retryable": True,
        },
        answer_transformations=[
            {
                "type": "dynamic-model-timeout-binding",
                "effective_timeout_seconds": timeout_seconds,
                "safety_cap_seconds": 240,
            }
        ],
    )


class ContinuousSpatiotemporalReplanningTests(unittest.TestCase):
    def test_learned_output_floor_survives_cross_model_replacement(self) -> None:
        engine = object.__new__(ContinuousSpatiotemporalExecutionEngine)
        source = truncation_attempt()
        engine._record_feedback(source)

        replacement = node(model="vendor/replacement")
        adapted, audit = engine._replacement_adaptation(
            replacement,
            source,
            False,
        )
        learned_floor = adapted.parameter_profile[
            "dynamic_output_allowance_floor_tokens"
        ]
        self.assertGreater(learned_floor, 1536)
        self.assertIsNotNone(audit)
        self.assertTrue(
            audit["continuous_spatiotemporal_replanning"]["enabled"]
        )

        payload, binding = continuous_bind_request_knobs(
            adapted,
            "任务" * 50,
            [],
        )
        self.assertGreaterEqual(payload["max_tokens"], learned_floor)
        self.assertEqual(
            binding["current_run_feedback_output_floor_tokens"],
            learned_floor,
        )

        later = node(model="vendor/later")
        later_adapted, _ = engine._replacement_adaptation(
            later,
            source,
            False,
        )
        self.assertGreaterEqual(
            later_adapted.parameter_profile[
                "dynamic_output_allowance_floor_tokens"
            ],
            learned_floor,
        )

    def test_learned_timeout_floor_survives_cross_model_replacement(self) -> None:
        engine = object.__new__(ContinuousSpatiotemporalExecutionEngine)
        source = timeout_attempt(timeout_seconds=60, latency_seconds=60.0)
        engine._record_feedback(source)

        replacement = node(model="vendor/replacement")
        adapted, _ = engine._replacement_adaptation(
            replacement,
            source,
            False,
        )
        learned_floor = adapted.parameter_profile[
            "dynamic_model_timeout_floor_seconds"
        ]
        self.assertGreater(learned_floor, 60)

        payload = {
            "messages": [{"role": "user", "content": "任务"}],
            "reasoning": {"effort": "medium"},
            "max_tokens": 256,
        }
        effective, audit = continuous_dynamic_model_timeout_seconds(
            adapted,
            payload,
            240,
        )
        self.assertGreaterEqual(effective, min(240, learned_floor))
        self.assertEqual(
            audit["current_run_feedback_timeout_floor_seconds"],
            learned_floor,
        )

    def test_time_and_space_state_are_recomputed_from_current_run(self) -> None:
        engine = object.__new__(ContinuousSpatiotemporalExecutionEngine)
        n1 = node(model="vendor/one")
        n2 = SelectedNode(
            node_id="n2",
            assigned_work=n1.assigned_work,
            professional_capabilities=n1.professional_capabilities,
            functions=n1.functions,
            prompt_profile=n1.prompt_profile,
            reasoning_profile=n1.reasoning_profile,
            parameter_profile=n1.parameter_profile,
            model="vendor/two",
            provider_endpoint="vendor/two@openrouter-auto",
            output_contract={
                **dict(n1.output_contract),
                "final_delivery_node": True,
            },
            estimated_quality=n1.estimated_quality,
            quality_uncertainty=n1.quality_uncertainty,
            estimated_cost=n1.estimated_cost,
            failure_probability=n1.failure_probability,
            request_config=n1.request_config,
            independence_group=n1.independence_group,
        )
        graph = ExecutionGraph(
            nodes=(n1, n2),
            edges=(
                SelectedEdge(
                    source="n1",
                    target="n2",
                    relation_type="information",
                    payload_type="text",
                    visibility_policy="declared-upstream-only",
                ),
            ),
            execution_stages=(("n1",), ("n2",)),
            entry_nodes=("n1",),
            final_nodes=("n2",),
            required_work=("w1",),
            estimated_quality=0.8,
            quality_floor=0.0,
            estimated_total_cost=0.02,
            metadata={
                "standby_inventory": [
                    {
                        "model": "vendor/a",
                        "provider_endpoint": "vendor/a@openrouter-auto",
                        "estimated_quality": 0.8,
                        "failure_probability": 0.1,
                        "estimated_cost": 0.01,
                    },
                    {
                        "model": "vendor/b",
                        "provider_endpoint": "vendor/b@openrouter-auto",
                        "estimated_quality": 0.8,
                        "failure_probability": 0.1,
                        "estimated_cost": 0.01,
                    },
                ]
            },
        )
        engine._initialize_feedback(graph)
        self.assertGreater(engine._spatial_pressure("n2"), 0.0)

        failed = truncation_attempt(model="vendor/one")
        engine._record_feedback(failed)
        self.assertGreater(engine._temporal_pressure("n1"), 0.0)
        snapshot = engine._feedback_snapshot()
        self.assertTrue(snapshot["continuous_spatiotemporal_replanning"])
        self.assertEqual(snapshot["current_replan_epoch"], 1)
        self.assertEqual(
            snapshot["node_runtime_state"]["n1"]["truncations"],
            1,
        )
        self.assertTrue(snapshot["finite_graph_invariant_preserved"])
        self.assertFalse(snapshot["initial_recovery_order_static"])
        self.assertTrue(
            snapshot["recovery_candidate_space_recomputed_each_iteration"]
        )

    def test_production_runtime_installs_continuous_engine(self) -> None:
        runtime = build_production_runtime(
            RuntimeConfig(
                total_call_limit=4,
                recovery_call_limit=2,
                cost_anomaly_usd=None,
                tools_allowed=False,
                live_catalog_required=False,
                provider_lock_required=False,
            )
        )
        self.assertIsInstance(
            runtime.execution_engine,
            ContinuousSpatiotemporalExecutionEngine,
        )


if __name__ == "__main__":
    unittest.main()
