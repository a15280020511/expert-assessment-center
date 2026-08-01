from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import model_market  # noqa: E402
import resource_matrix  # noqa: E402
import v5_general_task_planning  # noqa: E402
import v5_task_delivery_contract as contracts  # noqa: E402

# Compatibility fixture for older load tests. It is intentionally domain-neutral
# and must never be imported by production or release workflows.
FAILED_PRODUCTION_TASK = (
    "比较方案A与方案B的成本、风险和实施条件。严格使用以下3个Markdown二级标题，"
    "顺序不得改变，每项不得为空：\n"
    "1）已知条件与未知项\n"
    "2）比较结果与决策阈值\n"
    "3）风险、反证与下一步"
)


class V5ConstitutionalSemanticsTests(unittest.TestCase):
    @staticmethod
    def run_config(task: str) -> SimpleNamespace:
        return SimpleNamespace(
            task=task,
            minimum_context_length=16_384,
            max_completion_tokens=10_000,
        )

    def compile(self, task: str):
        run = self.run_config(task)
        profile = v5_general_task_planning.classify_task(task, run)
        bundle = resource_matrix.compile_v5_task_resources(
            profile,
            run,
            semantic_compiler=v5_general_task_planning.compile_task_semantics,
        )
        return profile, bundle

    def test_no_named_scenario_can_select_a_production_architecture(self) -> None:
        tasks = (
            "比较两个采购方案并给出结论。",
            "分析一段合同并列出风险。",
            "评估一个公共项目并进行反证。",
            "设计一项实验并说明验证标准。",
        )
        for task in tasks:
            with self.subTest(task=task):
                _, bundle = self.compile(task)
                signals = bundle["task_semantics"]["task_signals"]
                self.assertFalse(signals["task_specific_production_branching"])
                self.assertFalse(signals["case_derived_compaction_applied"])
                self.assertEqual(
                    "generic-semantic-matrix-only",
                    signals["architecture_selection_policy"],
                )
                self.assertEqual(
                    "task-independent-semantic-compilation",
                    bundle["task_semantics"]["architecture"],
                )

    def test_complexity_is_monotone_when_requirements_are_added(self) -> None:
        base = "比较方案A和方案B。"
        expanded = (
            base
            + "\n1）计算成本。\n2）分析风险。\n3）给出决策阈值。"
            + "\n4）独立红队反证。\n5）复核关键假设。\n6）给出实施步骤。"
        )
        base_profile, _ = self.compile(base)
        expanded_profile, _ = self.compile(expanded)
        self.assertGreaterEqual(
            expanded_profile.complexity_score,
            base_profile.complexity_score,
        )

    def test_explicit_markdown_contract_survives_wording_variants(self) -> None:
        variants = (
            "严格使用以下3个Markdown二级标题，顺序不得改变：\n1）甲\n2）乙\n3）丙",
            "请按照下列3个H2标题输出：\n1. 甲\n2. 乙\n3. 丙",
            "Use the following 3 level-2 headings in order:\n1) A\n2) B\n3) C",
        )
        for task in variants:
            with self.subTest(task=task):
                contract = contracts.extract_explicit_markdown_contract(task)
                self.assertTrue(contract["explicit_markdown_contract"])
                self.assertEqual(3, len(contract["exact_markdown_headings"]))
                self.assertTrue(contract["markdown_heading_order_required"])

    def test_fourteen_heading_contract_is_not_lost(self) -> None:
        headings = [f"章节{i}" for i in range(1, 15)]
        task = (
            "严格使用以下14个 Markdown 二级标题，顺序不得改变，每项不得为空：\n"
            + "\n".join(f"{index}）{heading}" for index, heading in enumerate(headings, 1))
        )
        contract = contracts.extract_explicit_markdown_contract(task)
        self.assertEqual(headings, contract["exact_markdown_headings"])
        self.assertEqual(14, contract["task_explicit_delivery_section_count"])

    def test_deprecated_install_is_a_noop(self) -> None:
        before = model_market.classify_task
        self.assertIsNone(v5_general_task_planning.install())
        self.assertIs(before, model_market.classify_task)


if __name__ == "__main__":
    unittest.main()
