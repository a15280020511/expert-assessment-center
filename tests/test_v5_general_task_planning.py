import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import resource_matrix  # noqa: E402
import v5_general_task_planning  # noqa: E402


class V5GeneralTaskPlanningTests(unittest.TestCase):
    @staticmethod
    def run_config(task: str):
        return SimpleNamespace(
            task=task,
            minimum_context_length=16_384,
            max_completion_tokens=3_000,
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

    def test_consumer_decision_uses_generic_compiler(self):
        task = (
            "比较两个通信方案的成本、覆盖量和切换风险，计算12个月总成本、"
            "盈亏平衡点，并给出30天试用方案。"
        )
        profile, bundle = self.compile(task)
        self.assertFalse(profile.high_stakes)
        semantics = bundle["task_semantics"]
        self.assertEqual(
            "task-independent-semantic-compilation",
            semantics["architecture"],
        )
        self.assertFalse(
            semantics["task_signals"]["task_specific_production_branching"]
        )
        self.assertFalse(
            semantics["task_signals"]["case_derived_compaction_applied"]
        )
        self.assertGreaterEqual(len(semantics["interpretations"]), 1)

    def test_added_requirements_never_reduce_structural_complexity(self):
        short = "比较两个方案。"
        long = (
            short
            + "\n1）计算成本。\n2）分析风险。\n3）给出阈值。"
            + "\n4）独立复核。\n5）红队反证。\n6）制定实施步骤。"
        )
        short_profile, _ = self.compile(short)
        long_profile, _ = self.compile(long)
        self.assertGreaterEqual(
            long_profile.complexity_score,
            short_profile.complexity_score,
        )

    def test_generic_report_word_does_not_force_long_context(self):
        task = "比较两个月度套餐的成本并输出简洁报告。"
        run = self.run_config(task)
        profile = v5_general_task_planning.classify_task(task, run)
        self.assertFalse(profile.long_context)
        self.assertLess(profile.requested_context, 65_536)

    def test_explicit_full_repository_audit_remains_long_context(self):
        task = "请对整个代码库逐行审计，检查安全漏洞和合规问题。"
        run = self.run_config(task)
        profile = v5_general_task_planning.classify_task(task, run)
        self.assertTrue(profile.long_context)
        self.assertTrue(profile.high_stakes)
        self.assertGreaterEqual(profile.requested_context, 65_536)

    def test_medical_decision_remains_high_stakes_without_special_architecture(self):
        task = "比较两种临床治疗方案，核验医学证据并评估用药风险。"
        profile, bundle = self.compile(task)
        self.assertTrue(profile.high_stakes)
        signals = bundle["task_semantics"]["task_signals"]
        self.assertFalse(signals["task_specific_production_branching"])
        self.assertEqual(
            "generic-semantic-matrix-only",
            signals["architecture_selection_policy"],
        )

    def test_explicit_contract_breadth_is_recorded_not_forced_to_one_work(self):
        headings = [f"部分{i}" for i in range(1, 9)]
        task = (
            "严格使用以下8个Markdown二级标题，顺序不得改变，每项不得为空：\n"
            + "\n".join(
                f"{index}）{heading}"
                for index, heading in enumerate(headings, 1)
            )
        )
        profile, bundle = self.compile(task)
        signals = bundle["task_semantics"]["task_signals"]
        structural = signals["structural_signals"]
        self.assertEqual(8, structural["explicit_contract_items"])
        self.assertTrue(structural["explicit_output_contract"])
        self.assertFalse(signals["case_derived_compaction_applied"])
        self.assertGreaterEqual(profile.complexity_score, 3)


def explicit_suite() -> unittest.TestSuite:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(
        V5GeneralTaskPlanningTests
    )
    if suite.countTestCases() != 6:
        raise RuntimeError(
            f"classification regression suite count mismatch: {suite.countTestCases()}"
        )
    return suite


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(explicit_suite())
    raise SystemExit(0 if result.wasSuccessful() else 1)
