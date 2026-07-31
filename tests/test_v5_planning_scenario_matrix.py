import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import model_market  # noqa: E402
import resource_matrix  # noqa: E402
import v5_general_task_planning  # noqa: E402


class V5PlanningScenarioMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        v5_general_task_planning.install()

    @staticmethod
    def run(task: str):
        return SimpleNamespace(
            task=task,
            minimum_context_length=16_384,
            max_completion_tokens=3_000,
        )

    def classify_and_compile(self, task: str):
        run = self.run(task)
        profile = model_market.classify_task(task, run)
        bundle = resource_matrix.compile_v5_task_resources(profile, run)
        self.assertTrue(bundle["atomic_work_graphs"]["all_graphs_are_dag"])
        self.assertTrue(bundle["task_semantics"]["interpretations"])
        return profile, bundle

    def test_non_regulated_numeric_decisions_compact(self):
        tasks = [
            "比较两个手机套餐：A每月39元，B每月20元另加99元设备费，计算12个月成本和盈亏平衡。",
            "比较夜班保安和网约车，计算月净收入、工时收入、三年收入和悲观基准乐观情景。",
            "一家小店比较购买设备和租赁设备，给定价格、维修费和使用年限，计算总成本并建议。",
            "家庭比较提前还贷和保留现金，按给定利率和金额计算三年现金流与触发门槛。",
            "比较两条配送路线的里程、时间、油费和失败概率，做敏感性分析后给出选择。",
        ]
        for task in tasks:
            with self.subTest(task=task):
                profile, bundle = self.classify_and_compile(task)
                self.assertFalse(profile.high_stakes)
                self.assertFalse(profile.long_context)
                semantics = bundle["task_semantics"]
                self.assertTrue(
                    semantics["task_signals"]["cost_performance_compaction_applied"]
                )
                self.assertEqual(len(semantics["interpretations"]), 1)
                works = semantics["interpretations"][0]["atomic_work"]
                self.assertEqual(len(works), 1)
                self.assertEqual(
                    works[0]["independence_requirements"][
                        "minimum_independent_copies"
                    ],
                    1,
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
                self.assertFalse(
                    bundle["task_semantics"]["task_signals"].get(
                        "cost_performance_compaction_applied", False
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

    def test_explicit_external_evidence_is_preserved_without_false_duplication(self):
        task = (
            "比较两个投资方案，按给定数字计算回报，并核验来源数据和证据可靠性，"
            "区分已验证事实与未知事项。"
        )
        profile, bundle = self.classify_and_compile(task)
        self.assertFalse(profile.high_stakes)
        work = bundle["task_semantics"]["interpretations"][0]["atomic_work"][0]
        self.assertIn("evidence_validation", work["operation_requirements"])
        self.assertIn("research", work["domain_requirements"])
        self.assertEqual(
            work["independence_requirements"]["minimum_independent_copies"], 1
        )


if __name__ == "__main__":
    unittest.main()
