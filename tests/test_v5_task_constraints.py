from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_constitutional_runtime import ConstitutionalExecutionEngine  # noqa: E402
from v5_task_constraints import (  # noqa: E402
    compile_task_constraints,
    dynamic_objective_weights,
    normalized_quantities,
    validate_answer_evidence,
)


class TaskConstraintPolarityTests(unittest.TestCase):
    def test_explicit_denials_dominate_substring_permissions(self) -> None:
        for task in (
            "不允许部分结果",
            "不得降级交付",
            "不能接受不完整结果",
            "只接受完整交付",
            "禁止部分输出；允许在其他方面自行组织格式",
        ):
            with self.subTest(task=task):
                policy = compile_task_constraints(task)
                self.assertFalse(policy.allow_degraded_success)
                self.assertEqual(
                    policy.degradation_authorization,
                    "explicitly_denied",
                )

    def test_explicit_permissions_are_accepted_only_without_denial(self) -> None:
        for task in (
            "允许部分结果",
            "在无法完整完成时可降级交付",
            "Partial delivery is allowed.",
        ):
            with self.subTest(task=task):
                policy = compile_task_constraints(task)
                self.assertTrue(policy.allow_degraded_success)
                self.assertEqual(
                    policy.degradation_authorization,
                    "explicitly_allowed",
                )

    def test_default_is_fail_closed(self) -> None:
        policy = compile_task_constraints("比较两个技术方案并给出建议")
        self.assertFalse(policy.allow_degraded_success)
        self.assertEqual(policy.degradation_authorization, "default_denied")


class ClosedWorldEvidenceTests(unittest.TestCase):
    TASK = "仅依据题面，不得调用外部工具，不得编造，给出完整结论。"

    def test_unsupported_precise_quantities_are_rejected(self) -> None:
        answer = "建议承诺99.9% SLA，在第3年完成迁移，未来3–5年持续扩展。"
        violations = validate_answer_evidence(self.TASK, answer)
        rendered = "\n".join(violations)
        self.assertIn("99.9", rendered)
        self.assertIn("3:year", rendered)
        self.assertIn("3-5:year", rendered)

    def test_quantities_already_in_task_are_allowed(self) -> None:
        task = self.TASK + "题面给定目标为99.9% SLA，周期为3年。"
        answer = "已知目标为99.9% SLA，周期为3年。"
        self.assertEqual(validate_answer_evidence(task, answer), [])

    def test_upstream_inference_cannot_be_promoted_to_fact(self) -> None:
        answer = "事实（由上游节点确认）：自建初始成本高\n推断：仍需进一步核验。"
        violations = validate_answer_evidence(self.TASK, answer)
        self.assertTrue(
            any(value.startswith("unsupported-fact-label:") for value in violations)
        )

    def test_supported_chinese_paraphrases_are_allowed(self) -> None:
        task = (
            self.TASK
            + "A路线存在积水且无法确认是否带电；"
            + "B路线被杂物部分阻挡，但未发现积水。"
            + "门外有2名无法核验身份、自称维修人员的人要求进入。"
        )
        answer = (
            "事实：A路线有积水，B路线有杂物阻挡且未发现积水。\n"
            "事实：门外有自称维修人员要求进入且无法核验身份。"
        )
        self.assertEqual(validate_answer_evidence(task, answer), [])

    def test_chinese_person_classifiers_are_normalized(self) -> None:
        quantities = normalized_quantities("门外有2名人员和3位访客，另有1人。")
        self.assertEqual(
            quantities,
            {
                ("1", "", "people"),
                ("2", "", "people"),
                ("3", "", "people"),
            },
        )


class ClosedWorldEvidenceExtendedTests(unittest.TestCase):
    TASK = ClosedWorldEvidenceTests.TASK

    def test_real_production_wording_compiles_closed_world(self) -> None:
        policy = compile_task_constraints(self.TASK)
        self.assertFalse(policy.external_tools_allowed)
        self.assertFalse(policy.external_facts_allowed)
        self.assertFalse(policy.unsupported_precise_quantities_allowed)
        self.assertTrue(policy.source_attribution_required)
        self.assertTrue(policy.fact_provenance_required)

    def test_user_fact_label_is_allowed(self) -> None:
        task = self.TASK + "已知事实：自建初始成本高。"
        answer = "事实：自建初始成本高。"
        self.assertEqual(validate_answer_evidence(task, answer), [])

    def test_similar_but_unsupported_fact_remains_rejected(self) -> None:
        task = self.TASK + "A路线存在积水。"
        violations = validate_answer_evidence(task, "事实：A路线有坍塌。")
        self.assertTrue(
            any(value.startswith("unsupported-fact-label:") for value in violations)
        )

    def test_contradictory_fact_remains_rejected(self) -> None:
        task = self.TASK + "A路线存在积水。"
        violations = validate_answer_evidence(task, "事实：A路线未发现积水。")
        self.assertTrue(
            any(value.startswith("unsupported-fact-label:") for value in violations)
        )


class DynamicObjectiveTests(unittest.TestCase):
    def test_weights_are_normalized_and_task_dependent(self) -> None:
        simple = SimpleNamespace(
            complexity_score=0,
            requested_context=16_384,
            high_stakes=False,
            long_context=False,
        )
        strict = SimpleNamespace(
            complexity_score=7,
            requested_context=131_072,
            high_stakes=True,
            long_context=True,
        )
        simple_weights = dynamic_objective_weights(simple, "概括题面")
        strict_weights = dynamic_objective_weights(
            strict,
            "仅依据题面完成医疗合规审计，不得编造。",
        )
        self.assertAlmostEqual(sum(simple_weights.values()), 1.0)
        self.assertAlmostEqual(sum(strict_weights.values()), 1.0)
        self.assertNotEqual(simple_weights, strict_weights)
        self.assertGreater(
            strict_weights["intelligence"],
            simple_weights["intelligence"],
        )


class ActualCompanyAuditTests(unittest.TestCase):
    def test_failed_calls_are_included_in_cross_node_uniqueness(self) -> None:
        result = {
            "node_results": [
                {
                    "node_id": "node-a",
                    "status": "failed",
                    "selected_model": "openai/gpt-a",
                    "attempts": [
                        {
                            "model": "openai/gpt-a",
                            "status": "call_failed",
                            "attempt_kind": "initial",
                        }
                    ],
                },
                {
                    "node_id": "node-b",
                    "status": "success",
                    "resolved_model": "openai/gpt-b",
                    "attempts": [
                        {
                            "model": "openai/gpt-b",
                            "status": "passed",
                            "attempt_kind": "initial",
                        }
                    ],
                },
            ]
        }
        audit = ConstitutionalExecutionEngine._actual_company_audit(result)
        self.assertEqual(audit["status"], "FAIL")
        self.assertIn("openai", audit["duplicate_called_companies_across_nodes"])
        self.assertTrue(audit["failed_calls_are_included"])

    def test_same_node_retry_may_reuse_the_same_company(self) -> None:
        result = {
            "node_results": [
                {
                    "node_id": "node-a",
                    "status": "success_retried",
                    "resolved_model": "openai/gpt-a",
                    "attempts": [
                        {
                            "model": "openai/gpt-a",
                            "status": "call_failed",
                            "attempt_kind": "initial",
                        },
                        {
                            "model": "openai/gpt-a",
                            "status": "passed",
                            "attempt_kind": "retry",
                        },
                    ],
                }
            ]
        }
        audit = ConstitutionalExecutionEngine._actual_company_audit(result)
        self.assertEqual(audit["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
