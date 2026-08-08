from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from execution_graph import SelectedNode  # noqa: E402
from v5_production_expert_policy import EvidenceCompleteExecutionEngine  # noqa: E402
from v5_runtime import FailureCategory, RuntimeAttempt  # noqa: E402
from v5_runtime_request_binding import dynamic_output_allowance  # noqa: E402


def node(*, effort: str = "medium", model: str = "vendor/model") -> SelectedNode:
    return SelectedNode(
        node_id="n1",
        assigned_work=("w1",),
        professional_capabilities={},
        functions=("synthesis",),
        prompt_profile={},
        reasoning_profile={"reasoning_enabled": True, "effort": effort},
        parameter_profile={},
        model=model,
        provider_endpoint=f"{model}@openrouter-auto",
        output_contract={
            "required_fields": ["核心判断", "关键依据", "不确定性", "结论"],
        },
        estimated_quality=0.8,
        quality_uncertainty=0.1,
        estimated_cost=0.01,
        failure_probability=0.1,
        request_config={},
    )


class FailureAdaptiveRecoveryTests(unittest.TestCase):
    def test_reasoning_ratio_reserves_visible_output(self) -> None:
        low = dynamic_output_allowance(node(effort="low"), "任务" * 200, [])
        high = dynamic_output_allowance(node(effort="high"), "任务" * 200, [])
        self.assertGreater(high, low)
        self.assertGreater(low, 0)

    def test_quality_failure_reranks_by_current_quality_signal(self) -> None:
        rows = [
            {"model": "cheap", "estimated_quality": 0.4, "failure_probability": 0.01, "estimated_cost": 0.001},
            {"model": "strong", "estimated_quality": 0.9, "failure_probability": 0.2, "estimated_cost": 0.02},
        ]
        ranked = EvidenceCompleteExecutionEngine._rank_rows_for_failure(
            rows,
            FailureCategory.QUALITY_GATE_FAILED,
        )
        self.assertEqual("strong", ranked[0]["model"])

    def test_provider_failure_reranks_by_current_failure_risk(self) -> None:
        rows = [
            {"model": "strong-risky", "estimated_quality": 0.95, "failure_probability": 0.5, "estimated_cost": 0.01},
            {"model": "stable", "estimated_quality": 0.7, "failure_probability": 0.02, "estimated_cost": 0.02},
        ]
        ranked = EvidenceCompleteExecutionEngine._rank_rows_for_failure(
            rows,
            FailureCategory.PROVIDER_INVALID_RESPONSE,
        )
        self.assertEqual("stable", ranked[0]["model"])

    def test_truncation_feedback_increases_next_request_allowance(self) -> None:
        engine = object.__new__(EvidenceCompleteExecutionEngine)
        replacement = node(effort="medium", model="vendor/replacement")
        source = RuntimeAttempt(
            attempt_index=1,
            attempt_kind="initial",
            candidate_id="n1",
            model="vendor/original",
            provider_endpoint="vendor/original@openrouter-auto",
            request={"model": "vendor/original", "max_tokens": 1000},
            status="quality_gate_failed",
            answer="partial",
            quality_score=0.4,
            gate_reasons=["truncated-output"],
            latency_seconds=0.1,
            usage={"completion_tokens": 1000},
            response_id="r1",
            response_model="vendor/original",
            response_provider="provider",
            failure={
                "category": FailureCategory.OUTPUT_TRUNCATED.value,
                "retryable": False,
            },
        )
        adapted, audit = engine._replacement_adaptation(
            replacement,
            source,
            False,
        )
        self.assertIsNotNone(audit)
        self.assertEqual(
            "current-run-failure-derived-request-rebinding-v1",
            audit["policy"],
        )
        self.assertTrue(
            any(
                row.get("policy")
                == "current-run-truncation-derived-output-allowance-v1"
                for row in audit["components"]
            )
        )
        self.assertGreater(
            adapted.parameter_profile["dynamic_output_allowance_multiplier"],
            1.0,
        )
        base = dynamic_output_allowance(replacement, "任务" * 100, [])
        increased = dynamic_output_allowance(adapted, "任务" * 100, [])
        self.assertGreater(increased, base)

    def test_timeout_feedback_increases_current_run_timeout_multiplier(self) -> None:
        engine = object.__new__(EvidenceCompleteExecutionEngine)
        replacement = node(effort="medium", model="vendor/replacement")
        source = RuntimeAttempt(
            attempt_index=1,
            attempt_kind="initial",
            candidate_id="n1",
            model="vendor/original",
            provider_endpoint="vendor/original@openrouter-auto",
            request={"model": "vendor/original", "max_tokens": 1200},
            status="call_failed",
            answer=None,
            quality_score=0.0,
            gate_reasons=[FailureCategory.PROVIDER_TIMEOUT.value],
            latency_seconds=60.0,
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
                    "effective_timeout_seconds": 60,
                    "safety_cap_seconds": 240,
                }
            ],
        )
        adapted, audit = engine._replacement_adaptation(
            replacement,
            source,
            False,
        )
        self.assertIsNotNone(audit)
        self.assertEqual(
            "current-run-failure-derived-request-rebinding-v1",
            audit["policy"],
        )
        self.assertTrue(
            any(
                row.get("policy") == "current-run-timeout-derived-deadline-v1"
                for row in audit["components"]
            )
        )
        self.assertGreater(
            adapted.parameter_profile["dynamic_model_timeout_multiplier"],
            1.0,
        )


if __name__ == "__main__":
    unittest.main()
