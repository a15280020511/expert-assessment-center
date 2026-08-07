from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import ExecutionGraph, SelectedNode  # noqa: E402
from v5_production_expert_policy import EvidenceCompleteExecutionEngine  # noqa: E402
from v5_runtime import (  # noqa: E402
    FailureCategory,
    OutputPolicy,
    QualityGatePolicy,
    RecoveryPolicy,
    RetryPolicy,
    RuntimeAttempt,
    RuntimeConfig,
)
from v5_soft_resource_governance import SoftResourcePromptPolicy  # noqa: E402


def node(model: str = "vendor/main") -> SelectedNode:
    return SelectedNode(
        node_id="n1",
        assigned_work=("w1",),
        professional_capabilities={},
        functions=("analysis",),
        prompt_profile={},
        reasoning_profile={},
        parameter_profile={},
        model=model,
        provider_endpoint=f"{model}@openrouter-auto",
        output_contract={},
        estimated_quality=0.0,
        quality_uncertainty=0.0,
        estimated_cost=0.0,
        failure_probability=0.0,
        request_config={},
    )


def attempt(model: str, *, passed: bool) -> RuntimeAttempt:
    category = FailureCategory.QUALITY_GATE_FAILED
    return RuntimeAttempt(
        attempt_index=1,
        attempt_kind="replacement" if model != "vendor/main" else "initial",
        candidate_id="n1",
        model=model,
        provider_endpoint=f"{model}@openrouter-auto",
        request={},
        status="passed" if passed else "quality_gate_failed",
        answer=(
            "## 核心判断\n可用。\n\n## 关键依据\n充分。\n\n"
            "## 不确定性与反例\n已检查。\n\n## 可执行结论\n完成。"
            if passed
            else "不合格"
        ),
        quality_score=1.0 if passed else 0.0,
        gate_reasons=[] if passed else [category.value],
        latency_seconds=0.01,
        usage={},
        response_id="response-1" if passed else None,
        response_model=model if passed else None,
        response_provider="fixture" if passed else None,
        failure=(
            None
            if passed
            else {"category": category.value, "retryable": False}
        ),
    )


class ProductionStandbyPromotionTests(unittest.TestCase):
    def test_active_production_engine_promotes_standby_after_initial_recovery_exhaustion(self) -> None:
        selected = node()
        graph = ExecutionGraph(
            nodes=(selected,),
            edges=(),
            execution_stages=(("n1",),),
            entry_nodes=("n1",),
            final_nodes=("n1",),
            required_work=("w1",),
            estimated_quality=0.0,
            quality_floor=0.0,
            estimated_total_cost=0.0,
            metadata={
                "recovery_pool": {"n1": []},
                "standby_inventory": [
                    {
                        "model": "vendor/standby-1",
                        "provider_endpoint": "vendor/standby-1@openrouter-auto",
                        "estimated_cost": 0.0,
                    },
                    {
                        "model": "vendor/standby-2",
                        "provider_endpoint": "vendor/standby-2@openrouter-auto",
                        "estimated_cost": 0.0,
                    },
                ],
            },
        )
        engine = EvidenceCompleteExecutionEngine(
            RuntimeConfig(1, 0, None),
            prompt_policy=SoftResourcePromptPolicy(),
            retry_policy=RetryPolicy(),
            recovery_policy=RecoveryPolicy(),
            quality_policy=QualityGatePolicy(),
            output_policy=OutputPolicy(),
        )
        engine._initialize_feedback(graph)  # noqa: SLF001
        initial = attempt("vendor/main", passed=False)
        attempts = [initial]
        engine._record_feedback(initial)  # noqa: SLF001
        called: list[str] = []

        def call(candidate: SelectedNode, kind: str):
            self.assertEqual("replacement", kind)
            called.append(candidate.model)
            return attempt(candidate.model, passed=True)

        result, _best, last = engine._recover_node(  # noqa: SLF001
            selected,
            attempts,
            [],
            FailureCategory.QUALITY_GATE_FAILED,
            None,
            call,
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("success_recovered", result.status)
        self.assertEqual("vendor/standby-1", result.resolved_model)
        self.assertEqual("vendor/standby-1", last.model)
        self.assertEqual(["vendor/standby-1"], called)
        snapshot = engine._feedback_snapshot()  # noqa: SLF001
        self.assertTrue(snapshot["enabled"])
        self.assertEqual(2, snapshot["standby_total"])
        self.assertEqual(1, snapshot["standby_promoted_or_claimed"])
        self.assertEqual(1, snapshot["promotion_attempts"])
        self.assertEqual(1, len(snapshot["events"]))
        self.assertEqual("vendor/standby-1", snapshot["events"][0]["promoted_model"])
        self.assertTrue(snapshot["events"][0]["passed"])
        self.assertFalse(snapshot["promotion_depth_fixed"])
        self.assertTrue(snapshot["promotion_depth_recomputed_from_current_run"])


if __name__ == "__main__":
    unittest.main()
