from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_task_delivery_contract as contracts  # noqa: E402
from v5_constitutional_runtime import (  # noqa: E402
    ConstitutionalExecutionEngine,
    validate_scope_boundaries,
)
from v5_recovery_runtime import build_production_runtime  # noqa: E402
from v5_runtime import FailureCategory, RuntimeConfig  # noqa: E402


class V5ConstitutionalRuntimeTests(unittest.TestCase):
    def test_missing_explicit_heading_fails_before_success(self) -> None:
        task = (
            "严格使用以下3个 Markdown 二级标题，顺序不得改变，每项不得为空：\n"
            "1）已知条件\n2）比较结果\n3）风险与下一步"
        )
        contract = contracts.extract_explicit_markdown_contract(task)
        answer = "## 已知条件\nA\n\n## 比较结果\nB"
        violations = contracts.validate_answer_contract(answer, contract)
        self.assertIn(
            "missing-exact-markdown-heading:风险与下一步",
            violations,
        )

    def test_closed_world_rejects_new_precise_quantity(self) -> None:
        task = "仅依据题面，不联网，不得编造。现有电量可维持1小时。"
        answer = "现有电量可维持1小时，备用照明至少持续3小时。"
        violations = validate_scope_boundaries(task, answer)
        self.assertEqual(
            ["closed-world-unsupported-quantity:3:hour"],
            violations,
        )

    def test_closed_world_allows_quantities_already_in_task(self) -> None:
        task = "仅依据题面。试用7天，每天记录2次。"
        answer = "执行7天，每天记录2次。"
        self.assertEqual([], validate_scope_boundaries(task, answer))

    def test_quality_contract_failure_is_recoverable_by_other_company(self) -> None:
        runtime = build_production_runtime(
            RuntimeConfig(
                total_call_limit=4,
                recovery_call_limit=1,
                tools_allowed=False,
                provider_lock_required=True,
            )
        )
        self.assertIn(
            FailureCategory.QUALITY_GATE_FAILED,
            runtime.recovery_policy.replace_categories,
        )
        self.assertIsInstance(
            runtime.execution_engine,
            ConstitutionalExecutionEngine,
        )

    def test_actual_successful_company_set_is_recomputed(self) -> None:
        result = {
            "node_results": [
                {
                    "node_id": "n1",
                    "selected_model": "openai/model-a",
                    "resolved_model": "anthropic/model-b",
                    "status": "success_recovered",
                    "attempts": [
                        {
                            "model": "openai/model-a",
                            "status": "quality_gate_failed",
                        },
                        {
                            "model": "anthropic/model-b",
                            "status": "passed",
                        },
                    ],
                },
                {
                    "node_id": "n2",
                    "selected_model": "google/model-c",
                    "resolved_model": "google/model-c",
                    "status": "success",
                    "attempts": [
                        {"model": "google/model-c", "status": "passed"}
                    ],
                },
            ]
        }
        audit = ConstitutionalExecutionEngine._actual_company_audit(result)
        self.assertEqual("PASS", audit["status"])
        self.assertEqual(
            ["anthropic", "google"],
            sorted(
                row["company"]
                for row in audit["successful_node_models"]
            ),
        )
        self.assertEqual(3, len(audit["all_called_models"]))

    def test_duplicate_actual_successful_companies_fail_closed(self) -> None:
        result = {
            "node_results": [
                {
                    "node_id": "n1",
                    "resolved_model": "google/model-a",
                    "status": "success",
                    "attempts": [],
                },
                {
                    "node_id": "n2",
                    "resolved_model": "deepmind/model-b",
                    "status": "success_recovered",
                    "attempts": [],
                },
            ]
        }
        audit = ConstitutionalExecutionEngine._actual_company_audit(result)
        self.assertEqual("FAIL", audit["status"])
        self.assertEqual(
            {"google": ["n1", "n2"]},
            audit["duplicate_successful_companies"],
        )


if __name__ == "__main__":
    unittest.main()
