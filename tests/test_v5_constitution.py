from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_constitution import (  # noqa: E402
    compile_task_constitution,
    constitution_manifest,
    dynamic_objective_weights,
    validate_answer_against_constitution,
)


class V5ConstitutionTests(unittest.TestCase):
    def test_negative_degradation_language_wins(self) -> None:
        for task in (
            "不允许部分结果",
            "不得降级交付",
            "不能接受不完整结果",
            "只接受完整交付",
        ):
            with self.subTest(task=task):
                policy = compile_task_constitution(task)
                self.assertEqual("forbidden", policy.degradation_authorization)
                self.assertNotEqual(
                    "explicit-positive-user-polarity",
                    policy.degradation_source,
                )
                self.assertFalse(
                    policy.contradictory_degradation_language,
                    task,
                )

    def test_positive_degradation_requires_explicit_authorization(self) -> None:
        policy = compile_task_constitution("无法完整完成时允许部分结果")
        self.assertEqual("allowed", policy.degradation_authorization)
        self.assertEqual(
            "explicit-positive-user-polarity",
            policy.degradation_source,
        )

    def test_contradictory_language_fails_closed(self) -> None:
        policy = compile_task_constitution(
            "允许部分结果，但不允许降级交付。"
        )
        self.assertTrue(policy.contradictory_degradation_language)
        self.assertEqual("forbidden", policy.degradation_authorization)

    def test_closed_world_includes_external_tool_prohibition(self) -> None:
        policy = compile_task_constitution("不得调用外部工具，只依据题面。")
        self.assertEqual("closed-world", policy.evidence_mode)
        self.assertEqual(
            "forbidden",
            policy.unsupported_precise_quantity_policy,
        )

    def test_closed_world_rejects_new_quantity(self) -> None:
        violations = validate_answer_against_constitution(
            "不得调用外部工具，仅依据题面。现有电量可维持1小时。",
            "现有电量可维持1小时，备用方案可持续3小时。",
        )
        self.assertEqual(
            ["closed-world-unsupported-quantity:3:hour"],
            violations,
        )

    def test_open_evidence_requires_assumption_label_for_new_quantity(self) -> None:
        violations = validate_answer_against_constitution(
            "制定实施方案。",
            "系统应达到99.9% SLA。",
        )
        self.assertIn("unsupported-unlabeled-quantity:99.9:%", violations)
        self.assertEqual(
            [],
            validate_answer_against_constitution(
                "制定实施方案。",
                "建议阈值：系统目标可暂定为99.9% SLA。",
            ),
        )

    def test_fact_labels_require_supported_source(self) -> None:
        self.assertIn(
            "fact-provenance-missing",
            validate_answer_against_constitution(
                "已知：预算紧张。",
                "事实：预算紧张。",
                require_claim_labels=True,
            ),
        )
        self.assertIn(
            "fact-provenance-unsupported-task",
            validate_answer_against_constitution(
                "已知：预算紧张。",
                "事实（题面）：系统已有99.9% SLA。",
                require_claim_labels=True,
            ),
        )
        self.assertEqual(
            [],
            validate_answer_against_constitution(
                "已知事实：预算紧张。",
                "事实（题面）：预算紧张。",
                require_claim_labels=True,
            ),
        )

    def test_upstream_fact_requires_tagged_supported_upstream(self) -> None:
        upstream = [
            {
                "node_id": "n1",
                "answer": "事实（题面）：当前预算紧张。",
            }
        ]
        self.assertEqual(
            [],
            validate_answer_against_constitution(
                "分析预算。",
                "事实（上游:n1）：当前预算紧张。",
                upstream=upstream,
                require_claim_labels=True,
            ),
        )
        self.assertIn(
            "fact-provenance-unsupported-upstream",
            validate_answer_against_constitution(
                "分析预算。",
                "事实（上游:n1）：预算充足。",
                upstream=upstream,
                require_claim_labels=True,
            ),
        )

    def test_objective_weights_are_task_derived_and_normalized(self) -> None:
        simple = SimpleNamespace(
            complexity_score=1,
            requested_context=16_384,
            high_stakes=False,
            long_context=False,
        )
        complex_profile = SimpleNamespace(
            complexity_score=7,
            requested_context=131_072,
            high_stakes=True,
            long_context=True,
        )
        budget = SimpleNamespace(quality_tier="budget")
        quality = SimpleNamespace(quality_tier="quality")
        simple_weights = dynamic_objective_weights(simple, budget)
        complex_weights = dynamic_objective_weights(complex_profile, quality)
        self.assertAlmostEqual(1.0, sum(simple_weights.values()), places=6)
        self.assertAlmostEqual(1.0, sum(complex_weights.values()), places=6)
        self.assertNotEqual(simple_weights, complex_weights)
        self.assertGreater(
            complex_weights["intelligence"],
            simple_weights["intelligence"],
        )

    def test_manifest_separates_invariants_from_dynamic_variables(self) -> None:
        manifest = constitution_manifest()
        self.assertIn("external-tools-forbidden", manifest["hard_invariants"])
        self.assertIn("objective-weights", manifest["dynamic_variables"])
        self.assertTrue(manifest["safety_constants_are_not_business_defaults"])
        self.assertFalse(manifest["cross_task_history_used"])


if __name__ == "__main__":
    unittest.main()
