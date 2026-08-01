from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from v5_operational_resilience import (  # noqa: E402
    OperationalResiliencePlannerPolicy,
    calibrate_endpoint_operational_profile,
    poisson_binomial_tail,
    uniform_failure_cap,
)
from v5_planner import CandidateNode  # noqa: E402
from v5_recovery_runtime import build_production_runtime  # noqa: E402
from v5_runtime import FailureCategory, RuntimeConfig  # noqa: E402


class V5OperationalResilienceTests(unittest.TestCase):
    def config(self) -> RuntimeConfig:
        return RuntimeConfig(
            total_call_limit=5,
            recovery_call_limit=1,
            cost_anomaly_usd=0.35,
            quality_tier="value",
            tools_allowed=False,
            provider_lock_required=True,
        )

    @staticmethod
    def candidate() -> CandidateNode:
        return CandidateNode(
            candidate_id="node-operational",
            interpretation_id="interpretation-operational",
            coverage_keys=("work-operational#0",),
            assigned_work=("work-operational",),
            copy_indices=(0,),
            professional_capabilities={"general_analysis": 0.8},
            functions=("analysis",),
            prompt_profile={"modules": ["scope_control"]},
            reasoning_profile={"reasoning_enabled": True, "effort": "medium"},
            parameter_profile={},
            model="example/model",
            provider_endpoint="example/model@example/provider",
            provider_slug="example/provider",
            output_contract={
                "required_fields": ["assumptions", "conclusions"],
                "machine_readable_required": False,
                "must_separate_fact_assumption_inference": True,
            },
            estimated_quality=0.7,
            quality_uncertainty=0.1,
            estimated_cost=0.01,
            failure_probability=0.04,
            request_config={
                "provider": {
                    "only": ["example/provider"],
                    "order": ["example/provider"],
                    "allow_fallbacks": False,
                    "require_parameters": True,
                }
            },
            independence_groups=(),
        )

    def test_openrouter_percentage_uptime_is_not_clamped_to_one(self) -> None:
        profile = calibrate_endpoint_operational_profile(
            {
                "status": 0,
                "uptime_last_1d": 91.0,
                "uptime_last_30m": 95.0,
                "uptime_last_5m": 94.0,
                "latency_last_30m": {"p90": 6000},
                "throughput_last_30m": {"p50": 40},
            }
        )
        self.assertGreaterEqual(profile["operational_reliability"], 0.90)
        self.assertLess(profile["operational_reliability"], 0.95)
        self.assertTrue(profile["uptime_percentage_normalized"])
        self.assertEqual(6000.0, profile["latency_p90_ms"])
        self.assertEqual(40.0, profile["throughput_p50_tps"])

    def test_missing_operational_evidence_uses_conservative_prior(self) -> None:
        profile = calibrate_endpoint_operational_profile({})
        self.assertEqual(0.90, profile["operational_reliability"])
        self.assertEqual(0.0, profile["operational_evidence_confidence"])

    def test_deadline_infeasible_candidate_is_removed_before_optimizer(self) -> None:
        policy = OperationalResiliencePlannerPolicy(self.config())
        works = [
            {
                "context_requirements": {"expected_output_tokens": 5851},
            }
        ]
        endpoint = {
            "throughput_p50_tps": 25.0,
            "latency_p90_ms": 830.0,
        }
        with patch.object(
            OperationalResiliencePlannerPolicy.__mro__[1],
            "candidate_factory",
            return_value=self.candidate(),
        ), patch.dict(os.environ, {"MODEL_TIMEOUT_SECONDS": "240"}):
            result = policy.candidate_factory(
                "interpretation-operational",
                ["work-operational#0"],
                works,
                [0],
                endpoint,
                {},
                {},
                [],
            )
        self.assertIsNone(result)

    def test_fast_candidate_keeps_audited_serviceability_evidence(self) -> None:
        policy = OperationalResiliencePlannerPolicy(self.config())
        works = [
            {
                "context_requirements": {"expected_output_tokens": 2500},
            }
        ]
        endpoint = {
            "throughput_p50_tps": 80.0,
            "latency_p90_ms": 1000.0,
        }
        with patch.object(
            OperationalResiliencePlannerPolicy.__mro__[1],
            "candidate_factory",
            return_value=self.candidate(),
        ), patch.dict(os.environ, {"MODEL_TIMEOUT_SECONDS": "240"}):
            result = policy.candidate_factory(
                "interpretation-operational",
                ["work-operational#0"],
                works,
                [0],
                endpoint,
                {},
                {},
                [],
            )
        self.assertIsNotNone(result)
        evidence = result.parameter_profile["operational_serviceability"]
        self.assertTrue(evidence["deadline_feasible"])
        self.assertLess(evidence["estimated_deadline_ratio"], 0.50)
        self.assertFalse(evidence["cross_task_history_used"])

    def test_poisson_binomial_tail_and_uniform_cap(self) -> None:
        tail = poisson_binomial_tail([0.10, 0.10, 0.10, 0.10], 1)
        self.assertGreater(tail, 0.05)
        cap = uniform_failure_cap(4, 1, 0.05)
        self.assertLess(cap, 0.10)
        self.assertLessEqual(
            poisson_binomial_tail([cap] * 4, 1),
            0.05 + 1e-12,
        )

    def test_recovery_sufficiency_detects_two_failure_tail(self) -> None:
        policy = OperationalResiliencePlannerPolicy(self.config())
        nodes = [
            {"node_id": f"node-{index}", "failure_probability": 0.10}
            for index in range(4)
        ]
        result = {
            "execution_graph": {
                "nodes": nodes,
                "metadata": {
                    "recovery_pool": {
                        row["node_id"]: [{"candidate_id": f"replacement-{row['node_id']}"}]
                        for row in nodes
                    }
                },
            }
        }
        assessment = policy._assess_recovery_sufficiency(result)
        self.assertEqual("FAIL", assessment["status"])
        self.assertIn(
            "unrecoverable-failure-tail-above-limit",
            assessment["blockers"],
        )

    def test_production_recovery_never_retries_failed_endpoint(self) -> None:
        runtime = build_production_runtime(self.config())
        self.assertEqual((), runtime.retry_policy.retry_same_endpoint_categories)
        self.assertEqual(
            0,
            runtime.retry_policy.maximum_same_endpoint_retries_per_node,
        )
        self.assertIn(
            FailureCategory.PROVIDER_TIMEOUT,
            runtime.recovery_policy.replace_categories,
        )
        self.assertIn(
            FailureCategory.PROVIDER_EMPTY_RESPONSE,
            runtime.recovery_policy.replace_categories,
        )
        self.assertIn(
            FailureCategory.PROVIDER_RATE_LIMITED,
            runtime.recovery_policy.replace_categories,
        )


if __name__ == "__main__":
    unittest.main()
