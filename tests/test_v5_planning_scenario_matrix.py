import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import resource_matrix  # noqa: E402
import v5_general_task_planning  # noqa: E402


class V5PlanningScenarioMatrixTests(unittest.TestCase):
    @staticmethod
    def run_config(task: str):
        return SimpleNamespace(
            task=task,
            minimum_context_length=16_384,
            max_completion_tokens=3_000,
        )

    def classify_and_compile(self, task: str):
        run = self.run_config(task)
        profile = v5_general_task_planning.classify_task(task, run)
        bundle = resource_matrix.compile_v5_task_resources(
            profile,
            run,
            semantic_compiler=v5_general_task_planning.compile_task_semantics,
        )
        self.assertTrue(bundle["atomic_work_graphs"]["all_graphs_are_dag"])
        self.assertTrue(bundle["task_semantics"]["interpretations"])
        self.assertTrue(
            bundle["semantic_input_policy"][
                "semantic_compiler_injected_explicitly"
            ]
        )
        signals = bundle["task_semantics"]["task_signals"]
        self.assertFalse(signals["task_specific_production_branching"])
        self.assertFalse(signals["case_derived_compaction_applied"])
        self.assertEqual(
            signals["architecture_selection_policy"],
            "generic-semantic-matrix-only",
        )
        return profile, bundle

    @staticmethod
    def all_works(bundle):
        return [
            work
            for interpretation in bundle["task_semantics"]["interpretations"]
            for work in interpretation["atomic_work"]
        ]

    def test_non_regulated_numeric_decisions_use_generic_dynamic_decomposition(self):
        tasks = [
            "比较两个手机套餐：A每月39元，B每月20元另加99元设备费，计算12个月成本和盈亏平衡。",
            "比较夜班保安和网约车，计算月净收入、工时收入、三年收入和悲观基准乐观情景。",
            "一家小店比较购买设备和租赁设备，给定价格、维修费和使用年限，计算总成本并建议。",
            "家庭比较提前还贷和保留现金，按给定利率和金额计算三年现金流与触发门槛。",
            "比较两条配送路线的里程、时间、油费和失败概率，做敏感性分析后给出选择。",
        ]
        interpretation_counts = []
        work_counts = []
        for task in tasks:
            with self.subTest(task=task):
                profile, bundle = self.classify_and_compile(task)
                self.assertFalse(profile.high_stakes)
                self.assertFalse(profile.long_context)
                interpretations = bundle["task_semantics"]["interpretations"]
                self.assertGreaterEqual(len(interpretations), 1)
                interpretation_counts.append(len(interpretations))
                for interpretation in interpretations:
                    self.assertNotEqual(
                        interpretation.get("strategy"),
                        "cost_performance_compact_decision",
                    )
                    works = interpretation["atomic_work"]
                    self.assertGreaterEqual(len(works), 1)
                    work_counts.append(len(works))
                    for work in works:
                        self.assertGreaterEqual(
                            work["independence_requirements"][
                                "minimum_independent_copies"
                            ],
                            1,
                        )
        self.assertTrue(
            any(count > 1 for count in interpretation_counts)
            or any(count > 1 for count in work_counts)
        )

    def test_regulated_and_safety_critical_tasks_remain_strict(self):
        tasks = [
            "比较两种临床治疗方案，核验医学证据、药物副作用和患者安全风险。",
            "分析合同争议和潜在诉讼，比较法律方案并评估监管合规后果。",
            "审计网络安全架构，寻找漏洞利用、数据泄露和攻击路径。",
            "评估战争升级与制裁方案，分析外交危机和军事风险。",
        ]
        for task in tasks:
            with self.subTest(task=task):
                profile, bundle = self.classify_and_compile(task)
                self.assertTrue(profile.high_stakes)
                signals = bundle["task_semantics"]["task_signals"]
                self.assertFalse(signals["case_derived_compaction_applied"])
                works = self.all_works(bundle)
                self.assertTrue(
                    any(
                        work["independence_requirements"][
                            "minimum_independent_copies"
                        ]
                        >= 2
                        or "risk_discovery"
                        in work["operation_requirements"]
                        for work in works
                    )
                )

    def test_long_context_requires_explicit_scope(self):
        short_report = "比较两个方案并输出风险报告。"
        short_profile, _ = self.classify_and_compile(short_report)
        self.assertFalse(short_profile.long_context)

        full_scope = "对整个仓库逐行审计，分析完整代码库中的安全漏洞。"
        full_profile, _ = self.classify_and_compile(full_scope)
        self.assertTrue(full_profile.long_context)
        self.assertGreaterEqual(full_profile.requested_context, 65_536)

    def test_explicit_evidence_requirement_is_preserved_without_fixed_layout(self):
        task = (
            "比较两个投资方案，按给定数字计算回报，并核验来源数据和证据可靠性，"
            "区分已验证事实与未知事项。"
        )
        profile, bundle = self.classify_and_compile(task)
        self.assertFalse(profile.high_stakes)
        works = self.all_works(bundle)
        self.assertTrue(
            any(
                "evidence_validation" in work["operation_requirements"]
                for work in works
            )
        )
        self.assertTrue(
            any("research" in work["domain_requirements"] for work in works)
        )
        self.assertFalse(
            bundle["task_semantics"]["task_signals"][
                "case_derived_compaction_applied"
            ]
        )


def explicit_suite() -> unittest.TestSuite:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(
        V5PlanningScenarioMatrixTests
    )
    if suite.countTestCases() != 4:
        raise RuntimeError(
            f"scenario-matrix regression suite count mismatch: {suite.countTestCases()}"
        )
    return suite


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(explicit_suite())
    raise SystemExit(0 if result.wasSuccessful() else 1)
