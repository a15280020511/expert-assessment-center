from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

import v5_task_scope_quality_circuit as task_scope  # noqa: E402
from v5_provider_account_repair_audit import (  # noqa: E402
    ProviderAccountRepairAuditExecutionEngine,
    refine_provider_account_transport_state,
)
from v5_recovery_runtime import build_production_runtime  # noqa: E402
from v5_runtime import RuntimeConfig  # noqa: E402


class FinalDeliverySemanticsTests(unittest.TestCase):
    def _build_runtime(self):
        return build_production_runtime(
            RuntimeConfig(
                total_call_limit=10,
                recovery_call_limit=5,
                cost_anomaly_usd=None,
                tools_allowed=False,
                live_catalog_required=True,
                provider_lock_required=False,
            )
        )

    def test_run414_fact_or_general_experience_wording_accepts_explicit_experience(self) -> None:
        self._build_runtime()
        task = (
            "最终报告必须明确区分事实/一般经验、推断、条件性判断和建议，"
            "并给出总体排序和决策表。"
        )
        answer = (
            "以下分析基于一般行业经验及逻辑推断。\n"
            "推断：在追求稳定时应优先比较保安和快递。\n"
            "条件性判断：若已有合适车辆，网约车的门槛会下降。\n"
            "建议：普通求职者先比较保安与快递。"
        )
        violations = task_scope.business_task_obligation_violations(task, answer)
        self.assertNotIn(
            "missing-task-obligation:classification:fact",
            violations,
        )
        self.assertNotIn(
            "missing-task-obligation:classification:calculated",
            violations,
        )

    def test_fact_only_requirement_still_requires_explicit_fact_classification(self) -> None:
        self._build_runtime()
        task = "请明确区分事实、判断和建议。"
        answer = "一般行业经验：需求有波动。判断：A更稳。建议：选择A。"
        violations = task_scope.business_task_obligation_violations(task, answer)
        self.assertIn(
            "missing-task-obligation:classification:fact",
            violations,
        )

    def test_general_experience_must_be_explicit_for_combined_category(self) -> None:
        self._build_runtime()
        task = "请明确区分事实/一般经验、判断和建议。"
        answer = "判断：A更稳。建议：选择A。"
        violations = task_scope.business_task_obligation_violations(task, answer)
        self.assertIn(
            "missing-task-obligation:classification:fact",
            violations,
        )

    def test_production_runtime_installs_provider_repair_audit_layer(self) -> None:
        runtime = self._build_runtime()
        self.assertIsInstance(
            runtime.execution_engine,
            ProviderAccountRepairAuditExecutionEngine,
        )

    def test_observed_zero_cost_transport_repair_is_distinguished_from_paid_block(self) -> None:
        refined = refine_provider_account_transport_state(
            {
                "provider_account_transport_state": {
                    "blocked": True,
                    "reason": "openrouter-http-402-insufficient-credits",
                    "model_replacement_can_repair": False,
                },
                "runtime_feedback_replanning": {
                    "provider_credit_zero_cost_recovery": {
                        "candidate_count": 5,
                        "events": [
                            {
                                "event_type": "provider-credit-zero-cost-recovery-attempt",
                                "attempt_status": "quality_gate_failed",
                                "model": "google/gemma:free",
                            }
                        ],
                    }
                },
            }
        )
        state = refined["provider_account_transport_state"]
        self.assertFalse(state["paid_model_replacement_can_repair"])
        self.assertTrue(state["signed_zero_cost_recovery_available"])
        self.assertTrue(state["signed_zero_cost_transport_repair_observed"])
        self.assertTrue(state["model_replacement_can_repair"])
        self.assertFalse(state["privacy_policy_relaxed_or_overridden"])

    def test_untried_zero_cost_space_reports_unknown_not_false_repairability(self) -> None:
        refined = refine_provider_account_transport_state(
            {
                "provider_account_transport_state": {"blocked": True},
                "runtime_feedback_replanning": {
                    "provider_credit_zero_cost_recovery": {
                        "candidate_count": 4,
                        "events": [],
                    }
                },
            }
        )
        state = refined["provider_account_transport_state"]
        self.assertIsNone(state["model_replacement_can_repair"])
        self.assertTrue(state["signed_zero_cost_recovery_available"])
        self.assertFalse(state["signed_zero_cost_transport_repair_observed"])


if __name__ == "__main__":
    unittest.main()
